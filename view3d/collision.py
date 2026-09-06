"""3D collision-face caches and blocked-edge classification.

The view instance is supplied explicitly; this module does not import the
application class or own Tk state.
"""

import math

import view3d.wf3d_debug as _wf3d_debug_mod
from cmodel.cmodel_access import get_cmodel_collision_triangles_3d


def entity_cache_key(view, e, suffix=None):
    """Stable-ish key for camera-independent world-space entity geometry."""
    try:
        tid = int(e.get("type_id", 0))
    except Exception:
        tid = 0
    key = (
        suffix,
        tid,
        e.get("_wf_kind"),
        round(float(e.get("x", 0.0)), 3),
        round(float(e.get("y", 0.0)), 3),
        round(float(e.get("z", 0.0)), 3),
        round(float(e.get("heading", e.get("rot", e.get("yaw", 0.0))) or 0.0), 3),
        round(float(e.get("pitch", 0.0) or 0.0), 3),
        _wf3d_debug_mod.WF3D_RENDER_CACHE_VERSION,
    )
    return key


def collision_triangles_for_entity(view, e, game_path=None):
    """Return cached world-space true collision triangles for one scene entity."""
    try:
        tid = int(e.get("type_id", 0))
        gp = game_path
        key = view._entity_cache_key(e, suffix=(
            "edge_collision_triangles",
            str(gp or '').lower(),
            bool(view._allow_underground_geometry()),
        ))
        cache = getattr(view, '_wf_edge_collision_tri_cache', None)
        if not isinstance(cache, dict):
            cache = {}
            view._wf_edge_collision_tri_cache = cache
        if key in cache:
            return cache[key]

        # This is scene-build work, never camera-render work. allow_load=True
        # guarantees that a wall shown through the normal CModel path can be
        # classified even if the dedicated triangle cache was not warm yet.
        local = get_cmodel_collision_triangles_3d(
            tid, gp, allow_load=True, log=None) or []
        if not local:
            cache[key] = []
            return []

        ex = float(e.get("x", 0.0)); ey = float(e.get("y", 0.0))
        base_z = view._entity_base_z(e, ex, ey)
        heading = float(e.get("heading", e.get("rot", e.get("yaw", 0.0))) or 0.0)
        ang = math.radians(-round(heading))
        ch = math.cos(ang); sh = math.sin(ang)
        pitch = float(e.get("pitch", 0.0) or 0.0)
        pitch_ang = math.radians(pitch)
        cp = math.cos(pitch_ang); sp = math.sin(pitch_ang)

        world = []
        for tri in local:
            if not isinstance(tri, (list, tuple)) or len(tri) < 3:
                continue
            out_tri = []
            for pt in tri[:3]:
                if not isinstance(pt, (list, tuple)) or len(pt) < 3:
                    out_tri = []
                    break
                x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
                y, z = y*cp - z*sp, y*sp + z*cp
                rx = x*ch - y*sh
                ry = x*sh + y*ch
                out_tri.append((ex+rx, ey+ry, base_z+z))
            if len(out_tri) == 3:
                world.append(tuple(out_tri))
        cache[key] = world
        view._trim_3d_world_caches()
        return world
    except Exception:
        return []


