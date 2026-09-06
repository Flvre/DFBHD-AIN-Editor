"""CModel 3D segment cache getters — load-on-demand layer over items_load+cmodel_parse."""
import math
from pathlib import Path
from shared.debug_log import _dbg
from config.render_config import CMODEL_DEDUP_EDGES
from cmodel.cmodel_parse import _parse_gpm_cmodel_3d, _parse_gpm_cmodel_3d_grouped
from resources.items_def import _load_items_def, _get_3di_bytes_for_type
from entity.entity_classify import classify_item_collision_hint
from cmodel.cmodel_geom import _foliage_hull_segments, _cmodel_husk_segments, _foliage_bounds_from_segments

_get_default_game_path = lambda: None
_get_pff_cache = lambda: {}

_3di_cmodel3d_cache = {}
_3di_cmodel_collision_segments3d_cache = {}
_3di_cmodel_triangles3d_cache = {}
_3di_walkable_triangle_cache = {}
_3di_cmodel_transit_triangle_cache = {}
_3di_cmodel3d_grouped_cache = {}
_3di_cmodel_collision_policy_recovery_cache = {}
_3di_walkable_component_profile_cache = {}
_cmodel_allowed_cache = {}


def get_cmodel_segments_3d(type_id, game_path=None, allow_load=True, log=None):
    """Return true compact/CModel 3D line segments for a type_id."""
    gp = str(game_path or _get_default_game_path() or '').lower()
    key = (gp, int(type_id))
    if key in _3di_cmodel3d_cache:
        return _3di_cmodel3d_cache[key]
    if not allow_load:
        return None
    try:
        raw, item = _get_3di_bytes_for_type(type_id, game_path or _get_default_game_path(), log=log, pff_cache=_get_pff_cache())
        if not raw:
            _3di_cmodel3d_cache[key] = None
            return None
        graphic = None
        try:
            graphic = (item or {}).get('graphic') or (item or {}).get('model') or (item or {}).get('name')
        except Exception:
            pass
        segs = _parse_gpm_cmodel_3d(raw, debug_name=f'{type_id}/{graphic}',
                                     dbg=_dbg, dedup_edges=CMODEL_DEDUP_EDGES)
        _3di_cmodel3d_cache[key] = segs
        return segs
    except Exception:
        _3di_cmodel3d_cache[key] = None
        return None


def get_cmodel_collision_segments_3d(type_id, game_path=None, allow_load=True, log=None):
    """Return only CModel faces eligible for physical collision."""
    gp = str(game_path or _get_default_game_path() or '').lower()
    key = (gp, int(type_id))
    if key in _3di_cmodel_collision_segments3d_cache:
        return _3di_cmodel_collision_segments3d_cache[key]
    if not allow_load:
        return None
    try:
        raw, item = _get_3di_bytes_for_type(type_id, game_path or _get_default_game_path(), log=log, pff_cache=_get_pff_cache())
        if not raw:
            _3di_cmodel_collision_segments3d_cache[key] = None
            return None
        graphic = None
        try:
            graphic = (item or {}).get('graphic') or (item or {}).get('model') or (item or {}).get('name')
        except Exception:
            pass
        segments = _parse_gpm_cmodel_3d(
            raw,
            debug_name=f'{type_id}/{graphic}/collision_edges',
            collision_only=True,
            collision_policy_key=key,
            dbg=_dbg, dedup_edges=CMODEL_DEDUP_EDGES,
            collision_policy_out=_3di_cmodel_collision_policy_recovery_cache,
        )
        _3di_cmodel_collision_segments3d_cache[key] = segments
        return segments
    except Exception:
        _3di_cmodel_collision_segments3d_cache[key] = None
        return None


