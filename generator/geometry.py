"""Generator collision geometry collection and seam stitching.

Standalone functions detached from AINEditor — no editor state dependency.
"""

import math
from collections import Counter, defaultdict

from cmodel.cmodel_access import (
    get_cmodel_collision_segments_3d,
    get_cmodel_collision_triangles_3d,
    get_cmodel_walkable_triangles,
    get_cmodel_transit_triangles,
    get_cmodel_walkable_component_profiles,
    _is_tree_like_foliage_entity,
)
from entity.entity_classify import classify_item_collision_hint
from entity.entity_data import DEFAULT_RADIUS, OBSTACLE_RADII
from generator.support import (
    GENERATOR_QUANTIZED_BASE_PITCH_M,
    GENERATOR_QUANTIZED_GRID_M,
    GENERATOR_REACHABLE_STAIR_STEP,
    _V52Spatial,
)
from resources.items_def import _load_items_def
from shared.nav_helpers import (
    GENERATOR_IGNORE_DESTROYABLE,
    _item_is_clearable_prop,
    build_ground_z_ref,
    interp_z,
)
from terrain.terrain import _terrain_sample_height


def _generator_entity_category(e, item=None):
    try:
        item = item or {}
        cat = str((item.get('category') or item.get('type') or
                   e.get('category') or e.get('render_kind') or '')).strip().lower()
        return cat
    except Exception:
        return ''


def _generator_should_skip_non_collision_foliage(e, item=None, segs=None):
    """Filter visual foliage that must not carve nav holes.

    Keep tree/grove-like foliage with useful CModel geometry.
    Skip bush/shrub/grass-style visual models.
    """
    if not True:
        return False
    item = item or {}
    if _generator_entity_category(e, item) != 'foliage':
        return False

    names = []
    for src in (item, e or {}):
        try:
            for k in ('graphic', 'model', 'name', 'description'):
                v = src.get(k) if hasattr(src, 'get') else None
                if v:
                    names.append(str(v).lower())
        except Exception:
            pass
    joined = ' '.join(names)
    tree_words = ('tree', 'palm', 'trunk', 'jungle', 'forest', 'grove', 'banana')
    bush_words = ('bush', 'shrub', 'grass', 'weed', 'fern', 'plant', 'flower')
    if any(w in joined for w in tree_words):
        return False
    if any(w in joined for w in bush_words):
        return True

    try:
        tid = int(e.get('type_id', -1))
        if not _is_tree_like_foliage_entity(tid, segs):
            return True
    except Exception:
        pass
    return False


def _generator_collision_entity(e, *, game_path, pff_cache,
                                ignore_destroyable=True, ignore_vehicles=False):
    try:
        tid = int(e.get('type_id', -1))
    except Exception:
        return False
    if not bool(e.get('is_static')):
        return False
    try:
        item = {}
        if game_path:
            item = _load_items_def(game_path, log=None, pff_cache=pff_cache).get(tid, {})
            if item and classify_item_collision_hint(item) != 'candidate':
                return False
            if (bool(ignore_destroyable)
                    and _item_is_clearable_prop(item)
                    and not e.get('invincible')):
                return False
        if (bool(ignore_vehicles)
                and _generator_entity_category(e, item) == 'vehicle'):
            return False
        if _generator_should_skip_non_collision_foliage(e, item, None):
            return False
    except Exception:
        pass
    return True


def _generator_geometry_cache_key(entities, bms_path, terrain_z, terrain_info,
                                  terrain_mis_path, z_filter_enabled,
                                  ignore_destroyable, ignore_vehicles,
                                  game_path, pff_cache):
    rows = []
    for e in (entities or []):
        if not _generator_collision_entity(e, game_path=game_path, pff_cache=pff_cache,
                                           ignore_destroyable=ignore_destroyable,
                                           ignore_vehicles=ignore_vehicles):
            continue
        try:
            rows.append((int(e.get('type_id', -1)), round(float(e.get('x', 0.0)), 3),
                         round(float(e.get('y', 0.0)), 3), round(float(e.get('z', 0.0)), 3),
                         round(float(e.get('heading', 0.0) or 0.0), 3),
                         round(float(e.get('pitch', 0.0) or 0.0), 3),
                         bool(e.get('is_static'))))
        except Exception:
            pass
    if isinstance(terrain_info, dict):
        terrain_signature = (
            str(terrain_mis_path or ''),
            id(terrain_info.get('heightmap')),
            str(terrain_info.get('_phase_mode', '') or ''),
        )
    else:
        terrain_signature = ('', 0, '')
    return (str(bms_path or ''),
            round(float(terrain_z or 18.5), 3),
            terrain_signature,
            True,
            True,
            bool(z_filter_enabled),
            bool(ignore_destroyable),
            bool(ignore_vehicles),
            tuple(rows))