def build_edge_collision_index(view, entities, game_path=None):
    """Build/reuse a small XY spatial index of scene CModel collision faces."""
    entities = list(entities or [])
    try:
        entity_key = tuple(sorted(
            repr(view._entity_cache_key(e, suffix=(
                "edge_collision_index_entity",
                str(game_path or '').lower(),
                bool(view._allow_underground_geometry()),
            ))) for e in entities))
    except Exception:
        entity_key = None

    cached = getattr(view, '_wf_edge_collision_index', None)
    if isinstance(cached, dict) and cached.get('key') == entity_key:
        return cached

    cell = 4.0
    triangles = []
    grid = {}
    for e in entities:
        for tri in view._wf_collision_triangles_for_entity(e):
            try:
                xs = (tri[0][0], tri[1][0], tri[2][0])
                ys = (tri[0][1], tri[1][1], tri[2][1])
                zs = (tri[0][2], tri[1][2], tri[2][2])
                bbox = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
                ti = len(triangles)
                triangles.append((tri, bbox))
                gx0 = int(math.floor(bbox[0] / cell)); gx1 = int(math.floor(bbox[1] / cell))
                gy0 = int(math.floor(bbox[2] / cell)); gy1 = int(math.floor(bbox[3] / cell))
                for gx in range(gx0, gx1 + 1):
                    for gy in range(gy0, gy1 + 1):
                        grid.setdefault((gx, gy), []).append(ti)
            except Exception:
                continue

    index = {'key': entity_key, 'cell': cell, 'triangles': triangles, 'grid': grid}
    view._wf_edge_collision_index = index
    return index


def wf_edge_is_blocked(view, a, b, index):
    """Classify one nav edge against the scene's indexed true collision."""
    try:
        pa = (float(a.x), float(a.y), float(a.z))
        pb = (float(b.x), float(b.y), float(b.z))
        cell = float(index.get('cell', 4.0))
        triangles = index.get('triangles') or []
        grid = index.get('grid') or {}
        if not triangles:
            return False

        minx, maxx = sorted((pa[0], pb[0])); miny, maxy = sorted((pa[1], pb[1]))
        minz, maxz = sorted((pa[2], pb[2]))
        gx0 = int(math.floor(minx / cell)); gx1 = int(math.floor(maxx / cell))
        gy0 = int(math.floor(miny / cell)); gy1 = int(math.floor(maxy / cell))
        candidate_ids = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                candidate_ids.update(grid.get((gx, gy), ()))

        for ti in candidate_ids:
            tri, bbox = triangles[ti]
            if (bbox[1] < minx or bbox[0] > maxx or
                    bbox[3] < miny or bbox[2] > maxy or
                    bbox[5] < minz or bbox[4] > maxz):
                continue
            if view._wf_segment_hits_triangle(pa, pb, tri):
                return True
        return False
    except Exception:
        return False


def refresh_blocked_edge_cache(view):
    """Reclassify the current visible nav edges; safe to call after edits."""
    blocked = set()
    try:
        scene = view.scene or {}
        edges = list(scene.get('edges') or [])
        index = view._wf_build_edge_collision_index(scene.get('entities') or [])
        for a, b, _both_selected in edges:
            try:
                aid = int(getattr(a, 'id'))
                bid = int(getattr(b, 'id'))
            except Exception:
                continue
            if view._wf_edge_is_blocked(a, b, index):
                blocked.add((aid, bid) if aid < bid else (bid, aid))
    except Exception:
        blocked = set()
    view._wf_blocked_edges = blocked
    return blocked


def trim_3d_world_caches(view):
    """Bound per-view world-line caches so long editing sessions stay sane."""
    try:
        vc = getattr(view, "_wf_visual_world_cache", None)
        if isinstance(vc, dict) and len(vc) > 512:
            for k in list(vc.keys())[:128]:
                vc.pop(k, None)
    except Exception:
        pass
    try:
        cc = getattr(view, "_wf_cmodel_world_cache", None)
        if isinstance(cc, dict) and len(cc) > 512:
            for k in list(cc.keys())[:128]:
                cc.pop(k, None)
    except Exception:
        pass
    try:
        tc = getattr(view, "_wf_cmodel_triangle_cache", None)
        if isinstance(tc, dict) and len(tc) > 256:
            for k in list(tc.keys())[:64]:
                tc.pop(k, None)
    except Exception:
        pass
    try:
        ec = getattr(view, "_wf_edge_collision_tri_cache", None)
        if isinstance(ec, dict) and len(ec) > 256:
            for k in list(ec.keys())[:64]:
                ec.pop(k, None)
    except Exception:
        pass