def get_cmodel_collision_triangles_3d(type_id, game_path=None, allow_load=True, log=None):
    """Return true compact/CModel collision triangles for a type_id."""
    gp = str(game_path or _get_default_game_path() or '').lower()
    key = (gp, int(type_id))
    if key in _3di_cmodel_triangles3d_cache:
        return _3di_cmodel_triangles3d_cache[key]
    if not allow_load:
        return None
    try:
        raw, item = _get_3di_bytes_for_type(type_id, game_path or _get_default_game_path(), log=log, pff_cache=_get_pff_cache())
        if not raw:
            _3di_cmodel_triangles3d_cache[key] = None
            return None
        graphic = None
        try:
            graphic = (item or {}).get('graphic') or (item or {}).get('model') or (item or {}).get('name')
        except Exception:
            pass
        triangles = _parse_gpm_cmodel_3d(
            raw,
            debug_name=f'{type_id}/{graphic}/collision_faces',
            return_triangles=True,
            collision_only=True,
            collision_policy_key=key,
            dbg=_dbg, dedup_edges=CMODEL_DEDUP_EDGES,
            collision_policy_out=_3di_cmodel_collision_policy_recovery_cache,
        )
        _3di_cmodel_triangles3d_cache[key] = triangles
        return triangles
    except Exception:
        _3di_cmodel_triangles3d_cache[key] = None
        return None


def get_cmodel_walkable_triangles(type_id, game_path=None, allow_load=True, log=None):
    """Return compact/CModel support triangles in local (x, y, z) space."""
    gp = str(game_path or _get_default_game_path() or '').lower()
    key = (gp, int(type_id))
    if key in _3di_walkable_triangle_cache:
        return _3di_walkable_triangle_cache[key]
    if not allow_load:
        return None
    try:
        raw, item = _get_3di_bytes_for_type(type_id, game_path or _get_default_game_path(), log=log, pff_cache=_get_pff_cache())
        if not raw:
            _3di_walkable_triangle_cache[key] = None
            return None
        graphic = None
        try:
            graphic = (item or {}).get('graphic') or (item or {}).get('model') or (item or {}).get('name')
        except Exception:
            pass
        face_edges = _parse_gpm_cmodel_3d(
            raw, debug_name=f'{type_id}/{graphic}/support',
            collision_only=True, collision_policy_key=key,
            dedup_edges=False, dbg=_dbg,
            collision_policy_out=_3di_cmodel_collision_policy_recovery_cache)
        triangles = []
        for index in range(0, len(face_edges or []) - 2, 3):
            edge_a, edge_b, edge_c = face_edges[index:index + 3]
            a = tuple(float(value) for value in edge_a[0])
            b = tuple(float(value) for value in edge_a[1])
            c = tuple(float(value) for value in edge_b[1])
            if len({a, b, c}) < 3:
                continue
            ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            nx = ab[1] * ac[2] - ab[2] * ac[1]
            ny = ab[2] * ac[0] - ab[0] * ac[2]
            nz = ab[0] * ac[1] - ab[1] * ac[0]
            normal_length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if normal_length <= 1e-9 or abs(nz) / normal_length < 0.58:
                continue
            footprint_area = abs(
                (b[0] - a[0]) * (c[1] - a[1])
                - (b[1] - a[1]) * (c[0] - a[0])
            ) * 0.5
            if footprint_area <= 0.002:
                continue
            triangles.append((a, b, c))
        _3di_walkable_triangle_cache[key] = triangles or None
        return _3di_walkable_triangle_cache[key]
    except Exception:
        _3di_walkable_triangle_cache[key] = None
        return None


def get_cmodel_transit_triangles(type_id, game_path=None, allow_load=True,
                                 log=None):
    """Return steep but traversable decoration faces."""
    gp = str(game_path or _get_default_game_path() or '').lower()
    key = (gp, int(type_id))
    if key in _3di_cmodel_transit_triangle_cache:
        return _3di_cmodel_transit_triangle_cache[key]
    if not allow_load:
        return None
    try:
        triangles = get_cmodel_collision_triangles_3d(
            type_id, game_path or _get_default_game_path(), allow_load=True, log=log)
        transit = []
        for triangle in triangles or ():
            a, b, c = triangle
            ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            nx = ab[1] * ac[2] - ab[2] * ac[1]
            ny = ab[2] * ac[0] - ab[0] * ac[2]
            nz = ab[0] * ac[1] - ab[1] * ac[0]
            normal_length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if normal_length <= 1e-9:
                continue
            normal_z = abs(nz) / normal_length
            if normal_z < 0.20 or normal_z >= 0.58:
                continue
            footprint_area = abs(
                (b[0] - a[0]) * (c[1] - a[1])
                - (b[1] - a[1]) * (c[0] - a[0])
            ) * 0.5
            if footprint_area <= 0.002:
                continue
            transit.append((a, b, c))
        _3di_cmodel_transit_triangle_cache[key] = transit or None
        return _3di_cmodel_transit_triangle_cache[key]
    except Exception:
        _3di_cmodel_transit_triangle_cache[key] = None
        return None