def collect_generator_geometry(entities, terrain_z, terrain_info,
                               game_path, pff_cache,
                               bms_path='', terrain_mis_path='',
                               z_filter_enabled=True,
                               ignore_destroyable=True,
                               ignore_vehicles=False,
                               cache=None, on_progress=None):
    """Collect collision geometry for generator use.

    Returns dict with keys: world_segments, entities_core, stats, cache_hit,
    support_triangles, support_triangle_metadata, collision_segments_3d,
    collision_triangles_3d, cache_dict.
    """
    _math = math
    _defaultdict = defaultdict
    _Counter = Counter
    key = _generator_geometry_cache_key(
        entities, bms_path, terrain_z, terrain_info, terrain_mis_path,
        z_filter_enabled, ignore_destroyable, ignore_vehicles,
        game_path, pff_cache)
    if cache and cache.get('key') == key:
        return {
            'world_segments': cache['world_segments'],
            'entities_core': cache['entities_core'],
            'stats': cache['stats'],
            'cache_hit': True,
            'support_triangles': cache.get('support_triangles', []),
            'support_triangle_metadata': cache.get('support_triangle_metadata', []),
            'collision_segments_3d': cache.get('collision_segments_3d', []),
            'collision_triangles_3d': cache.get('collision_triangles_3d', []),
            'cache_dict': cache,
        }

    _tz = float(terrain_z or 18.5)
    _t_info = terrain_info
    _z_ref = build_ground_z_ref(entities, _tz) if entities else []
    band_lo = _tz + 0.20
    band_hi = _tz + 1.80
    surface_z_cache = {}

    def _surface_z_for_band(x, y):
        skey = (float(x), float(y))
        cached_z = surface_z_cache.get(skey)
        if cached_z is not None:
            return cached_z
        if _t_info is not None:
            try:
                h = _terrain_sample_height(_t_info, x, y)
                if h is not None:
                    value = float(h)
                    surface_z_cache[skey] = value
                    return value
            except Exception:
                pass
        value = interp_z(x, y, _z_ref, _tz)
        surface_z_cache[skey] = value
        return value

    collision_entities = [e for e in (entities or [])
                         if _generator_collision_entity(
                             e, game_path=game_path, pff_cache=pff_cache,
                             ignore_destroyable=ignore_destroyable,
                             ignore_vehicles=ignore_vehicles)]
    world_segments = []
    collision_segments_3d = []
    collision_triangles_3d = []
    support_triangles = []
    support_triangle_metadata = []
    ent_points = _defaultdict(list)
    entities_core = []
    failures = _Counter()
    loaded_types = set()

    def rot_xy(lx, ly, heading):
        a = _math.radians(-float(heading or 0.0))
        ca = _math.cos(a); sa = _math.sin(a)
        return ca*lx - sa*ly, sa*lx + ca*ly

    def rot_xyz(lx, ly, lz, heading, pitch):
        """Apply MED local pitch (around X), then heading (around Z)."""
        p = _math.radians(float(pitch or 0.0))
        cp = _math.cos(p); sp = _math.sin(p)
        ly, lz = ly*cp - lz*sp, ly*sp + lz*cp
        rx, ry = rot_xy(lx, ly, heading)
        return rx, ry, lz
    _gp = game_path
    _pff = pff_cache

    for src_i, e in enumerate(collision_entities):
        try:
            tid = int(e.get('type_id', -1))
            ex = float(e.get('x', 0.0)); ey = float(e.get('y', 0.0)); ez = float(e.get('z', 0.0))
            heading = float(e.get('heading', 0.0) or 0.0)
            pitch = float(e.get('pitch', 0.0) or 0.0)
            name = str(e.get('name') or e.get('graphic') or e.get('model') or '')
        except Exception:
            failures['bad_entity_record'] += 1
            continue
        try:
            item = _load_items_def(_gp, log=None, pff_cache=_pff).get(tid, {}) if _gp else {}
        except Exception:
            item = {}
        ent_cat = _generator_entity_category(e, item)
        support_graphic = str(
            item.get('graphic') or e.get('graphic')
            or e.get('model') or e.get('name') or '').lower()
        collision_ez = ez
        if (ent_cat == 'foliage' and
                str(item.get('ai_function') or '').strip().lower() == 'tree'):
            collision_ez = _surface_z_for_band(ex, ey)
            failures['tree_collision_terrain_anchored'] += 1
        local3d = None
        local_triangles = None
        local_support = None
        local_transit = None
        local_support_component_profiles = None
        try:
            local3d = get_cmodel_collision_segments_3d(
                tid, _gp, allow_load=True, log=lambda m: None)
            local_triangles = get_cmodel_collision_triangles_3d(
                tid, _gp, allow_load=True, log=lambda m: None)
            local_support = get_cmodel_walkable_triangles(
                tid, _gp, allow_load=True, log=lambda m: None)
            if ent_cat == 'decoration':
                local_transit = get_cmodel_transit_triangles(
                    tid, _gp, allow_load=True, log=lambda m: None)
            if bool(z_filter_enabled):
                local_support_component_profiles = (
                    get_cmodel_walkable_component_profiles(
                        tid, _gp, allow_load=True,
                        log=lambda m: None))
            loaded_types.add(tid)
        except Exception:
            failures['cmodel3d_error'] += 1
            local3d = None

        if _generator_should_skip_non_collision_foliage(e, item, local3d):
            failures['non_collision_foliage_skipped'] += 1
            continue
        if _generator_entity_category(e, item) == 'foliage' and not local3d:
            failures['foliage_without_cmodel_skipped'] += 1
            continue

        is_landmine_section = (
            str(item.get('ai_function') or '').strip().lower() == 'lndm'
            or str(item.get('render_function') or '').strip().lower() == 'lndm'
        )
        if is_landmine_section:
            failures['nonphysical_landmine_sections_skipped'] += 1
            continue

        core_i = len(entities_core)
        ent_graphic = support_graphic
        entities_core.append({'x': ex, 'y': ey, 'z': ez, 'type_id': tid, 'name': name.lower(),
                              'category': ent_cat, 'graphic': ent_graphic,
                              '_barrel': ('barl' in name.lower()) or ('barrel' in name.lower()) or tid in (1483, 4301)})
        if local3d:
            support_edges = set()
            for triangle in (local_support or []):
                for edge_index in range(3):
                    point_a = triangle[edge_index]
                    point_b = triangle[(edge_index + 1) % 3]
                    support_edges.add(tuple(sorted((
                        tuple(round(float(value), 4) for value in point_a),
                        tuple(round(float(value), 4) for value in point_b),
                    ))))
            transit_edges = set()
            for triangle in (local_transit or []):
                for edge_index in range(3):
                    point_a = triangle[edge_index]
                    point_b = triangle[(edge_index + 1) % 3]
                    transit_edges.add(tuple(sorted((
                        tuple(round(float(value), 4) for value in point_a),
                        tuple(round(float(value), 4) for value in point_b),
                    ))))
            seen_edges = set()
            for seg in local3d:
                try:
                    a, b = seg
                    lx1, ly1, lz1 = float(a[0]), float(a[1]), float(a[2])
                    lx2, ly2, lz2 = float(b[0]), float(b[1]), float(b[2])
                except Exception:
                    continue
                local_edge_key = tuple(sorted((
                    (round(lx1, 4), round(ly1, 4), round(lz1, 4)),
                    (round(lx2, 4), round(ly2, 4), round(lz2, 4)),
                )))
                if local_edge_key in support_edges:
                    failures['walkable_support_edges_ignored'] += 1
                    continue
                if local_edge_key in transit_edges:
                    failures['decoration_transit_edges_ignored'] += 1
                    continue
                rx1, ry1, rz1 = rot_xyz(lx1, ly1, lz1, heading, pitch)
                rx2, ry2, rz2 = rot_xyz(lx2, ly2, lz2, heading, pitch)
                x1 = ex + rx1; y1 = ey + ry1; x2 = ex + rx2; y2 = ey + ry2
                wz1 = collision_ez + rz1; wz2 = collision_ez + rz2
                collision_segments_3d.append(
                    (x1, y1, wz1, x2, y2, wz2, core_i))
                ground_z = _surface_z_for_band((x1 + x2) * 0.5, (y1 + y2) * 0.5)
                band_lo = ground_z + 0.20
                band_hi = ground_z + 1.80
                if min(wz1, wz2) > band_hi or max(wz1, wz2) < band_lo:
                    continue
                if _math.hypot(x2 - x1, y2 - y1) <= 0.08:
                    continue
                q = tuple(sorted(((round(x1, 2), round(y1, 2)), (round(x2, 2), round(y2, 2)))))
                if q in seen_edges:
                    continue
                seen_edges.add(q)
                world_segments.append((x1, y1, x2, y2, core_i))
                ent_points[core_i].append((x1, y1)); ent_points[core_i].append((x2, y2))
            for support_index, triangle in enumerate(local_support or []):
                transformed = []
                for lx, ly, lz in triangle:
                    rx, ry, rz = rot_xyz(float(lx), float(ly), float(lz), heading, pitch)
                    transformed.append((ex + rx, ey + ry, collision_ez + rz))
                support_triangles.append(tuple(transformed))
                component_profile = (
                    local_support_component_profiles[support_index]
                    if (local_support_component_profiles is not None
                        and support_index
                        < len(local_support_component_profiles))
                    else {}
                )
                component_size = int(component_profile.get('size', 0))
                component_z_span = float(
                    component_profile.get('z_span', 0.0))
                component_footprint_area = float(
                    component_profile.get('footprint_area', 0.0))
                support_triangle_metadata.append({
                    'entity_index': int(core_i),
                    'type_id': int(tid),
                    'graphic': ent_graphic,
                    'category': ent_cat,
                    'component_size': component_size,
                    'component_z_span': component_z_span,
                    'component_footprint_area': component_footprint_area,
                    'stair_sequence_candidate': bool(
                        component_profile.get(
                            'stair_sequence_candidate', False)),
                    'geometry_stair_sequence_candidate': bool(
                        component_profile.get(
                            'geometry_stair_sequence_candidate', False)),
                    'tunnel_stair_candidate': bool(
                        'tun' in ent_graphic
                        and 0 < component_size <= 40),
                    'tunnel_roof_candidate': bool(
                        'tun' in ent_graphic
                        and component_size >= 80
                        and component_z_span >= 1.0),
                })
            for triangle in (local_triangles or []):
                transformed = []
                for lx, ly, lz in triangle:
                    rx, ry, rz = rot_xyz(float(lx), float(ly), float(lz), heading, pitch)
                    transformed.append((ex + rx, ey + ry, collision_ez + rz))
                if len(transformed) == 3:
                    a, b, c = transformed
                    collision_triangles_3d.append(
                        (a[0], a[1], a[2],
                         b[0], b[1], b[2],
                         c[0], c[1], c[2], core_i))
        else:
            try:
                r = float(OBSTACLE_RADII.get(tid, DEFAULT_RADIUS))
            except Exception:
                r = float(DEFAULT_RADIUS)
            cat = _generator_entity_category(e, item)
            lname = (name or '').lower()
            fallback_words = ('barl', 'barrel', 'crate', 'wcrate', 'cargo', 'box', 'tire', 'sandbag', 'sndbag',
                              'vndcrt', 'hndcrt', 'ncar', 'ntrk', 'nbus', 'car', 'truck', 'bus', 'humv', 'jeep', 'vehicle')
            allow_radius_fallback = (cat not in ('building', 'foliage') and r <= 5.0 and any(w in lname for w in fallback_words))
            if r > 0.05 and allow_radius_fallback:
                sides = 12
                pts = [(_math.cos(_math.tau * k / sides) * r, _math.sin(_math.tau * k / sides) * r) for k in range(sides)]
                for k in range(sides):
                    ax, ay = pts[k]; bx, by = pts[(k + 1) % sides]
                    rx1, ry1 = rot_xy(ax, ay, heading); rx2, ry2 = rot_xy(bx, by, heading)
                    x1 = ex + rx1; y1 = ey + ry1; x2 = ex + rx2; y2 = ey + ry2
                    world_segments.append((x1, y1, x2, y2, core_i))
                failures['radius_fallback_direct'] += 1
            else:
                failures['no_geometry_no_fake_radius_fallback'] += 1
        if (src_i + 1) % 12 == 0 and on_progress is not None:
            message = (f'Single-file lab core geometry '
                       f'{src_i + 1}/{len(collision_entities)} entities, '
                       f'{len(world_segments)} segments...')
            try:
                on_progress(message)
            except Exception:
                pass
    stats = {'collision_entities': len(collision_entities),
             'final_collision_entities': len(entities_core),
             'segments': len(world_segments),
             'collision_segments_3d': len(collision_segments_3d),
             'collision_triangles_3d': len(collision_triangles_3d),
             'support_triangles': len(support_triangles),
             'loaded_cmodel_types': len(loaded_types), 'failures': dict(failures),
             'band_lo': band_lo, 'band_hi': band_hi}
    cache_dict = {'key': key, 'world_segments': world_segments,
                  'entities_core': entities_core, 'stats': stats,
                  'support_triangles': support_triangles,
                  'support_triangle_metadata': support_triangle_metadata,
                  'collision_segments_3d': collision_segments_3d,
                  'collision_triangles_3d': collision_triangles_3d}
    return {
        'world_segments': world_segments,
        'entities_core': entities_core,
        'stats': stats,
        'cache_hit': False,
        'support_triangles': support_triangles,
        'support_triangle_metadata': support_triangle_metadata,
        'collision_segments_3d': collision_segments_3d,
        'collision_triangles_3d': collision_triangles_3d,
        'cache_dict': cache_dict,
    }


