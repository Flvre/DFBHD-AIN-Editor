"""Generator worker/process infrastructure.

Manages the persistent low-priority subprocess that runs generator jobs
without starving the Tk UI thread.  Contains CLI entry points, job
serialization, progress event protocol, and the process manager.

No import of app.py — geometry is accessed through generator.geometry.
Facade globals (_init_game_path, _pff_index_cache, etc.) are resolved
at call time through runtime_namespace.
"""

import os, sys, time, json, math, pickle, tempfile, subprocess, atexit
import threading, traceback
from pathlib import Path
from collections import defaultdict, Counter

import runtime_namespace as _ns

from format.ain_format import Node, MAX_NEIGHBORS
from format.bms_parse import parse_bms, _detect_bms_terrain_name
from terrain.terrain_load import _guess_mis_for_bms, _load_terrain_backdrop_from_mis
from terrain.terrain_water import _attach_bms_water_level, _terrain_navigable_height
from terrain.terrain import _terrain_sample_height, _terrain_water_height_world
from shared.nav_helpers import (interp_z, build_ground_z_ref,
    GENERATOR_IGNORE_DESTROYABLE)
from generator.support import (LabNode, _generator_node_record,
    _crop_generator_nodes_for_job,
    GENERATOR_REACHABLE_MAX_STEP, GENERATOR_REACHABLE_STAIR_STEP)
from generator.core import (generate_seed_disc_campaign_v58,
    _build_generator_reachable_surface,
    _build_generator_terrain_surface_contract)
from generator.geometry import collect_generator_geometry, add_old_new_seams
from generator.zones import execute_tile_zone_job


def _bms_load_trace(message):
    return None