def get_cmodel_walkable_component_profiles(type_id, game_path=None,
                                            allow_load=True, log=None):
    """Return shared-vertex support-component facts for every triangle."""
    gp = str(game_path or _get_default_game_path() or '').lower()
    key = (gp, int(type_id))
    if key in _3di_walkable_component_profile_cache:
        return _3di_walkable_component_profile_cache[key]
    triangles = get_cmodel_walkable_triangles(
        type_id, game_path, allow_load=allow_load, log=log)
    if not triangles:
        _3di_walkable_component_profile_cache[key] = None
        return None
    collision_policy_recovered = bool(
        _3di_cmodel_collision_policy_recovery_cache.get(key, False))
    from collections import defaultdict as _component_defaultdict
    vertex_rows = _component_defaultdict(list)
    for triangle_index, triangle in enumerate(triangles):
        for point in triangle:
            vertex_rows[tuple(
                round(float(value), 4) for value in point
            )].append(triangle_index)
    remaining = set(range(len(triangles)))
    components = []
    while remaining:
        root = remaining.pop()
        component = {root}
        queue = [root]
        while queue:
            triangle_index = queue.pop()
            for point in triangles[triangle_index]:
                key_point = tuple(
                    round(float(value), 4) for value in point)
                for neighbor_index in vertex_rows[key_point]:
                    if neighbor_index not in remaining:
                        continue
                    remaining.remove(neighbor_index)
                    component.add(neighbor_index)
                    queue.append(neighbor_index)
        component_points = [
            point
            for triangle_index in component
            for point in triangles[triangle_index]
        ]
        component_x = [float(point[0]) for point in component_points]
        component_y = [float(point[1]) for point in component_points]
        component_z = [float(point[2]) for point in component_points]
        footprint_area = 0.0
        for triangle_index in component:
            point_a, point_b, point_c = triangles[triangle_index]
            footprint_area += abs(
                (point_b[0] - point_a[0]) * (point_c[1] - point_a[1])
                - (point_b[1] - point_a[1]) * (point_c[0] - point_a[0])
            ) * 0.5
        components.append({
            'indices': tuple(component),
            'size': len(component),
            'min_x': min(component_x),
            'max_x': max(component_x),
            'min_y': min(component_y),
            'max_y': max(component_y),
            'min_z': min(component_z),
            'max_z': max(component_z),
            'center_x': (min(component_x) + max(component_x)) * 0.5,
            'center_y': (min(component_y) + max(component_y)) * 0.5,
            'center_z': (min(component_z) + max(component_z)) * 0.5,
            'footprint_area': footprint_area,
        })

    tread_candidates = []
    for component_index, component in enumerate(components):
        span_x = component['max_x'] - component['min_x']
        span_y = component['max_y'] - component['min_y']
        z_span = component['max_z'] - component['min_z']
        if (
            2 <= component['size'] <= 16
            and z_span <= 0.25
            and 0.20 <= component['footprint_area'] <= 12.0
            and span_x >= 0.20
            and span_y >= 0.20
        ):
            tread_candidates.append(component_index)

    tread_links = {component_index: set()
                   for component_index in tread_candidates}
    for row, component_index in enumerate(tread_candidates):
        component = components[component_index]
        for other_index in tread_candidates[row + 1:]:
            other = components[other_index]
            dz = abs(component['center_z'] - other['center_z'])
            if dz < 0.10 or dz > 0.80:
                continue
            dx = component['center_x'] - other['center_x']
            dy = component['center_y'] - other['center_y']
            horizontal_advance = math.hypot(dx, dy)
            if horizontal_advance < 0.12 or horizontal_advance > 1.35:
                continue
            gap_x = max(
                0.0,
                component['min_x'] - other['max_x'],
                other['min_x'] - component['max_x'],
            )
            gap_y = max(
                0.0,
                component['min_y'] - other['max_y'],
                other['min_y'] - component['max_y'],
            )
            if math.hypot(gap_x, gap_y) > 0.65:
                continue
            tread_links[component_index].add(other_index)
            tread_links[other_index].add(component_index)

    stair_components = set()
    unvisited_treads = set(tread_candidates)
    while unvisited_treads:
        root = unvisited_treads.pop()
        flight = {root}
        queue = [root]
        while queue:
            component_index = queue.pop()
            for neighbor_index in tread_links[component_index]:
                if neighbor_index not in unvisited_treads:
                    continue
                unvisited_treads.remove(neighbor_index)
                flight.add(neighbor_index)
                queue.append(neighbor_index)
        if len(flight) < 3:
            continue
        flight_z = [components[index]['center_z'] for index in flight]
        flight_x = [components[index]['center_x'] for index in flight]
        flight_y = [components[index]['center_y'] for index in flight]
        if max(flight_z) - min(flight_z) < 0.60:
            continue
        if math.hypot(max(flight_x) - min(flight_x),
                      max(flight_y) - min(flight_y)) < 0.75:
            continue
        stair_components.update(flight)

    profiles = [{'size': 1, 'z_span': 0.0}] * len(triangles)
    for component_index, component in enumerate(components):
        component_profile = {
            'component_id': component_index,
            'size': component['size'],
            'z_span': component['max_z'] - component['min_z'],
            'footprint_area': component['footprint_area'],
            'span_x': component['max_x'] - component['min_x'],
            'span_y': component['max_y'] - component['min_y'],
            'geometry_stair_sequence_candidate': bool(
                component_index in stair_components),
            'stair_sequence_candidate': bool(
                collision_policy_recovered
                and component_index in stair_components),
        }
        for triangle_index in component['indices']:
            profiles[triangle_index] = component_profile
    _3di_walkable_component_profile_cache[key] = tuple(profiles)
    return _3di_walkable_component_profile_cache[key]