def line_crosses_segments(ax, ay, bx, by, segments):
    minx = min(ax, bx) - 0.05; maxx = max(ax, bx) + 0.05
    miny = min(ay, by) - 0.05; maxy = max(ay, by) + 0.05
    d1x = bx - ax; d1y = by - ay
    for seg in segments or []:
        try:
            x1, y1, x2, y2 = float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])
        except Exception:
            continue
        if max(x1, x2) < minx or min(x1, x2) > maxx or max(y1, y2) < miny or min(y1, y2) > maxy:
            continue
        d2x = x2 - x1; d2y = y2 - y1
        den = d1x * d2y - d1y * d2x
        if -1e-12 < den < 1e-12:
            continue
        t = ((x1 - ax) * d2y - (y1 - ay) * d2x) / den
        if t <= 0.02 or t >= 0.98:
            continue
        u = ((x1 - ax) * d1y - (y1 - ay) * d1x) / den
        if 0.0 <= u <= 1.0:
            return True
    return False


def add_old_new_seams(existing_nodes, batch_nodes, offset, world_segments,
                      collision_triangles_3d=None,
                      quantized_topology=False):
    """Add sparse old-new links for additive batches without rebuilding old graph fabric."""
    if not existing_nodes or not batch_nodes:
        return 0
    try:
        old_grid = _V52Spatial(8.0)
        for oi, on in enumerate(existing_nodes):
            try:
                old_grid.insert(float(on.x), float(on.y), oi)
            except Exception:
                pass

        segs_for_bridge = []
        for seg in world_segments or []:
            try:
                if len(seg) >= 4:
                    segs_for_bridge.append((float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])))
            except Exception:
                pass

        bridge_segment_cell = 4.0
        bridge_segment_grid = {}
        bridge_global_segments = []

        for segment_index, (x1, y1, x2, y2) in enumerate(segs_for_bridge):
            gx0 = int(math.floor(min(x1, x2) / bridge_segment_cell))
            gx1 = int(math.floor(max(x1, x2) / bridge_segment_cell))
            gy0 = int(math.floor(min(y1, y2) / bridge_segment_cell))
            gy1 = int(math.floor(max(y1, y2) / bridge_segment_cell))
            cell_count = (gx1 - gx0 + 1) * (gy1 - gy0 + 1)
            if cell_count > 1024:
                bridge_global_segments.append(segment_index)
                continue
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    bridge_segment_grid.setdefault((gx, gy), []).append(segment_index)

        def _line_crosses_indexed_segments(ax, ay, bx, by):
            if not segs_for_bridge:
                return False
            minx = min(ax, bx) - 0.05
            maxx = max(ax, bx) + 0.05
            miny = min(ay, by) - 0.05
            maxy = max(ay, by) + 0.05
            gx0 = int(math.floor(minx / bridge_segment_cell))
            gx1 = int(math.floor(maxx / bridge_segment_cell))
            gy0 = int(math.floor(miny / bridge_segment_cell))
            gy1 = int(math.floor(maxy / bridge_segment_cell))
            candidate_indices = set(bridge_global_segments)
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    candidate_indices.update(bridge_segment_grid.get((gx, gy), ()))
            d1x = bx - ax
            d1y = by - ay
            for segment_index in candidate_indices:
                x1, y1, x2, y2 = segs_for_bridge[segment_index]
                if (max(x1, x2) < minx or min(x1, x2) > maxx or
                        max(y1, y2) < miny or min(y1, y2) > maxy):
                    continue
                d2x = x2 - x1
                d2y = y2 - y1
                den = d1x * d2y - d1y * d2x
                if -1e-12 < den < 1e-12:
                    continue
                t = ((x1 - ax) * d2y - (y1 - ay) * d2x) / den
                if t <= 0.02 or t >= 0.98:
                    continue
                u = ((x1 - ax) * d1y - (y1 - ay) * d1x) / den
                if 0.0 <= u <= 1.0:
                    return True
            return False

        triangle_cell = 3.0
        triangle_grid = {}
        triangles = list(collision_triangles_3d or [])
        for triangle_index, triangle in enumerate(triangles):
            try:
                ax, ay, _az, bx, by, _bz, cx, cy, _cz, _entity_index = triangle
                gx0 = int(math.floor(min(ax, bx, cx) / triangle_cell))
                gx1 = int(math.floor(max(ax, bx, cx) / triangle_cell))
                gy0 = int(math.floor(min(ay, by, cy) / triangle_cell))
                gy1 = int(math.floor(max(ay, by, cy) / triangle_cell))
                for gx in range(gx0, gx1 + 1):
                    for gy in range(gy0, gy1 + 1):
                        triangle_grid.setdefault((gx, gy), []).append(triangle_index)
            except Exception:
                continue

        def _crosses_collision_face(px, py, pz, qx, qy, qz):
            if not triangles:
                return False
            min_gx = int(math.floor(min(px, qx) / triangle_cell))
            max_gx = int(math.floor(max(px, qx) / triangle_cell))
            min_gy = int(math.floor(min(py, qy) / triangle_cell))
            max_gy = int(math.floor(max(py, qy) / triangle_cell))
            candidates = set()
            for gx in range(min_gx, max_gx + 1):
                for gy in range(min_gy, max_gy + 1):
                    candidates.update(triangle_grid.get((gx, gy), ()))
            dx, dy, dz = qx - px, qy - py, qz - pz
            for triangle_index in candidates:
                ax, ay, az, bx, by, bz, cx, cy, cz, _entity_index = triangles[triangle_index]
                e1x, e1y, e1z = bx - ax, by - ay, bz - az
                e2x, e2y, e2z = cx - ax, cy - ay, cz - az
                hx = dy * e2z - dz * e2y
                hy = dz * e2x - dx * e2z
                hz = dx * e2y - dy * e2x
                det = e1x * hx + e1y * hy + e1z * hz
                if -1e-10 < det < 1e-10:
                    continue
                inv_det = 1.0 / det
                sx, sy, sz = px - ax, py - ay, pz - az
                u = (sx * hx + sy * hy + sz * hz) * inv_det
                if u < -1e-7 or u > 1.0 + 1e-7:
                    continue
                qvx = sy * e1z - sz * e1y
                qvy = sz * e1x - sx * e1z
                qvz = sx * e1y - sy * e1x
                v = (dx * qvx + dy * qvy + dz * qvz) * inv_det
                if v < -1e-7 or u + v > 1.0 + 1e-7:
                    continue
                t = (e2x * qvx + e2y * qvy + e2z * qvz) * inv_det
                if 0.02 < t < 0.98:
                    return True
            return False

        def _height_check_blocked(ax, ay, az, bx, by, bz):
            lo = _crosses_collision_face(ax, ay, az - 0.5, bx, by, bz - 0.5)
            hi = _crosses_collision_face(ax, ay, az + 0.5, bx, by, bz + 0.5)
            return lo or hi

        def _r(n):
            try:
                return max(0.25, float(getattr(n, 'b15', 16)) / 16.0)
            except Exception:
                return 1.0

        def _node_xy(global_id):
            if 0 <= global_id < len(existing_nodes):
                node = existing_nodes[global_id]
            elif offset <= global_id < offset + len(batch_nodes):
                node = batch_nodes[global_id - offset]
            else:
                return None
            return float(node.x), float(node.y)

        def _direction_covered(node, x, y, tx, ty, distance):
            angle = math.atan2(ty - y, tx - x)
            for neighbor_id in node.neighbors or []:
                pos = _node_xy(int(neighbor_id))
                if pos is None:
                    continue
                px, py = pos
                other_distance = math.hypot(px - x, py - y)
                other_angle = math.atan2(py - y, px - x)
                delta = abs((angle - other_angle + math.pi) % math.tau - math.pi)
                if delta < math.pi / 8.0 and other_distance <= 1.08 * distance:
                    return True
            return False

        def _old_node_intervenes(nx, ny, ox, oy, target_old_id):
            vx = ox - nx; vy = oy - ny
            d2 = vx * vx + vy * vy
            if d2 <= 1e-9:
                return False
            distance = math.sqrt(d2)
            mx, my = (nx + ox) * 0.5, (ny + oy) * 0.5
            corridor = max(0.35, min(0.75, 0.18 * distance))
            for px, py, old_id in old_grid.query(mx, my, distance * 0.5 + corridor):
                if old_id == target_old_id:
                    continue
                t = ((px - nx) * vx + (py - ny) * vy) / d2
                if t <= 0.10 or t >= 0.90:
                    continue
                qx = nx + t * vx; qy = ny + t * vy
                if math.hypot(px - qx, py - qy) <= corridor:
                    return True
            return False

        old_new_links = 0
        max_deg = 10
        seam_max_dz = float(GENERATOR_REACHABLE_STAIR_STEP) + 0.08

        if quantized_topology:
            qgrid = float(GENERATOR_QUANTIZED_GRID_M)
            base_pitch = float(GENERATOR_QUANTIZED_BASE_PITCH_M)
            source_step_q = int(round(base_pitch / qgrid))

            def _exact_source_keys(node):
                raw_keys = getattr(node, 'quantized_source_keys', ()) or ()
                result = set()
                for key in raw_keys:
                    try:
                        result.add((int(key[0]), int(key[1])))
                    except Exception:
                        continue
                return result

            def _rasterized_old_keys(node):
                exact = _exact_source_keys(node)
                if exact:
                    return exact
                if (bool(getattr(node, 'quantized_map_grid', False))
                        and int(getattr(node, 'b15', 0)) in (4, 8, 16, 32)):
                    side_cells = int(node.b15) // 4
                    center_qx = int(round(float(node.x) / qgrid))
                    center_qy = int(round(float(node.y) / qgrid))
                    source_offsets = range(
                        -(side_cells - 1), side_cells, 2)
                    return {
                        (center_qx + off_x, center_qy + off_y)
                        for off_x in source_offsets
                        for off_y in source_offsets
                    }
                try:
                    nx = float(node.x); ny = float(node.y)
                    owner_radius = max(
                        base_pitch * 0.5,
                        _r(node) + base_pitch * 0.4)
                except Exception:
                    return set()
                ix0 = int(math.floor((nx - owner_radius) / base_pitch))
                ix1 = int(math.ceil((nx + owner_radius) / base_pitch))
                iy0 = int(math.floor((ny - owner_radius) / base_pitch))
                iy1 = int(math.ceil((ny + owner_radius) / base_pitch))
                result = set()
                for ix in range(ix0, ix1 + 1):
                    x = ix * base_pitch
                    for iy in range(iy0, iy1 + 1):
                        y = iy * base_pitch
                        if math.hypot(x - nx, y - ny) <= owner_radius + 1e-9:
                            result.add((ix * source_step_q,
                                        iy * source_step_q))
                if not result:
                    result.add((
                        int(math.floor(nx / base_pitch + 0.5)) * source_step_q,
                        int(math.floor(ny / base_pitch + 0.5)) * source_step_q))
                return result

            old_cell_owners = defaultdict(list)
            for oi, on in enumerate(existing_nodes):
                for source_key in _rasterized_old_keys(on):
                    old_cell_owners[source_key].append(oi)

            boundary_counts = Counter()
            cardinal_steps = ((source_step_q, 0), (-source_step_q, 0),
                              (0, source_step_q), (0, -source_step_q))
            for ni, nn in enumerate(batch_nodes):
                new_keys = _exact_source_keys(nn)
                if not new_keys:
                    continue
                for cell_qx, cell_qy in new_keys:
                    for step_qx, step_qy in cardinal_steps:
                        for oi in old_cell_owners.get(
                                (cell_qx + step_qx,
                                 cell_qy + step_qy), ()):
                            boundary_counts[(ni, oi, step_qx, step_qy)] += 1

            candidates_by_new = defaultdict(list)
            for (ni, oi, step_qx, step_qy), shared_count in boundary_counts.items():
                try:
                    nn = batch_nodes[ni]; on = existing_nodes[oi]
                    nx = float(nn.x); ny = float(nn.y)
                    ox = float(on.x); oy = float(on.y)
                    d = math.hypot(nx - ox, ny - oy)
                    dz = abs(float(nn.z) - float(on.z))
                    if d < 0.30 or dz > seam_max_dz:
                        continue
                    if _crosses_collision_face(
                            nx, ny, float(nn.z),
                            ox, oy, float(on.z)):
                        continue
                    if (not triangles
                            and _line_crosses_indexed_segments(
                                nx, ny, ox, oy)):
                        continue
                    if _height_check_blocked(
                            nx, ny, float(nn.z),
                            ox, oy, float(on.z)):
                        continue
                    candidates_by_new[ni].append((
                        -int(shared_count), d,
                        int(step_qx), int(step_qy), oi))
                except Exception:
                    continue

            for ni in sorted(candidates_by_new):
                nn = batch_nodes[ni]
                if len(nn.neighbors or []) >= max_deg:
                    continue
                used_sides = set()
                used_old = set()
                for _neg_shared, _distance, step_qx, step_qy, oi in sorted(
                        candidates_by_new[ni]):
                    side = (step_qx, step_qy)
                    if side in used_sides or oi in used_old:
                        continue
                    on = existing_nodes[oi]
                    if len(on.neighbors or []) >= max_deg:
                        continue
                    new_id = offset + ni
                    if oi not in nn.neighbors:
                        nn.neighbors.append(oi)
                    if new_id not in on.neighbors:
                        on.neighbors.append(new_id)
                    used_sides.add(side)
                    used_old.add(oi)
                    old_new_links += 1
                    if len(nn.neighbors or []) >= max_deg:
                        break
            return old_new_links

        for ni, nn in enumerate(batch_nodes):
            try:
                nx = float(nn.x); ny = float(nn.y); nr = _r(nn)
            except Exception:
                continue
            if len(nn.neighbors or []) >= max_deg:
                continue
            qr = max(5.2, min(10.0, 1.85 * (nr + 2.0)))
            cand = []
            for ox, oy, oi in old_grid.query(nx, ny, qr):
                if oi is None or oi < 0 or oi >= len(existing_nodes):
                    continue
                on = existing_nodes[oi]
                try:
                    new_id = offset + ni
                    if new_id in (on.neighbors or []) or oi in (nn.neighbors or []):
                        continue
                    if len(on.neighbors or []) >= max_deg:
                        continue
                    orad = _r(on)
                    d = math.hypot(nx - ox, ny - oy)
                    dz = abs(float(nn.z) - float(on.z))
                    rsum = nr + orad
                    if d < 0.80 or d > max(7.8, 1.70 * rsum):
                        continue
                    if dz > seam_max_dz:
                        continue
                    if d < 0.50 * rsum:
                        continue
                    if _crosses_collision_face(
                            nx, ny, float(nn.z),
                            ox, oy, float(on.z)):
                        continue
                    if (not triangles
                            and _line_crosses_indexed_segments(
                                nx, ny, ox, oy)):
                        continue
                    if _height_check_blocked(
                            nx, ny, float(nn.z),
                            ox, oy, float(on.z)):
                        continue
                    if _old_node_intervenes(nx, ny, ox, oy, oi):
                        continue
                    if _direction_covered(nn, nx, ny, ox, oy, d):
                        continue
                    if _direction_covered(on, ox, oy, nx, ny, d):
                        continue
                    ang = math.atan2(oy - ny, ox - nx)
                    sec = int(((ang + math.pi) / math.tau) * 8) % 8
                    cand.append((d, sec, oi))
                except Exception:
                    continue
            cand.sort()
            used = set()
            added_for_new = 0
            for d, sec, oi in cand:
                if added_for_new >= 2 or len(nn.neighbors or []) >= max_deg:
                    break
                if sec in used:
                    continue
                on = existing_nodes[oi]
                if len(on.neighbors or []) >= max_deg:
                    continue
                new_id = offset + ni
                if oi not in nn.neighbors:
                    nn.neighbors.append(oi)
                if new_id not in on.neighbors:
                    on.neighbors.append(new_id)
                used.add(sec)
                added_for_new += 1
                old_new_links += 1
        return old_new_links
    except Exception:
        return 0
