"""Generator area-zone tiling — per-tile generation orchestration and seam stitching."""

import time

from format.ain_format import Node, MAX_NEIGHBORS
from terrain.terrain import _terrain_sample_height
from shared.nav_helpers import interp_z, build_ground_z_ref
from generator.support import (LabNode, GENERATOR_REACHABLE_MAX_STEP)
from generator.core import (generate_seed_disc_campaign_v58,
    _build_generator_reachable_surface,
    _build_generator_terrain_surface_contract)
from generator.geometry import add_old_new_seams


def execute_tile_zone_job(job):
    """Run the tile-zone transaction algorithm outside Tk.

    Each tile job sees the immutable pre-zone graph PLUS cropped nodes from
    all previous tile jobs, so spacing checks prevent boundary overlap.
    Cross-batch seams are added after all owned batches have been frozen.
    """
    from generator.process import (
        _configure_generator_worker_process,
        _hydrate_generator_process_job,
        _emit_generator_process_event)
    _configure_generator_worker_process()
    hydrate_started = time.perf_counter()
    job = _hydrate_generator_process_job(job)
    hydrate_elapsed = time.perf_counter() - hydrate_started

    terrain_info = job.get('terrain_info')
    terrain_z = float(job.get('terrain_z', 18.5))
    entities_snapshot = list(job.get('entities_snapshot') or [])
    z_ref = build_ground_z_ref(entities_snapshot, terrain_z) if entities_snapshot else []

    def surface_z(x, y):
        if terrain_info is not None:
            try:
                height = _terrain_sample_height(terrain_info, x, y)
                if height is not None:
                    return float(height)
            except Exception:
                pass
        return interp_z(x, y, z_ref, terrain_z)

    existing_nodes = [Node.from_dict(record)
                      for record in list(job.get('existing_nodes') or [])]
    immutable_existing_nodes = tuple(existing_nodes)
    existing_count = len(existing_nodes)
    jobs = list(job.get('tile_jobs') or [])
    z_filter_enabled = bool(job.get('z_filter_enabled'))
    rooftops_enabled = bool(job.get('rooftops_enabled'))
    ladders_enabled = bool(
        z_filter_enabled and rooftops_enabled
        and job.get('ladders_enabled'))
    quantized_enabled = bool(job.get('quantized_enabled'))
    world_segments = job.get('world_segments') or []
    entities_core = job.get('entities_core') or []
    support_triangles = job.get('support_triangles') or []
    support_metadata = job.get('support_triangle_metadata') or []
    collision_segments_3d = job.get('collision_segments_3d') or []
    collision_triangles_3d = job.get('collision_triangles_3d') or []

    raw_batches = []
    all_batch_nodes = []
    accumulated_tile_nodes = []
    total_before_crop = 0
    total_seam_links = 0
    job_reports = []
    job_count = max(1, len(jobs))

    for job_index, tile_job in enumerate(jobs):
        cx, cy = tile_job['center']
        radius = float(tile_job['radius'])
        base_pct = 9.0 + 82.0 * job_index / job_count
        span_pct = 82.0 / job_count
        _emit_generator_process_event(
            'phase', base_pct,
            f'Local transaction {job_index + 1}/{len(jobs)}',
            f'R={radius:.1f}m; building reachable surface')

        generator_surface_z = surface_z
        support_metrics = {'enabled': False}
        if z_filter_enabled:
            generator_surface_z, support_metrics = _build_generator_reachable_surface(
                (float(cx), float(cy)), radius, surface_z,
                support_triangles,
                support_triangle_metadata=support_metadata,
                collision_segments_3d=collision_segments_3d,
                collision_triangles_3d=collision_triangles_3d,
                max_step=GENERATOR_REACHABLE_MAX_STEP,
                cell=0.25,
                include_rooftops=rooftops_enabled,
                highest_broad_rooftops_only=bool(
                    job.get('highest_broad_rooftops_only')))
            if generator_surface_z is None:
                job_reports.append({
                    'job': job_index + 1,
                    'center': (round(cx, 3), round(cy, 3)),
                    'radius': round(radius, 3),
                    'status': 'no_reachable_surface',
                })
                continue
        else:
            generator_surface_z, support_metrics = (
                _build_generator_terrain_surface_contract(
                    (float(cx), float(cy)), radius, surface_z,
                    collision_triangles_3d=collision_triangles_3d,
                    cell=0.50))

        _emit_generator_process_event(
            'phase', base_pct + span_pct * 0.35,
            f'Local transaction {job_index + 1}/{len(jobs)}',
            f'Placing and connecting nodes (R={radius:.1f}m)')

        progress_gate = {'phase': None, 'at': 0.0}

        def core_progress(percent, phase, detail, _base=base_pct,
                          _span=span_pct, _index=job_index):
            now = time.perf_counter()
            phase_name = str(phase)
            if (phase_name == progress_gate['phase']
                    and now - progress_gate['at'] < 0.125
                    and float(percent) < 100.0):
                return
            progress_gate['phase'] = phase_name
            progress_gate['at'] = now
            core_pct = max(0.0, min(100.0, float(percent)))
            overall = _base + _span * (0.35 + core_pct * 0.0060)
            _emit_generator_process_event(
                'phase', overall,
                f'Local transaction {_index + 1}/{len(jobs)}: {phase_name}',
                detail)

        result = generate_seed_disc_campaign_v58(
            world_segments,
            entities_core,
            terrain_z,
            center=(float(cx), float(cy)),
            focus_radius=radius,
            existing_nodes=list(immutable_existing_nodes) + accumulated_tile_nodes,
            log=lambda _message: None,
            z_at=generator_surface_z,
            collision_segments_3d=collision_segments_3d,
            collision_triangles_3d=collision_triangles_3d,
            radius_cap=float(job.get('radius_cap', 80.0)),
            on_progress=core_progress,
            quantize_to_map_grid=quantized_enabled,
            geometry_pre_normalized=True,
            include_ladders=ladders_enabled)

        generated = list(result.get('nodes') or [])
        total_before_crop += len(generated)
        core_x1, core_y1, core_x2, core_y2 = tile_job['core']
        tile_list = list(tile_job.get('tiles') or [])
        kept_indices = []
        for local_index, node in enumerate(generated):
            nx = float(node.x)
            ny = float(node.y)
            if not (core_x1 <= nx < core_x2 and core_y1 <= ny < core_y2):
                continue
            if any(tx <= nx < tx + ts and ty <= ny < ty + ts
                   for tx, ty, ts in tile_list):
                kept_indices.append(local_index)

        for _ki in kept_indices:
            _kn = generated[_ki]
            accumulated_tile_nodes.append(LabNode(
                float(_kn.x), float(_kn.y), float(_kn.z),
                int(_kn.b12), int(_kn.b15),
                neighbors=[]))

        result_metrics = result.get('metrics') or {}
        report = {
            'job': job_index + 1,
            'cluster': int(tile_job['cluster_id']) + 1,
            'center': (round(cx, 3), round(cy, 3)),
            'radius': round(radius, 3),
            'generated': len(generated),
            'kept': len(kept_indices),
            'seam_links': 0,
            'walker_regrow_seeds': result_metrics.get('walker_regrow_seeds'),
            'automatic_z_filter': support_metrics,
        }
        raw_batches.append({
            'generated': generated,
            'kept_indices': kept_indices,
            'report': report,
            'ladder_old_new_seams': list(
                result_metrics.get('ladder_old_new_seams') or ()),
        })
        _emit_generator_process_event(
            'phase', base_pct + span_pct * 0.95,
            f'Local transaction {job_index + 1}/{len(jobs)}',
            f'Owned {len(kept_indices)}/{len(generated)} nodes; '
            f'{len(accumulated_tile_nodes)} accumulated for spacing')

    batch_ranges = []
    for raw in raw_batches:
        generated = raw['generated']
        kept_indices = raw['kept_indices']
        offset = existing_count + len(all_batch_nodes)
        index_map = {old_i: offset + new_i
                     for new_i, old_i in enumerate(kept_indices)}
        local_batch = []
        for old_i in kept_indices:
            node = generated[old_i]
            local_neighbors = []
            for neighbor in sorted(list(node.neighbors or [])):
                try:
                    mapped = index_map.get(int(neighbor))
                except Exception:
                    mapped = None
                if mapped is not None:
                    local_neighbors.append(mapped)
            local_batch.append(Node(
                index_map[old_i], float(node.x), float(node.y), float(node.z),
                b12=int(node.b12), b13=0, b14=0, b15=int(node.b15),
                b16=int(node.b16), b17=int(node.b17), b18=int(node.b18),
                neighbors=local_neighbors[:MAX_NEIGHBORS],
                quantized_source_keys=getattr(node, 'quantized_source_keys', None),
                quantized_map_grid=bool(getattr(node, 'quantized_map_grid', False))))
        start = len(all_batch_nodes)
        all_batch_nodes.extend(local_batch)
        kept_index_map = {
            int(old_index): int(new_index)
            for new_index, old_index in enumerate(kept_indices)
        }
        ladder_seams = [
            (int(old_id), int(kept_index_map[int(local_index)]))
            for old_id, local_index in raw.get('ladder_old_new_seams', ())
            if int(local_index) in kept_index_map
        ]
        batch_ranges.append((
            start, len(all_batch_nodes), raw['report'], ladder_seams))

    for start, end, report, ladder_seams in batch_ranges:
        local_batch = all_batch_nodes[start:end]
        if not local_batch:
            continue
        prior_nodes = existing_nodes + all_batch_nodes[:start]
        offset = existing_count + start
        seam_links = 0
        if prior_nodes:
            seam_links = add_old_new_seams(
                prior_nodes, local_batch, offset,
                world_segments, collision_triangles_3d,
                quantized_topology=quantized_enabled)
            _prior_by_id = {
                int(node.id): node for node in prior_nodes
            }
            for _old_id, _local_index in ladder_seams:
                _old_node = _prior_by_id.get(int(_old_id))
                if (_old_node is None
                        or not 0 <= int(_local_index) < len(local_batch)):
                    continue
                _new_node = local_batch[int(_local_index)]
                _new_id = offset + int(_local_index)
                if (_new_id not in (_old_node.neighbors or [])
                        and len(_old_node.neighbors or []) < MAX_NEIGHBORS):
                    _old_node.neighbors.append(_new_id)
                if (int(_old_id) not in (_new_node.neighbors or [])
                        and len(_new_node.neighbors or []) < MAX_NEIGHBORS):
                    _new_node.neighbors.append(int(_old_id))
                if (_new_id in (_old_node.neighbors or [])
                        and int(_old_id) in (_new_node.neighbors or [])):
                    seam_links += 1
        report['seam_links'] = int(seam_links or 0)
        total_seam_links += int(seam_links or 0)

    return {
        'zone_mode': True,
        'stitched_existing_nodes': [node.to_dict() for node in existing_nodes],
        'batch_nodes': [node.to_dict() for node in all_batch_nodes],
        'metrics': {
            'worker_geometry_cache_hit': bool(job.get('worker_geometry_cache_hit', False)),
            'worker_geometry_time_sec': round(hydrate_elapsed, 3),
            'geometry_stats': job.get('geometry_stats'),
            'old_new_links': int(total_seam_links),
            'local_transaction_reports': job_reports,
            'total_before_crop': int(total_before_crop),
            'zone_generated_after_crop': len(all_batch_nodes),
        },
    }