def get_cmodel_segments_3d_grouped(type_id, game_path=None, allow_load=True, log=None):
    """Return grouped true compact/CModel 3D line records for a type_id."""
    gp = str(game_path or _get_default_game_path() or '').lower()
    key = (gp, int(type_id))
    if key in _3di_cmodel3d_grouped_cache:
        return _3di_cmodel3d_grouped_cache[key]
    if not allow_load:
        return None
    try:
        raw, item = _get_3di_bytes_for_type(type_id, game_path or _get_default_game_path(), log=log, pff_cache=_get_pff_cache())
        if not raw:
            _3di_cmodel3d_grouped_cache[key] = None
            return None
        graphic = None
        try:
            graphic = (item or {}).get('graphic') or (item or {}).get('model') or (item or {}).get('name')
        except Exception:
            pass
        recs = _parse_gpm_cmodel_3d_grouped(raw, debug_name=f'{type_id}/{graphic}', dbg=_dbg)
        _3di_cmodel3d_grouped_cache[key] = recs
        return recs
    except Exception:
        _3di_cmodel3d_grouped_cache[key] = None
        return None


def _cmodel_type_allowed(type_id, game_path=None, log=None):
    """Return True if this type is worth loading as fixed collision geometry."""
    gp = Path(game_path) if game_path else (_get_default_game_path() if _get_default_game_path() else None)
    if not gp:
        return True
    key = (str(gp).lower(), int(type_id))
    cached = _cmodel_allowed_cache.get(key)
    if cached is not None:
        return cached
    try:
        item = _load_items_def(gp, log=log, pff_cache=_get_pff_cache()).get(int(type_id))
        allowed = (classify_item_collision_hint(item) == 'candidate')
    except Exception:
        allowed = True
    _cmodel_allowed_cache[key] = allowed
    return allowed