def _configure_generator_worker_thread():
    """Keep generator work below the UI and foreground applications on Windows."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -1)
    except Exception:
        pass


def _configure_generator_worker_process():
    """Run the isolated generator below interactive work and with Windows EcoQoS."""
    _configure_generator_worker_thread()
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        process = kernel32.GetCurrentProcess()
        kernel32.SetPriorityClass(process, 0x00004000)

        class _PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
            _fields_ = [
                ('Version', wintypes.DWORD),
                ('ControlMask', wintypes.DWORD),
                ('StateMask', wintypes.DWORD),
            ]

        state = _PROCESS_POWER_THROTTLING_STATE(1, 1, 1)
        kernel32.SetProcessInformation(
            process, 4, ctypes.byref(state), ctypes.sizeof(state))
    except Exception:
        pass


_GENERATOR_PROCESS_EVENT_PREFIX = 'DFBHD_AIN_GENERATOR_EVENT\t'
_GENERATOR_WORKER_MAP_CACHE = {}
_GENERATOR_PROCESS_EVENT_STATE = None


def _reset_generator_process_event_state():
    """Start a fresh coalesced progress stream for one worker request."""
    global _GENERATOR_PROCESS_EVENT_STATE
    _GENERATOR_PROCESS_EVENT_STATE = {
        'phase_name': None,
        'phase_at': 0.0,
        'phase_percent': -1.0,
        'status_text': None,
        'status_at': 0.0,
    }


def _emit_generator_process_event(kind, *payload, force=False):
    """Write one progress event for the parent without mixing it with logs."""
    global _GENERATOR_PROCESS_EVENT_STATE
    try:
        if _GENERATOR_PROCESS_EVENT_STATE is None:
            _reset_generator_process_event_state()
        state = _GENERATOR_PROCESS_EVENT_STATE
        now = time.perf_counter()
        if not force and kind == 'phase' and len(payload) >= 2:
            percent = float(payload[0])
            phase_name = str(payload[1])
            phase_changed = phase_name != state['phase_name']
            completed = percent >= 99.999
            if (not phase_changed and not completed and
                    now - state['phase_at'] < 0.125):
                return False
            if (not phase_changed and not completed and
                    abs(percent - state['phase_percent']) < 0.01):
                return False
            state['phase_name'] = phase_name
            state['phase_at'] = now
            state['phase_percent'] = percent
        elif not force and kind == 'status' and payload:
            status_text = str(payload[0])
            if status_text == state['status_text']:
                return False
            if now - state['status_at'] < 0.25:
                return False
            state['status_text'] = status_text
            state['status_at'] = now
        packet = json.dumps([kind, *payload], separators=(',', ':'))
        sys.stdout.write(_GENERATOR_PROCESS_EVENT_PREFIX + packet + '\n')
        sys.stdout.flush()
        return True
    except Exception:
        return False


def _load_generator_worker_terrain(bms_path, log=None):
    """Load the same automatic terrain source used by the editor, headlessly."""
    log = log or (lambda _message: None)
    _init_game_path = _ns.get('_init_game_path', None)
    _pff_index_cache = _ns.get('_pff_index_cache', {})
    mis_path = _guess_mis_for_bms(bms_path)
    game_dir = (_init_game_path(bms_path) if _init_game_path else None) or Path(bms_path).parent
    if mis_path:
        info = _load_terrain_backdrop_from_mis(mis_path, game_dir, pff_cache=_pff_index_cache)
        info = _attach_bms_water_level(info, bms_path)
        if isinstance(info, dict) and info.get('ok'):
            return info
    terrain_name, _source = _detect_bms_terrain_name(
        bms_path, game_dir, log=log,
        init_game_path=_init_game_path or (lambda p=None: None),
        pff_cache=_pff_index_cache)
    if terrain_name:
        info = _load_terrain_backdrop_from_mis(
            None, game_dir, terrain_name_override=terrain_name,
            source_label=f'BMS {Path(bms_path).name}', pff_cache=_pff_index_cache)
        return _attach_bms_water_level(info, bms_path)
    return None


def _prepare_generator_worker_map(job):
    """Build and cache map movement geometry entirely outside the Tk process."""
    _init_game_path = _ns.get('_init_game_path', None)
    _pff_index_cache = _ns.get('_pff_index_cache', {})

    bms_path = os.path.abspath(str(job.get('bms_path') or ''))
    if not bms_path or not os.path.isfile(bms_path):
        raise RuntimeError(f'Generator worker cannot open BMS: {bms_path!r}')
    try:
        stat = os.stat(bms_path)
        file_token = (int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        file_token = (0, 0)
    cache_key = (
        os.path.normcase(bms_path), file_token,
        bool(job.get('z_filter_enabled')),
        bool(job.get('ignore_destroyable_objects', True)),
        bool(job.get('ignore_vehicles', False)),
        bool(_ns.get('GENERATOR_IGNORE_NON_COLLISION_FOLIAGE', True)),
        bool(_ns.get('FOLIAGE_TREES_ONLY', True)),
    )
    cached = _GENERATOR_WORKER_MAP_CACHE.get(cache_key)
    if cached is not None:
        _emit_generator_process_event(
            'status', 'Generate AIN: reused worker-owned movement geometry cache')
        return cached, True

    _emit_generator_process_event(
        'phase', 3.0, 'Collecting movement geometry',
        'Worker is loading the map and CModel collision data')
    entities, terrain_z = parse_bms(
        bms_path,
        log=lambda message: _emit_generator_process_event('status', str(message)),
        init_game_path=_init_game_path or (lambda p=None: None),
        pff_cache=_pff_index_cache,
        trace=_bms_load_trace)
    terrain_info = _load_generator_worker_terrain(
        bms_path,
        log=lambda message: _emit_generator_process_event('status', str(message)))

    preload_fn = _ns.get('preload_cmodels_for_entities', None)
    if preload_fn is not None:
        preload = preload_fn(
            entities, bms_path=bms_path,
            log=lambda message: _emit_generator_process_event('status', str(message)))
        if preload is not None:
            preload.join()

    game_path = _ns.get('_game_path', None)
    r = collect_generator_geometry(
        entities=entities,
        terrain_z=float(terrain_z),
        terrain_info=terrain_info,
        game_path=game_path,
        pff_cache=_pff_index_cache,
        bms_path=bms_path,
        terrain_mis_path=str(
            (terrain_info or {}).get('mis_path', '')
            if isinstance(terrain_info, dict) else ''),
        z_filter_enabled=bool(job.get('z_filter_enabled')),
        ignore_destroyable=bool(job.get('ignore_destroyable_objects', True)),
        ignore_vehicles=bool(job.get('ignore_vehicles', False)),
        cache=None,
        on_progress=lambda msg: _emit_generator_process_event('status', str(msg)),
    )
    world_segments = r['world_segments']
    entities_core = r['entities_core']
    stats = r['stats']

    payload = {
        'world_segments': world_segments,
        'entities_core': entities_core,
        'entities_snapshot': entities,
        'terrain_z': float(terrain_z),
        'terrain_info': terrain_info,
        'water_height': _terrain_water_height_world(terrain_info),
        'support_triangles': r['support_triangles'],
        'support_triangle_metadata': r['support_triangle_metadata'],
        'collision_segments_3d': r['collision_segments_3d'],
        'collision_triangles_3d': r['collision_triangles_3d'],
        'geometry_stats': stats,
    }
    map_token = cache_key[:2]
    if any(key[:2] != map_token for key in _GENERATOR_WORKER_MAP_CACHE):
        _GENERATOR_WORKER_MAP_CACHE.clear()
    _GENERATOR_WORKER_MAP_CACHE[cache_key] = payload
    while len(_GENERATOR_WORKER_MAP_CACHE) > 8:
        _GENERATOR_WORKER_MAP_CACHE.pop(next(iter(_GENERATOR_WORKER_MAP_CACHE)))
    return payload, False


def _hydrate_generator_process_job(job):
    """Attach worker-owned map geometry when the parent sent a lightweight job."""
    if job.get('world_segments') is not None:
        return job
    payload, cache_hit = _prepare_generator_worker_map(job)
    hydrated = dict(job)
    hydrated.update(payload)
    hydrated['worker_geometry_cache_hit'] = bool(cache_hit)
    return hydrated


def _execute_generator_process_job(job):
    """Execute the exact embedded core in a process that cannot starve Tk."""
    if job.get('job_kind') == 'tile_zone':
        return execute_tile_zone_job(job)
    _configure_generator_worker_process()
    hydrate_started = time.perf_counter()
    job = _hydrate_generator_process_job(job)
    hydrate_elapsed = time.perf_counter() - hydrate_started
    terrain_info = job.get('terrain_info')
    terrain_z = float(job.get('terrain_z', 18.5))
    entities_snapshot = list(job.get('entities_snapshot') or [])
    water_height = job.get('water_height')
    z_ref = build_ground_z_ref(entities_snapshot, terrain_z) if entities_snapshot else []

    def surface_z(x, y):
        if terrain_info is not None:
            try:
                height = _terrain_navigable_height(terrain_info, x, y)
                if height is not None:
                    return float(height)
            except Exception:
                pass
            try:
                raw_height = _terrain_sample_height(terrain_info, x, y)
                if (raw_height is not None and water_height is not None
                        and float(raw_height) < float(water_height) - 0.05):
                    return None
            except Exception:
                pass
        return interp_z(x, y, z_ref, terrain_z)

    center = tuple(job['center'])
    radius = float(job['radius'])
    generator_surface_z = surface_z
    support_metrics = {'enabled': False}
    if bool(job.get('z_filter_enabled')):
        _emit_generator_process_event(
            'phase', 12.0, 'Building reachable-surface filter',
            f'Sampling terrain and support surfaces at 0.25 m cells; maximum step {GENERATOR_REACHABLE_MAX_STEP:.2f} m')
        generator_surface_z, support_metrics = _build_generator_reachable_surface(
            center,
            radius,
            surface_z,
            job.get('support_triangles') or [],
            support_triangle_metadata=job.get('support_triangle_metadata') or [],
            collision_segments_3d=job.get('collision_segments_3d') or [],
            collision_triangles_3d=job.get('collision_triangles_3d') or [],
            max_step=GENERATOR_REACHABLE_MAX_STEP,
            cell=0.25,
            include_rooftops=bool(job.get('rooftops_enabled')),
            highest_broad_rooftops_only=bool(
                job.get('highest_broad_rooftops_only')),
            seed_support_z=job.get('target_support_z'))
        if generator_surface_z is None:
            return {
                'no_reachable_surface': True,
                'nodes': [],
                'metrics': {'automatic_z_filter': support_metrics},
            }
    else:
        generator_surface_z, support_metrics = (
            _build_generator_terrain_surface_contract(
                center,
                radius,
                surface_z,
                collision_triangles_3d=(
                    job.get('collision_triangles_3d') or []),
                cell=0.50))

    existing_nodes = [Node.from_dict(record)
                      for record in list(job.get('existing_nodes') or [])]

    _emit_generator_process_event(
        'phase', 22.0, 'Starting navigation grower',
        'Entering the instrumented node-generation core')

    def core_progress(percent, phase, detail):
        overall = 22.0 + max(0.0, min(100.0, float(percent))) * 0.70
        _emit_generator_process_event('phase', overall, phase, detail)

    result = generate_seed_disc_campaign_v58(
        job.get('world_segments') or [],
        job.get('entities_core') or [],
        terrain_z,
        center=center,
        focus_radius=radius,
        existing_nodes=existing_nodes,
        log=lambda message: _emit_generator_process_event('status', str(message)),
        z_at=generator_surface_z,
        collision_segments_3d=job.get('collision_segments_3d') or [],
        collision_triangles_3d=job.get('collision_triangles_3d') or [],
        on_progress=core_progress,
        quantize_to_map_grid=bool(job.get('quantized_enabled')),
        geometry_pre_normalized=True,
        include_ladders=bool(
            job.get('z_filter_enabled')
            and job.get('rooftops_enabled')
            and job.get('ladders_enabled')))
    result.setdefault('metrics', {})['automatic_z_filter'] = support_metrics
    result['metrics']['worker_geometry_cache_hit'] = bool(
        job.get('worker_geometry_cache_hit', False))
    result['metrics']['worker_geometry_time_sec'] = round(hydrate_elapsed, 3)
    if job.get('geometry_stats') is not None:
        result['metrics']['geometry_stats'] = job.get('geometry_stats')
    cropped_nodes, generated_before_crop, generated_after_crop = (
        _crop_generator_nodes_for_job(result.get('nodes') or [], job))
    result['nodes'] = cropped_nodes
    if job.get('crop_polygon') or job.get('crop_tiles'):
        result['metrics']['zone_generated_before_crop'] = int(
            generated_before_crop)
        result['metrics']['zone_generated_after_crop'] = int(
            generated_after_crop)
        _emit_generator_process_event(
            'phase', 93.0, 'Cropping generated graph',
            f'Kept {generated_after_crop}/{generated_before_crop} nodes inside the zone')
    generated_records = [_generator_node_record(node)
                         for node in list(result.get('nodes') or [])]
    generated_count = len(generated_records)
    offset = len(existing_nodes)
    batch_nodes = []
    for local_index, record in enumerate(generated_records):
        local_neighbors = []
        for neighbor in list(record.get('neighbors') or []):
            try:
                neighbor = int(neighbor)
            except Exception:
                continue
            if 0 <= neighbor < generated_count:
                local_neighbors.append(offset + neighbor)
        batch_nodes.append(Node(
            offset + local_index,
            float(record.get('x', 0.0)),
            float(record.get('y', 0.0)),
            float(record.get('z', 0.0)),
            b12=int(record.get('b12', 0)), b13=0, b14=0,
            b15=int(record.get('b15', 36)),
            b16=int(record.get('b16', 0)),
            b17=int(record.get('b17', 0)),
            b18=int(record.get('b18', 0)),
            neighbors=local_neighbors[:MAX_NEIGHBORS],
            quantized_source_keys=record.get('quantized_source_keys'),
            quantized_map_grid=bool(record.get('quantized_map_grid', False))))
    seam_pairs = []
    old_new_links = 0
    if existing_nodes and batch_nodes:
        old_new_links = add_old_new_seams(
            existing_nodes, batch_nodes, offset,
            job.get('world_segments') or [],
            job.get('collision_triangles_3d') or [],
            quantized_topology=bool(job.get('quantized_enabled')))
        _existing_by_id = {
            int(node.id): node for node in existing_nodes
        }
        for _old_id, _local_index in list(
                (result.get('metrics') or {}).get(
                    'ladder_old_new_seams') or ()):
            try:
                _old_id = int(_old_id)
                _local_index = int(_local_index)
            except Exception:
                continue
            if not (0 <= _local_index < len(batch_nodes)):
                continue
            _old_node = _existing_by_id.get(_old_id)
            if _old_node is None:
                continue
            _new_node = batch_nodes[_local_index]
            _new_id = offset + _local_index
            if (_new_id not in (_old_node.neighbors or [])
                    and len(_old_node.neighbors or []) < MAX_NEIGHBORS):
                _old_node.neighbors.append(_new_id)
            if (_old_id not in (_new_node.neighbors or [])
                    and len(_new_node.neighbors or []) < MAX_NEIGHBORS):
                _new_node.neighbors.append(_old_id)
            if (_new_id in (_old_node.neighbors or [])
                    and _old_id in (_new_node.neighbors or [])):
                old_new_links += 1
        for local_index, node in enumerate(batch_nodes):
            for neighbor in list(node.neighbors or []):
                if 0 <= int(neighbor) < offset:
                    seam_pairs.append((int(neighbor), int(local_index)))
    result['metrics']['old_new_links'] = int(old_new_links)
    result['old_new_seams'] = seam_pairs
    result['nodes'] = generated_records
    return result


def _generator_worker_process_cli(job_path, result_path):
    """Private command-line entry point used by the editor's worker thread."""
    envelope = None
    try:
        with open(job_path, 'rb') as stream:
            job = pickle.load(stream)
        envelope = {'ok': True, 'result': _execute_generator_process_job(job)}
    except Exception as exc:
        envelope = {
            'ok': False,
            'error': str(exc),
            'traceback': traceback.format_exc(),
        }
    try:
        with open(result_path, 'wb') as stream:
            pickle.dump(envelope, stream, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        return 3
    return 0 if envelope.get('ok') else 2


def _generator_worker_server_cli():
    """Serve exact generator requests while retaining parsed map/CModel caches."""
    _configure_generator_worker_process()
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
        except Exception:
            continue
        if request.get('command') == 'shutdown':
            return 0
        job_path = str(request.get('job_path') or '')
        result_path = str(request.get('result_path') or '')
        request_id = str(request.get('request_id') or '')
        envelope = None
        try:
            _reset_generator_process_event_state()
            with open(job_path, 'rb') as stream:
                job = pickle.load(stream)
            envelope = {'ok': True, 'result': _execute_generator_process_job(job)}
        except Exception as exc:
            envelope = {
                'ok': False,
                'error': str(exc),
                'traceback': traceback.format_exc(),
            }
        try:
            with open(result_path, 'wb') as stream:
                pickle.dump(envelope, stream, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            envelope = {'ok': False, 'error': str(exc),
                        'traceback': traceback.format_exc()}
        _emit_generator_process_event('request_done', request_id, force=True)
    return 0


class _PersistentGeneratorProcess:
    """One low-power child that owns all heavyweight generator map state."""

    def __init__(self):
        self._process = None
        self._temp = tempfile.TemporaryDirectory(
            prefix='dfbhd_ain_generator_server_')
        self._counter = 0
        self._lock = threading.Lock()

    def _start(self):
        if self._process is not None and self._process.poll() is None:
            return
        facade_path = _ns.get('__file__', None)
        if facade_path is None:
            facade_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), '..', 'ain_editor_v1_0.py')
        if getattr(sys, 'frozen', False):
            command = [sys.executable, '--generator-worker-server']
            worker_cwd = os.path.dirname(os.path.abspath(sys.executable))
        else:
            command = [sys.executable, os.path.abspath(facade_path),
                       '--generator-worker-server']
            worker_cwd = os.path.dirname(os.path.abspath(facade_path))
        creationflags = 0
        if os.name == 'nt':
            creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            creationflags=creationflags,
            cwd=worker_cwd)

    def run(self, job, on_event=None):
        with self._lock:
            self._start()
            self._counter += 1
            request_id = str(self._counter)
            job_path = os.path.join(self._temp.name, f'job_{request_id}.pickle')
            result_path = os.path.join(self._temp.name, f'result_{request_id}.pickle')
            with open(job_path, 'wb') as stream:
                pickle.dump(job, stream, protocol=pickle.HIGHEST_PROTOCOL)
            request = json.dumps({
                'request_id': request_id,
                'job_path': job_path,
                'result_path': result_path,
            }, separators=(',', ':'))
            try:
                self._process.stdin.write(request + '\n')
                self._process.stdin.flush()
            except Exception as exc:
                raise RuntimeError('Generator worker server could not accept the request') from exc

            worker_output = []
            completed = False
            while self._process.poll() is None:
                line = self._process.stdout.readline()
                if not line:
                    continue
                line = line.rstrip('\r\n')
                if line.startswith(_GENERATOR_PROCESS_EVENT_PREFIX):
                    try:
                        event = tuple(json.loads(
                            line[len(_GENERATOR_PROCESS_EVENT_PREFIX):]))
                    except Exception:
                        continue
                    if (event and event[0] == 'request_done'
                            and len(event) > 1 and str(event[1]) == request_id):
                        completed = True
                        break
                    if on_event is not None:
                        on_event(event)
                elif line:
                    worker_output.append(line)

            try:
                os.unlink(job_path)
            except Exception:
                pass
            if not completed or not os.path.isfile(result_path):
                detail = '\n'.join(worker_output[-40:])
                return_code = self._process.poll()
                raise RuntimeError(
                    f'Generator worker server stopped without a result (exit {return_code}).'
                    + (f'\n{detail}' if detail else ''))
            try:
                with open(result_path, 'rb') as stream:
                    envelope = pickle.load(stream)
            finally:
                try:
                    os.unlink(result_path)
                except Exception:
                    pass
            if not envelope.get('ok'):
                raise RuntimeError(
                    str(envelope.get('error') or 'Generator worker failed') + '\n'
                    + str(envelope.get('traceback') or ''))
            return envelope['result']

    def close(self):
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            try:
                process.stdin.write('{"command":"shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=2.0)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
        try:
            self._temp.cleanup()
        except Exception:
            pass


_GENERATOR_PROCESS_MANAGER = None
_GENERATOR_PROCESS_MANAGER_LOCK = threading.Lock()


def _close_generator_process_manager():
    global _GENERATOR_PROCESS_MANAGER
    manager = _GENERATOR_PROCESS_MANAGER
    _GENERATOR_PROCESS_MANAGER = None
    _ns.set_val('_GENERATOR_PROCESS_MANAGER', None)
    if manager is not None:
        manager.close()


atexit.register(_close_generator_process_manager)


def _run_generator_process(job, on_event=None):
    """Run in the persistent low-power process that owns map geometry caches."""
    global _GENERATOR_PROCESS_MANAGER
    with _GENERATOR_PROCESS_MANAGER_LOCK:
        if _GENERATOR_PROCESS_MANAGER is None:
            _GENERATOR_PROCESS_MANAGER = _PersistentGeneratorProcess()
            _ns.set_val('_GENERATOR_PROCESS_MANAGER', _GENERATOR_PROCESS_MANAGER)
        manager = _GENERATOR_PROCESS_MANAGER
    return manager.run(job, on_event=on_event)


def _generator_cooperative_yield(state, interval=0.050):
    """Release the GIL at a bounded cadence without changing generator results."""
    now = time.perf_counter()
    if now < float(state.get('next', 0.0) or 0.0):
        return
    state['next'] = now + float(interval)
    time.sleep(0)