def _cmodel_category_for_type(type_id, game_path=None):
    """Return items.def category for a type, cached through _load_items_def."""
    try:
        gp = Path(game_path) if game_path else (_get_default_game_path() if _get_default_game_path() else None)
        if not gp:
            return ''
        item = _load_items_def(gp, log=None, pff_cache=_get_pff_cache()).get(int(type_id), {})
        return str(item.get('category') or '').lower()
    except Exception:
        return ''


CMODEL_RPX_FULL_BUILDING    = 150
CMODEL_RPX_FULL_OBJECT      = 150
CMODEL_RPX_FULL_HEAVY       = 150
CMODEL_RPX_MEDIUM           = 80
CMODEL_RPX_MARKER_MIN       = 3
CMODEL_MED_CULL_MARGIN_PX   = 320


def _segments_draw_lod_rpx(segs, rpx, type_id=None):
    """Projected-size LOD without ugly bbox boxes."""
    if not segs:
        return segs
    from config.render_config import (ENTITY_RENDER_MODE, FOLIAGE_HULL_ONLY,
        STABLE_ENTITY_OUTLINES, CMODEL_HUSK_SMALL_SEG_LIMIT,
        CMODEL_HEAVY_SEG_THRESHOLD)
    mode = str(ENTITY_RENDER_MODE).lower()
    if mode == 'markers_only':
        return []
    cat = _cmodel_category_for_type(type_id)
    if cat == 'foliage' and bool(FOLIAGE_HULL_ONLY):
        return _foliage_hull_segments(segs)
    if mode in ('full_collision', 'stable_full') or bool(STABLE_ENTITY_OUTLINES):
        return segs
    try:
        n = len(segs)
        if n <= int(CMODEL_HUSK_SMALL_SEG_LIMIT):
            return segs
        if cat == 'building':
            return segs
        heavy_n = int(CMODEL_HEAVY_SEG_THRESHOLD)
        if n >= heavy_n:
            full_thresh = float(CMODEL_RPX_FULL_HEAVY)
        else:
            full_thresh = float(CMODEL_RPX_FULL_OBJECT)
        med_thresh = float(CMODEL_RPX_MEDIUM)
        marker_min = float(CMODEL_RPX_MARKER_MIN)
        if rpx >= full_thresh:
            return segs
        husk = _cmodel_husk_segments(segs)
        if rpx >= med_thresh and husk:
            return husk
        if n < heavy_n and rpx >= marker_min:
            return segs
        return []
    except Exception:
        return segs


_get_foliage_trees_only = lambda: True

def _is_tree_like_foliage_entity(type_id, segs=None, trees_only=None):
    """Heuristic: show trees/groves, hide bush/shrub-like foliage."""
    # Renderers may own the toggle as instance state.  Accept that value
    # explicitly instead of forcing every caller through the module callback.
    # The callback remains the default for generator/legacy callers.
    if trees_only is None:
        trees_only = _get_foliage_trees_only()
    if not bool(trees_only):
        return True
    try:
        item = None
        gp = _get_default_game_path()
        if gp:
            item = _load_items_def(gp, pff_cache=_get_pff_cache()).get(int(type_id))
        names = []
        if item:
            for k in ('graphic', 'model', 'name', 'description'):
                v = item.get(k)
                if v:
                    names.append(str(v).lower())
        joined = ' '.join(names)
        bush_words = ('bush', 'shrub', 'grass', 'weed', 'fern', 'plant', 'flower')
        tree_words = ('tree', 'palm', 'trunk', 'jungle', 'forest', 'grove', 'banana')
        if any(w in joined for w in tree_words):
            return True
        if any(w in joined for w in bush_words):
            return False
        if segs:
            b = _foliage_bounds_from_segments(segs)
            if b:
                mnx, mny, mxx, mxy = b
                span = max(mxx - mnx, mxy - mny)
                return span >= 6.5
    except Exception:
        pass
    return True
