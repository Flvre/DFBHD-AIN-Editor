"""3D render-layer helpers.

The view object is supplied explicitly; the stateful render loop remains in
wf3d_view.py and calls these helpers through compatibility wrappers.
"""

import math
import os
import sys
import tkinter as tk
from PIL import ImageFont

from cmodel.cmodel_access import get_cmodel_segments_3d, get_cmodel_segments_3d_grouped
from cmodel.cmodel_geom import _rotated_segments_for_heading
from config.editor_config import _load_editor_cfg
from config.render_config import CMODEL_DEDUP_EDGES
from entity.entity_data import OBSTACLE_RADII
from shared.nav_helpers import NAV_NODE_Z_LIFT
from terrain.terrain import _terrain_water_height_world
import view3d.wf3d_debug as _wf3d_debug_mod
from view3d.wf3d_debug import _wf3d_dbg

def draw_lines_batch(view, draw, lines_iter):
    """Fast bulk line draw — bypasses per-line _line() overhead.

    lines_iter yields (a, b, color, width) tuples where a/b are
    world-space (x,y,z) tuples.  All budget/cull/scale state is read
    once up front and applied with inlined arithmetic.
    """
    # Snapshot projection constants
    tx, ty, tz = view._proj_target
    cy = view._proj_cy; sy = view._proj_sy
    cp = view._proj_cp; sp = view._proj_sp
    cw = view._proj_cw; ch = view._proj_ch
    scale = view.scale
    pan_x = view.pan_x; pan_y = view.pan_y
    hcw = cw * 0.5 + pan_x
    hch = ch * 0.5 + pan_y

    # Budget
    budget = getattr(view, "_draw_line_budget", None)
    budget_active = getattr(view, "_draw_budget_active", False)
    count = getattr(view, "_draw_line_count", 0)
    skipped_budget = getattr(view, "_draw_line_skipped_budget", 0)
    skipped_cull = getattr(view, "_draw_line_skipped_cull", 0)

    # Cull / scale
    try:
        do_cull = bool(view.perf_offscreen_cull.get())
    except Exception:
        do_cull = True
    m = 256
    cw_m = cw + m; ch_m = ch + m
    neg_m = -m
    try:
        coord_scale = float(getattr(view, "_draw_coord_scale", 1.0) or 1.0)
    except Exception:
        coord_scale = 1.0
    try:
        width_scale = float(getattr(view, "_draw_width_scale", 1.0) or 1.0)
    except Exception:
        width_scale = 1.0
    do_scale = abs(coord_scale - 1.0) > 1e-9

    dl = draw.line
    section_rng = view._section_range()
    section_xy = view._section_xy_range()
    section_any = (section_rng is not None or section_xy is not None)
    fit_capture = getattr(view, "_fit_capture_bounds", None) is not None

    if section_rng is not None:
        _z0, _z1 = section_rng
    else:
        _z0 = _z1 = None
    if section_xy is not None:
        _x0, _x1, _y0, _y1 = section_xy
    else:
        _x0 = _x1 = _y0 = _y1 = None

    for a, b, color, width in lines_iter:
        if section_any:
            outside = False
            inside = True

            if section_xy is not None:
                if ((a[0] < _x0 and b[0] < _x0) or
                    (a[0] > _x1 and b[0] > _x1) or
                    (a[1] < _y0 and b[1] < _y0) or
                    (a[1] > _y1 and b[1] > _y1)):
                    outside = True
                inside = inside and (
                    _x0 <= a[0] <= _x1 and _x0 <= b[0] <= _x1 and
                    _y0 <= a[1] <= _y1 and _y0 <= b[1] <= _y1)

            if not outside and section_rng is not None:
                if ((a[2] < _z0 and b[2] < _z0) or
                    (a[2] > _z1 and b[2] > _z1)):
                    outside = True
                inside = inside and (
                    _z0 <= a[2] <= _z1 and _z0 <= b[2] <= _z1)

            if outside:
                continue
            if not inside:
                clipped = view._clip_segment_to_section(a, b, section_rng)
                if clipped is None:
                    continue
                a, b = clipped
        # Apply the same still-frame line budget before Fit capture.  This
        # keeps capture order identical to raster drawing: once the visible
        # frame has exhausted its geometry budget, later context lines must not
        # silently enlarge Fit bounds.
        if budget_active and budget is not None and count >= budget:
            skipped_budget += 1
            break
        if fit_capture:
            view._fit_capture_point(a)
            view._fit_capture_point(b)
            count += 1
            continue

        # Inline _project for point a
        ax = a[0] - tx; ay_w = a[1] - ty; az = a[2] - tz
        x1a = cy * ax - sy * ay_w
        y1a = sy * ax + cy * ay_w
        y2a = cp * y1a - sp * az
        z2a = sp * y1a + cp * az
        sax = hcw + x1a * scale
        say = hch - z2a * scale

        # Inline _project for point b
        bx = b[0] - tx; by_w = b[1] - ty; bz = b[2] - tz
        x1b = cy * bx - sy * by_w
        y1b = sy * bx + cy * by_w
        y2b = cp * y1b - sp * bz
        z2b = sp * y1b + cp * bz
        sbx = hcw + x1b * scale
        sby = hch - z2b * scale

        # Offscreen cull
        if do_cull:
            if (sax < neg_m and sbx < neg_m) or (sax > cw_m and sbx > cw_m) or \
               (say < neg_m and sby < neg_m) or (say > ch_m and sby > ch_m):
                skipped_cull += 1
                continue

        if do_scale:
            sax *= coord_scale; say *= coord_scale
            sbx *= coord_scale; sby *= coord_scale
        rw = max(1, int(round(width * width_scale)))
        dl((sax, say, sbx, sby), fill=color, width=rw)
        count += 1

    view._draw_line_count = count
    view._draw_line_skipped_budget = skipped_budget
    view._draw_line_skipped_cull = skipped_cull


def draw_grid(view, draw):
    mode = str(getattr(view, "ground_mode", tk.StringVar(value="Grid")).get() or "Grid")
    if mode == "Off" or not view.show_grid.get():
        return
    # Motion LOD keeps the terrain grid when the camera is still,
    # but it costs many terrain samples + projected lines every drag frame.
    # Keep the solid plane as depth reference; suppress the line grid while
    # orbiting/panning.
    if view._motion_lod_active():
        if mode in ("Grid", "Cutaway"):
            return
        if mode == "Solid + Grid":
            mode = "Solid"
    cutaway = (mode == "Cutaway")
    # A filled ground polygon cannot be meaningfully slab-clipped by the line
    # renderer. In Section mode keep only terrain lines, which are clipped
    # correctly against the active Z range.
    if view._section_is_enabled():
        if mode == "Solid":
            return
        if mode == "Solid + Grid":
            mode = "Grid"
    nodes = view.scene.get("nodes") or []
    if nodes:
        cx = sum(n.x for n in nodes) / len(nodes)
        cy = sum(n.y for n in nodes) / len(nodes)
    else:
        cx, cy, _ = view.target

    bounds = (view.scene or {}).get("bounds")
    if bounds:
        bx_half = (bounds[3] - bounds[0]) * 0.5
        by_half = (bounds[4] - bounds[1]) * 0.5
        radius = max(20.0, max(bx_half, by_half) + 10.0)
    else:
        radius = max(40.0, float(view.context_radius.get() or 55.0) + 20.0)
    step = 8.0
    if radius > 90:
        step = 16.0
    x0 = math.floor((cx - radius) / step) * step
    x1 = math.ceil((cx + radius) / step) * step
    y0 = math.floor((cy - radius) / step) * step
    y1 = math.ceil((cy + radius) / step) * step

    # Water is terrain context, not entity geometry. Draw a coarse flat
    # surface only above terrain cells that are actually submerged.
    _water_z = _terrain_water_height_world(
        getattr(view.app, 'terrain_info', None))
    _show_water = True
    try:
        _show_water = bool(view.app.show_water.get())
    except Exception:
        pass
    if _water_z is not None and _show_water:
        _draw_water = True
        if view._section_is_enabled():
            _section_rng = view._section_range()
            if (_section_rng is not None and
                    not (_section_rng[0] <= _water_z <= _section_rng[1])):
                _draw_water = False
        if _draw_water:
            try:
                _water_step = max(step, 12.0)
                _wx = math.floor(x0 / _water_step) * _water_step
                _water_fill = (18, 48, 66)
                _water_edge = (30, 84, 108)
                while _wx < x1 - 0.001:
                    _wy = math.floor(y0 / _water_step) * _water_step
                    while _wy < y1 - 0.001:
                        _cx = _wx + _water_step * 0.5
                        _cy = _wy + _water_step * 0.5
                        if view._terrain_z(_cx, _cy) < float(_water_z) - 0.05:
                            _corners = [
                                (_wx, _wy, _water_z),
                                (_wx + _water_step, _wy, _water_z),
                                (_wx + _water_step, _wy + _water_step, _water_z),
                                (_wx, _wy + _water_step, _water_z),
                            ]
                            _pts = [view._project(*_p)[:2] for _p in _corners]
                            draw.polygon(_pts, fill=_water_fill)
                            draw.line(_pts + [_pts[0]], fill=_water_edge, width=1)
                        _wy += _water_step
                    _wx += _water_step
            except Exception:
                pass

    # Draw a very low-priority ground plane first. It exists for depth/scale,
    # not as a main visual element.
    if mode in ("Solid", "Solid + Grid"):
        try:
            corners3 = [
                (x0, y0, view._terrain_z(x0, y0)),
                (x1, y0, view._terrain_z(x1, y0)),
                (x1, y1, view._terrain_z(x1, y1)),
                (x0, y1, view._terrain_z(x0, y1)),
            ]
            pts = []
            for p in corners3:
                sx, sy, _ = view._project(*p)
                pts.append((sx, sy))
            draw.polygon(pts, fill=getattr(view, "GROUND_SOLID", (8,8,7)))
            draw.line(pts + [pts[0]], fill=getattr(view, "GROUND_SOLID_EDGE", (26,24,18)), width=1)
        except Exception:
            pass
        if mode == "Solid":
            return

    # Under-view color hint; muted by design, not MED-bright green.
    grid_col = (38, 58, 62) if cutaway else view.GRID
    if math.degrees(view.pitch) < -5:
        grid_col = (32, 68, 48) if cutaway else (40, 80, 50)

    xi = x0
    while xi <= x1 + 0.001:
        pts = []
        yi = y0
        while yi <= y1 + 0.001:
            pts.append((xi, yi, view._terrain_z(xi, yi)))
            yi += step
        for a, b in zip(pts, pts[1:]):
            col = view.GRID_AXIS_Y if abs(xi - cx) < 0.01 else grid_col
            view._line(draw, a, b, col, 1)
        xi += step

    yi = y0
    while yi <= y1 + 0.001:
        pts = []
        xi = x0
        while xi <= x1 + 0.001:
            pts.append((xi, yi, view._terrain_z(xi, yi)))
            xi += step
        for a, b in zip(pts, pts[1:]):
            col = view.GRID_AXIS_X if abs(yi - cy) < 0.01 else grid_col
            view._line(draw, a, b, col, 1)
        yi += step


def draw_cmodel_overlay_for_entity(view, draw, e, col=None, allow_2d_fallback=True,
                                    group_filter=..., group_mode=...,
                                    render_color_override=None):
    _keep_motion_cmodel = view._keep_cmodel_blocks_during_motion_active()
    if _keep_motion_cmodel and e.get("_wf_kind") == "decoration":
        if not view._keep_decorations_during_motion_active():
            _keep_motion_cmodel = False
    if view._line_budget_exhausted() and not _keep_motion_cmodel:
        return 0
    """Draw true compact/CModel 3D geometry over the visual render mesh.

    The renderer uses cached world-space CModel lines.  Camera movement still
    projects/draws each line, but it no longer has to rebuild the CModel
    transform/filter/color list every frame.
    """
    try:
        tid = int(e.get("type_id", 0))
        lines, meta = view._cmodel_world_lines_for_entity(
            e, col=col, allow_2d_fallback=allow_2d_fallback,
            group_filter=group_filter, group_mode=group_mode)
        if not lines:
            return 0

        detail_mode = view._selected_cmodel_detail_mode()
        motion_lod = view._motion_lod_active()
        if _keep_motion_cmodel:
            motion_lod = False
        elif (motion_lod and e.get("_wf_kind") == "decoration" and
                view._keep_decorations_during_motion_active()):
            motion_lod = False
        dim_color = view._dim_color

        def _styled_lines():
            for rec in lines:
                if len(rec) >= 5:
                    a, b, c, _gi, importance = rec
                else:
                    a, b, c, _gi = rec
                    importance = "raw"
                imp = str(importance or "raw").lower()
                display_c = render_color_override if render_color_override is not None else c
                if detail_mode == "Raw Wire":
                    yield a, b, display_c, 1
                    continue
                important = imp in ("boundary", "crease")
                if important:
                    yield a, b, display_c, (1 if motion_lod else 2)
                    continue
                if detail_mode == "Structure":
                    continue
                if motion_lod:
                    continue
                ghost = 0.22 if imp == "noise" else 0.34
                yield a, b, dim_color(display_c, ghost), 1

        before = getattr(view, "_draw_line_count", 0)
        _old_budget_active = getattr(view, '_draw_budget_active', False)
        if _keep_motion_cmodel:
            # CModel-only exception: preserve the normal drag preview for
            # visual meshes/nodes/etc., but do not let its temporary line
            # cap erase collision blocks that the user asked to keep.
            view._draw_budget_active = False
        try:
            view._draw_lines_batch(draw, _styled_lines())
        finally:
            view._draw_budget_active = _old_budget_active
        count = getattr(view, "_draw_line_count", 0) - before

        if _wf3d_debug_mod.WF3D_DEBUG_ENABLED:
            group_counts = meta.get("groups") or {}
            if group_counts:
                summary = ', '.join(f'{k}:{v}' for k, v in sorted(group_counts.items())[:16])
                more = '...' if len(group_counts) > 16 else ''
                _wf3d_dbg(
                    f"ENTITY3D: type={tid} CMODEL3D_OVERLAY cached_world drawn_lines={count} "
                    f"groups={len(group_counts)} [{summary}{more}] "
                    f"filter={view._selected_cmodel_group() if view._selected_cmodel_group() is not None else 'All'} "
                    f"mode={view._selected_cmodel_group_mode()} "
                    f"zrange=({float(meta.get('zmin',0.0)):.2f},{float(meta.get('zmax',0.0)):.2f})"
                )
            else:
                _wf3d_dbg(
                    f"ENTITY3D: type={tid} CMODEL3D_OVERLAY cached_world drawn_lines={count} "
                    f"zrange=({float(meta.get('zmin',0.0)):.2f},{float(meta.get('zmax',0.0)):.2f})"
                )
        return count
    except Exception as exc:
        if _wf3d_debug_mod.WF3D_DEBUG_ENABLED:
            _wf3d_dbg(f"ENTITY3D: CMODEL3D_OVERLAY exception type={e.get('type_id','?')}: {exc}")
        return 0


def cmodel_world_lines_for_entity(view, e, col=None, allow_2d_fallback=True, game_path=None, cmodel_segments_fn=None,
                                   group_filter=..., group_mode=...):
    """Return cached world-space CModel lines as (a, b, color, group_index).

    This is the world-space geometry cache pass.  The expensive parts here are not camera
    dependent: choosing grouped CModel data, applying the entity heading,
    anchoring to entity origin Z, filtering Addr blocks, and assigning
    colors.  Cache those world-space lines so orbit/pan/zoom only projects
    and draws them.
    """
    try:
        tid = int(e.get("type_id", 0))
        gp = game_path
        if group_filter is ...:
            group_filter = view._selected_cmodel_group()
        if group_mode is ...:
            group_mode = view._selected_cmodel_group_mode()
        color_mode = str(getattr(view, "cmodel_color_mode", tk.StringVar(value="Source")).get() or "Source")
        default_c = col or view.CMODEL_OVERLAY
        _gf_key = frozenset(group_filter) if isinstance(group_filter, (set, frozenset)) else (group_filter if group_filter is not None else "All")
        key = view._entity_cache_key(e, suffix=(
            "cmodel_world",
            _gf_key,
            group_mode,
            color_mode,
            view._selected_cmodel_detail_mode(),
            bool(view._allow_underground_geometry()),
            bool(allow_2d_fallback),
            default_c,
        ))
        cache = getattr(view, "_wf_cmodel_world_cache", None)
        if isinstance(cache, dict) and key in cache and not _wf3d_debug_mod.WF3D_DEBUG_ENABLED:
            view._wf_cache_hits += 1
            return cache[key]
        view._wf_cache_misses += 1

        use_groups = (color_mode == "Addr Block") or (group_filter is not None)
        grouped = None
        if use_groups:
            grouped = get_cmodel_segments_3d_grouped(tid, gp, allow_load=True, log=None)

        segs3 = None
        if grouped:
            segs3 = [(rec[1], rec[2]) for rec in grouped if len(rec) >= 3]
        else:
            segs3 = get_cmodel_segments_3d(tid, gp, allow_load=True, log=None)

        ex = float(e.get("x", 0.0)); ey = float(e.get("y", 0.0))
        base_z = view._entity_base_z(e, ex, ey)

        out = []
        meta = {"fallback": False, "groups": {}, "zmin": 0.0, "zmax": 0.0, "raw": 0}

        if not segs3:
            # A 2D footprint has no address-block provenance and becomes a
            # flat floor drawing when projected in the 3D view. Never use it
            # while isolating one CModel block, and let callers suppress it
            # entirely for decorations/vehicles.
            isolate_group = (group_filter is not None and group_mode in ("Only", "Solo"))
            if not allow_2d_fallback or isolate_group:
                result = (out, meta)
                if isinstance(cache, dict):
                    cache[key] = result
                return result
            if _wf3d_debug_mod.WF3D_DEBUG_ENABLED:
                _wf3d_dbg(f"ENTITY3D: type={tid} CMODEL3D_OVERLAY no true 3D CModel; falling back to 2D footprint")
            segs2 = cmodel_segments_fn(tid, gp, allow_load=True, log=None)
            if not segs2:
                result = (out, meta)
                if isinstance(cache, dict):
                    cache[key] = result
                return result
            heading = float(e.get("heading", e.get("rot", e.get("yaw", 0.0))) or 0.0)
            rot = _rotated_segments_for_heading(segs2, heading)
            c = default_c
            for a, b in rot:
                out.append(((ex+a[0], ey+a[1], base_z), (ex+b[0], ey+b[1], base_z), c, None, "raw"))
            meta["fallback"] = True
            meta["raw"] = len(rot)
            result = (out, meta)
            if isinstance(cache, dict):
                cache[key] = result
                view._trim_3d_world_caches()
            return result

        heading = float(e.get("heading", e.get("rot", e.get("yaw", 0.0))) or 0.0)
        import math as _math
        ang = _math.radians(-round(heading))
        ch = _math.cos(ang); sh = _math.sin(ang)
        pitch = float(e.get("pitch", 0.0) or 0.0)
        pitch_ang = _math.radians(pitch)
        cp = _math.cos(pitch_ang); sp = _math.sin(pitch_ang)

        if grouped:
            iterable = []
            zvals = []
            for rec in grouped:
                if len(rec) >= 4:
                    gi, a, b, imp = rec[0], rec[1], rec[2], rec[3]
                elif len(rec) >= 3:
                    gi, a, b, imp = rec[0], rec[1], rec[2], "raw"
                else:
                    continue
                iterable.append((gi, a, b, imp))
                zvals.extend([float(a[2]), float(b[2])])
        else:
            iterable = [(None, a, b, "raw") for a, b in segs3]
            zvals = [float(p[2]) for a, b in segs3 for p in (a, b)]
        meta["zmin"] = min(zvals) if zvals else 0.0
        meta["zmax"] = max(zvals) if zvals else 0.0
        meta["raw"] = len(iterable) if hasattr(iterable, "__len__") else 0

        group_counts = {}
        _gf_is_set = isinstance(group_filter, (set, frozenset))
        for gi, a, b, importance in iterable:
            if group_filter is not None:
                if gi is None:
                    if group_mode in ("Only", "Solo"):
                        continue
                else:
                    if _gf_is_set:
                        in_visible = int(gi) in group_filter
                    else:
                        in_visible = int(gi) == int(group_filter)
                    if group_mode in ("Only", "Solo") and not in_visible:
                        continue
                    if group_mode == "Hide" and in_visible:
                        continue
            ax0, ay0, az0 = float(a[0]), float(a[1]), float(a[2])
            bx0, by0, bz0 = float(b[0]), float(b[1]), float(b[2])
            # Entity pitch rotates local depth/height around local X before
            # heading rotates the result around world vertical.
            ay0, az0 = ay0*cp - az0*sp, ay0*sp + az0*cp
            by0, bz0 = by0*cp - bz0*sp, by0*sp + bz0*cp
            ax = ax0*ch - ay0*sh; ay = ax0*sh + ay0*ch
            bx = bx0*ch - by0*sh; by = bx0*sh + by0*ch
            c = view._group_palette_color(gi) if (color_mode == "Addr Block" and gi is not None) else default_c
            out.append(((ex+ax, ey+ay, base_z+az0), (ex+bx, ey+by, base_z+bz0), c, gi, importance))
            if gi is not None:
                group_counts[int(gi)] = group_counts.get(int(gi), 0) + 1
        meta["groups"] = group_counts
        result = (out, meta)
        if isinstance(cache, dict):
            cache[key] = result
            view._trim_3d_world_caches()
        return result
    except Exception as exc:
        if _wf3d_debug_mod.WF3D_DEBUG_ENABLED:
            _wf3d_dbg(f"ENTITY3D: CMODEL3D_WORLD_CACHE exception type={e.get('type_id','?')}: {exc}")
        return ([], {"fallback": False, "groups": {}, "zmin": 0.0, "zmax": 0.0, "raw": 0})


def draw_entities(view, draw):
    entities = view.scene.get("entities") or []
    _wf3d_dbg(f"DRAW_ENTITIES: scene entities={len(entities)} show_buildings={view.show_buildings.get()} show_vehicles={view.show_vehicles.get()} show_decor={view.show_decorations.get()}")
    _drawn = 0
    _marker_boxes = 0
    _line_total = 0
    model_width = view._model_line_width_px()
    # Two passes: buildings/foliage first, then decorations/vehicles.
    # The second pass stays inside the normal line budget; some decorations
    # have very dense collision data and must not bypass the performance guard.
    _deferred_decor = []
    _group_filter = view._selected_cmodel_group()
    _group_mode = view._selected_cmodel_group_mode()
    _gf_is_set = isinstance(_group_filter, (set, frozenset))
    _group_isolation = (_group_filter is not None and (_gf_is_set or _group_mode in ("Only", "Solo")))
    _solo_mode = view._cmodel_solo_active()
    _focus_key = view._tunnel_focus_key()
    _show_buildings = view.show_buildings.get()
    _show_vehicles = view.show_vehicles.get()
    _show_decorations = view.show_decorations.get()
    _show_foliage = view.show_foliage.get()
    _show_visual = bool(getattr(view, "show_visual_mesh", tk.BooleanVar(value=True)).get())
    _show_collision = bool(getattr(view, "show_cmodel_overlay", tk.BooleanVar(value=False)).get())
    # One boolean + one RGB tuple for the whole frame. The override is then
    # consumed only by the final draw generators below; no geometry cache
    # key contains Node Focus state or color.
    try:
        _node_focus_active = bool(view.node_focus_mode.get())
    except Exception:
        _node_focus_active = False
    _node_focus_col = tuple(getattr(view, '_node_focus_rgb', (74, 64, 54)))
    _tunnel_draw = str(getattr(view, "tunnel_draw_mode", tk.StringVar(value="Normal")).get() or "Normal")
    _keep_motion_decor = view._keep_decorations_during_motion_active()
    _keep_motion_cmodel = view._keep_cmodel_blocks_during_motion_active()
    for e in entities:
        kind = e.get("_wf_kind")
        _over_budget_keep_cmodel = False
        if view._line_budget_exhausted():
            # Decorations go through their own gate first: kept whole
            # (visual + CModel) when deco-keep is on, skipped entirely
            # when it's off — the CModel-only path is for buildings.
            if kind == "decoration":
                if _keep_motion_decor:
                    pass
                else:
                    continue
            elif _keep_motion_cmodel and _show_collision:
                _over_budget_keep_cmodel = True
            elif _keep_motion_decor:
                continue
            else:
                break
        if kind == "building" and not _show_buildings:
            continue
        if kind == "vehicle" and not _show_vehicles:
            continue
        if kind == "decoration" and not _show_decorations:
            continue
        if kind == "foliage" and not _show_foliage:
            continue
        if _focus_key is not None and not view._entity_matches_tunnel_key(e, _focus_key):
            continue
        col = view.BUILDING if kind == "building" else (view.VEHICLE if kind == "vehicle" else view.DECOR)
        model_col = view._model_line_color(col)
        is_tunnel = view._is_tunnel_entity(e)
        tdraw = _tunnel_draw if is_tunnel else "Normal"
        solo_mode = _solo_mode
        visual_on = _show_visual and view._entity_scope_allows(e, view.visual_scope, default="Local")
        collision_on = _show_collision and view._entity_scope_allows(e, view.collision_scope, default="Selected")
        if _over_budget_keep_cmodel:
            visual_on = False
            if not collision_on:
                continue
        if is_tunnel and tdraw == "Visual only":
            visual_on = True; collision_on = False
        elif is_tunnel and tdraw == "CModel only":
            visual_on = False; collision_on = True
        elif is_tunnel and tdraw == "Compare":
            visual_on = True; collision_on = True
            col = (175, 175, 175)
        if solo_mode:
            # Focus mode: preserve nodes/labels, but suppress visual mesh/fallback
            # noise so the selected CModel block can be read cleanly.
            visual_on = False

        # Some diagnostic modes replace the entity color above. Node Focus
        # is the ordinary-geometry lens, so explicit tunnel Compare colors
        # remain diagnostic and take precedence.
        if _node_focus_active and not (is_tunnel and tdraw == "Compare"):
            model_col = view._model_line_color(_node_focus_col)
        else:
            model_col = view._model_line_color(col)

        # X-Ray + CModel Detail = Structure means "show the actual
        # structural CModel only".  The old behavior still drew the grey
        # visual/render mesh behind it, which made the view look like
        # Structure + extra noise.  Keep the visual shell for X-Ray's default
        # Structure + Ghost comparison mode, but suppress it when the user
        # explicitly switches detail to Structure.
        try:
            preset = str(getattr(view, "view_preset", tk.StringVar(value="")).get() or "")
            detail = view._selected_cmodel_detail_mode()
            # Structure mode always hides visual mesh — user wants skeleton only.
            # Structure + Ghost keeps visual mesh (that's the point of Ghost).
            if detail == "Structure" and collision_on:
                visual_on = False
        except Exception:
            preset = ""

        # During motion LOD, if CModel/Collision is already visible, the grey
        # visual shell is mostly orientation/debug noise during camera drag.
        # Suppress it in motion frames except in explicit Visual/Debug modes.
        try:
            if (view._motion_lod_active() and collision_on and preset not in ("Visual", "Debug") and
                    not (_keep_motion_decor and kind == "decoration")):
                visual_on = False
        except Exception:
            pass

        if kind in ("decoration", "vehicle"):
            _deferred_decor.append((e, visual_on, collision_on))
            continue

        drew_any = False

        if visual_on:
            # Foliage: always use simplified cylinder stand-in.
            # Loading full CModel for trees causes severe FPS drops.
            # Until face-flag filtering is implemented, show a simple
            # trunk cylinder + canopy circle instead.
            if kind == "foliage":
                import math as _fm
                ex = float(e.get("x", 0.0))
                ey = float(e.get("y", 0.0))
                ez = view._entity_base_z(e, ex, ey)
                # Trunk: thin vertical cylinder (8 sides)
                tr = 0.3
                th = 8.0
                n_sides = 8
                foliage_col = view._model_line_color(
                    _node_focus_col if _node_focus_active else (80, 140, 60))
                pts_bot = [(ex + tr*_fm.cos(2*_fm.pi*i/n_sides),
                            ey + tr*_fm.sin(2*_fm.pi*i/n_sides), ez)
                           for i in range(n_sides)]
                pts_top = [(p[0], p[1], ez + th) for p in pts_bot]
                for i in range(n_sides):
                    view._line(draw, pts_bot[i], pts_bot[(i+1)%n_sides], foliage_col, model_width)
                    view._line(draw, pts_top[i], pts_top[(i+1)%n_sides], foliage_col, model_width)
                    view._line(draw, pts_bot[i], pts_top[i], foliage_col, model_width)
                # Canopy: circle at top
                cr = float(OBSTACLE_RADII.get(int(e.get("type_id", 0)), 2.5))
                cr = max(1.0, min(cr, 4.0))
                cz = ez + th
                c_pts = [(ex + cr*_fm.cos(2*_fm.pi*i/n_sides),
                          ey + cr*_fm.sin(2*_fm.pi*i/n_sides), cz)
                         for i in range(n_sides)]
                for i in range(n_sides):
                    view._line(draw, c_pts[i], c_pts[(i+1)%n_sides], foliage_col, model_width)
                drew_any = True
            else:
                lines = view._entity_segments3d(e)
                if not lines:
                    # Do not invent geometry for entities that have no usable
                    # visual mesh. Collision/CModel rendering below still gets
                    # its normal chance to draw real geometry; if that is also
                    # unavailable, the entity stays invisible instead of being
                    # represented by a synthetic radius-based marker box.
                    _wf3d_dbg(
                        f"DRAW_ENTITIES: type={int(e.get('type_id',0))} "
                        "no visual geometry; synthetic marker box suppressed"
                    )
                else:
                    _line_total += len(lines)
                    view._draw_lines_batch(draw,
                        ((a, b, model_col, model_width) for a, b in lines))
                    drew_any = True
        if collision_on:
            cm_col = (255, 220, 0) if (is_tunnel and tdraw == "Compare") else None
            # Block isolation/filtering is an explicit CModel diagnostic and
            # therefore keeps the cached/original Addr-Block colors bright.
            # Ordinary context geometry receives only a final draw override.
            cm_focus_override = None
            if (_node_focus_active and cm_col is None and not _group_isolation):
                cm_focus_override = _node_focus_col
            ccount = view._draw_cmodel_overlay_for_entity(
                draw, e, col=cm_col, render_color_override=cm_focus_override)
            if ccount:
                drew_any = True
        if drew_any:
            _drawn += 1
    _decor_gf = view._selected_decor_cmodel_group()
    _decor_gm = view._selected_decor_cmodel_group_mode()
    _decor_filtered = _decor_gf is not None
    for e, d_visual_on, d_collision_on in _deferred_decor:
        _is_kept_motion_decor = bool(_keep_motion_decor and e.get("_wf_kind") == "decoration")
        _is_kept_motion_cmodel = bool(_keep_motion_cmodel)
        if (view._line_budget_exhausted() and not _is_kept_motion_decor and
                not _is_kept_motion_cmodel):
            # Do not let an over-budget vehicle prevent a later decoration
            # or an explicitly retained CModel block from reaching its
            # keep-visible override.
            continue
        _old_budget_active = getattr(view, '_draw_budget_active', False)
        if _is_kept_motion_decor:
            # Targeted exception: preserve the normal drag preview for nodes,
            # buildings and vehicles, but do not let its temporary line cap
            # erase decorations. The configured option deliberately trades
            # some drag FPS for stable scene context.
            view._draw_budget_active = False
        try:
            drew = False
            if d_visual_on and not _decor_filtered:
                dlines = view._entity_segments3d(e)
                if dlines:
                    _line_total += len(dlines)
                    d_src = view.DECOR if e.get("_wf_kind") == "decoration" else view.VEHICLE
                    d_col = view._model_line_color(
                        _node_focus_col if _node_focus_active else d_src)
                    view._draw_lines_batch(draw,
                        ((a, b, d_col, model_width) for a, b in dlines))
                    drew = True
            decor_focus_override = (
                _node_focus_col if (_node_focus_active and not _decor_filtered) else None)
            if d_collision_on:
                ccount = view._draw_cmodel_overlay_for_entity(
                    draw, e, allow_2d_fallback=not _decor_filtered,
                    group_filter=_decor_gf, group_mode=_decor_gm,
                    render_color_override=decor_focus_override)
                if ccount:
                    drew = True
        finally:
            view._draw_budget_active = _old_budget_active
        if not drew and not _decor_filtered:
            # Same policy as buildings: an entity with neither drawable visual
            # geometry nor drawable CModel/collision geometry contributes
            # nothing to the normal 3D scene. Do not fabricate a proxy box.
            _wf3d_dbg(
                f"DRAW_ENTITIES: type={int(e.get('type_id',0))} "
                f"kind={e.get('_wf_kind')} no drawable geometry; synthetic marker box suppressed"
            )
        if drew:
            _drawn += 1
    _wf3d_dbg(f"DRAW_ENTITIES: drawn={_drawn} marker_boxes={_marker_boxes} model_lines={_line_total} deferred_decor={len(_deferred_decor)}")


def draw_floor_coordinates(view, draw):
    if not view.show_floor_coordinates.get():
        return
    if view._motion_lod_active():
        return
    entities = view.scene.get("entities") or []
    buildings = [e for e in entities if e.get("_wf_kind") == "building"]
    if not buildings:
        return

    try:
        font = view._get_3d_label_font()
    except Exception:
        font = None

    col = view.FLOOR_COORD
    leader_len = 55
    gap = 6
    min_floor_spacing = 2.0

    for e in buildings:
        try:
            lines3d = view._entity_segments3d(e)
        except Exception:
            continue
        if not lines3d:
            continue

        bx_min = by_min = float("inf")
        bx_max = by_max = float("-inf")
        z_counts = {}
        for (a, b) in lines3d:
            bx_min = min(bx_min, a[0], b[0])
            by_min = min(by_min, a[1], b[1])
            bx_max = max(bx_max, a[0], b[0])
            by_max = max(by_max, a[1], b[1])
            if abs(a[2] - b[2]) < 0.3:
                z_bin = round((a[2] + b[2]) * 0.5, 1)
                z_counts[z_bin] = z_counts.get(z_bin, 0) + 1
        if bx_min == float("inf") or not z_counts:
            continue

        total_horiz = sum(z_counts.values())
        threshold = max(8, int(total_horiz * 0.04))

        candidates = [(z, c) for z, c in z_counts.items() if c >= threshold]
        if not candidates:
            continue
        candidates.sort(key=lambda t: t[0])

        floors = []
        for z, count in candidates:
            if not floors or abs(z - floors[-1][0]) >= min_floor_spacing:
                floors.append((z, count))
            else:
                if count > floors[-1][1]:
                    floors[-1] = (z, count)

        for fz, _ in floors:
            if not view._section_z_visible(fz):
                continue
            corners = [
                (bx_max, by_min, fz),
                (bx_max, by_max, fz),
                (bx_min, by_max, fz),
                (bx_min, by_min, fz),
            ]
            best_sx = float("-inf")
            best_sy = 0.0
            for c in corners:
                sx, sy, _ = view._project(*c)
                if sx > best_sx:
                    best_sx = sx
                    best_sy = sy

            x0 = int(best_sx + gap)
            y0 = int(best_sy)
            x1 = x0 + leader_len
            draw.line((x0, y0, x1, y0), fill=col, width=1)
            draw.line((x0, y0 - 3, x0, y0 + 3), fill=col, width=1)
            label = f"Z={fz:.1f}"
            try:
                draw.text((x1 + 4, y0 - 7), label, fill=col, font=font)
            except Exception:
                draw.text((x1 + 4, y0 - 7), label, fill=col)


def refresh_graph_end_cache(view):
    """Find suspiciously close endpoints between disconnected graph components.

    This is deliberately a *diagnostic*, not an automatic linker.  Components
    are computed from the full 3D working set (before Section clipping), so a
    Section slice cannot manufacture fake disconnected graphs.  Markers are
    emitted only when both candidate nodes are currently visible.

    Ordinary near-level gaps require the existing true-CModel LOS test to be
    clear.  Rising/falling stair-like gaps get a narrow exception because real
    stair risers can block the same centreline test even for valid authored
    stair transitions.  False positives are preferable here to hiding the exact
    one-or-two-node stair failure this overlay exists to expose.
    """
    view._wf_graph_end_pairs = []
    try:
        if not bool(getattr(view, 'show_graph_ends', tk.BooleanVar(value=False)).get()):
            return []
        scene = view.scene or {}
        source_ids = [int(i) for i in (scene.get('source_node_ids') or scene.get('node_ids') or [])]
        visible_ids = {int(i) for i in (scene.get('node_ids') or [])}
        if len(source_ids) < 2 or len(visible_ids) < 2:
            return []

        app_nodes = getattr(view.app, 'nodes', None) or []
        source_set = {i for i in source_ids if 0 <= i < len(app_nodes)}
        visible_ids.intersection_update(source_set)
        if len(source_set) < 2 or len(visible_ids) < 2:
            return []

        # Undirected connectivity: one-way mistakes should still count as one
        # physical component for this visualization rather than creating a
        # storm of bogus component boundaries.
        adjacency = {i: set() for i in source_set}
        for i in source_set:
            for raw_nb in getattr(app_nodes[i], 'neighbors', []) or []:
                try:
                    nb = int(raw_nb)
                except Exception:
                    continue
                if nb in source_set and nb != i:
                    adjacency[i].add(nb)
                    adjacency[nb].add(i)

        component_of = {}
        component_sizes = {}
        component_index = 0
        for root in source_ids:
            if root not in source_set or root in component_of:
                continue
            stack = [root]
            component_of[root] = component_index
            members = []
            while stack:
                cur = stack.pop()
                members.append(cur)
                for nb in adjacency.get(cur, ()):
                    if nb not in component_of:
                        component_of[nb] = component_index
                        stack.append(nb)
            component_sizes[component_index] = len(members)
            component_index += 1

        if component_index < 2:
            return []

        # Large enough to bridge one/two missing generator nodes, but still
        # local enough that unrelated disconnected regions do not light up.
        max_xy = 6.0
        max_d3 = 7.0
        max_abs_dz = 4.0
        cell = max_xy
        buckets = {}
        for nid in visible_ids:
            n = app_nodes[nid]
            key = (int(math.floor(float(n.x) / cell)),
                   int(math.floor(float(n.y) / cell)))
            buckets.setdefault(key, []).append(nid)

        # Reuse the already-cached scene collision index.  No CModel parsing
        # or geometry rebuild is performed by this diagnostic.
        collision_index = view._wf_build_edge_collision_index(scene.get('entities') or [])
        best_by_component_pair = {}
        seen_node_pairs = set()

        for aid in visible_ids:
            a = app_nodes[aid]
            ac = component_of.get(aid)
            if ac is None:
                continue
            gx = int(math.floor(float(a.x) / cell))
            gy = int(math.floor(float(a.y) / cell))
            for dx_cell in (-1, 0, 1):
                for dy_cell in (-1, 0, 1):
                    for bid in buckets.get((gx + dx_cell, gy + dy_cell), ()):
                        if bid == aid:
                            continue
                        node_pair = (aid, bid) if aid < bid else (bid, aid)
                        if node_pair in seen_node_pairs:
                            continue
                        seen_node_pairs.add(node_pair)
                        bc = component_of.get(bid)
                        if bc is None or bc == ac:
                            continue
                        b = app_nodes[bid]
                        dx = float(b.x) - float(a.x)
                        dy = float(b.y) - float(a.y)
                        dz = float(b.z) - float(a.z)
                        dxy = math.hypot(dx, dy)
                        adz = abs(dz)
                        if dxy < 0.05 or dxy > max_xy or adz > max_abs_dz:
                            continue
                        d3 = math.sqrt(dxy*dxy + dz*dz)
                        if d3 > max_d3:
                            continue

                        blocked = bool(view._wf_edge_is_blocked(a, b, collision_index))
                        stair_like = (adz >= 0.55 and dxy >= 0.55
                                      and adz / max(dxy, 1e-6) <= 1.35)
                        if blocked and not stair_like:
                            continue

                        degree_penalty = 0.12 * (
                            len(adjacency.get(aid, ())) + len(adjacency.get(bid, ())))
                        # Prefer close, low-degree boundaries.  A small blocked
                        # stair penalty means a clear candidate wins when both
                        # exist, without suppressing stair-riser cases entirely.
                        score = d3 + degree_penalty + (0.35 if blocked else 0.0)
                        comp_pair = (ac, bc) if ac < bc else (bc, ac)
                        rec = {
                            'a': a, 'b': b,
                            'a_id': int(aid), 'b_id': int(bid),
                            'a_component': int(ac), 'b_component': int(bc),
                            'a_component_size': int(component_sizes.get(ac, 0)),
                            'b_component_size': int(component_sizes.get(bc, 0)),
                            'distance': float(d3), 'distance_xy': float(dxy),
                            'dz': float(dz), 'blocked': bool(blocked),
                            'stair_like': bool(stair_like), 'score': float(score),
                        }
                        prev = best_by_component_pair.get(comp_pair)
                        if prev is None or rec['score'] < prev['score']:
                            best_by_component_pair[comp_pair] = rec

        # Keep the view readable on badly fragmented graphs.  Prefer the most
        # suspicious pairs and avoid using one node as the endpoint of several
        # different markers unless there is no alternative.
        ranked = sorted(best_by_component_pair.values(),
                        key=lambda rec: (rec['score'], rec['distance']))
        chosen = []
        used_nodes = set()
        for rec in ranked:
            if len(chosen) >= 12:
                break
            if rec['a_id'] in used_nodes or rec['b_id'] in used_nodes:
                continue
            chosen.append(rec)
            used_nodes.add(rec['a_id']); used_nodes.add(rec['b_id'])

        view._wf_graph_end_pairs = chosen
        return chosen
    except Exception:
        view._wf_graph_end_pairs = []
        return []


def draw_graph_end_overlay(view, draw):
    """Draw cached suspected component endpoints above normal scene geometry."""
    try:
        if not bool(getattr(view, 'show_graph_ends', tk.BooleanVar(value=False)).get()):
            return
    except Exception:
        return
    pairs = list(getattr(view, '_wf_graph_end_pairs', []) or [])
    if not pairs:
        return

    line_clear = view.GRAPH_END_CLEAR
    line_stair = view.GRAPH_END_STAIR
    try:
        show_clear = bool(view.show_graph_end_clear.get())
    except Exception:
        show_clear = True
    try:
        show_stair = bool(view.show_graph_end_stair.get())
    except Exception:
        show_stair = True
    if not show_clear and not show_stair:
        return

    halo = (35, 10, 30)
    inner = (255, 235, 250)
    for rec in pairs:
        try:
            a = rec['a']; b = rec['b']
            pa = (float(a.x), float(a.y), float(a.z))
            pb = (float(b.x), float(b.y), float(b.z))
            is_stair = bool(rec.get('blocked') and rec.get('stair_like'))
            if is_stair and not show_stair:
                continue
            if not is_stair and not show_clear:
                continue
            col = line_stair if is_stair else line_clear

            # Dashed connector shows which two disconnected pieces produced
            # the markers without pretending an actual AIN edge exists.
            steps = 10
            for i in range(0, steps, 2):
                t0 = i / steps
                t1 = min(1.0, (i + 1) / steps)
                p0 = tuple(pa[k] + (pb[k] - pa[k]) * t0 for k in range(3))
                p1 = tuple(pa[k] + (pb[k] - pa[k]) * t1 for k in range(3))
                view._line(draw, p0, p1, col, 2)

            # Two-ring endpoint marker: the original node/selection colour
            # remains visible in the middle while the diagnostic is obvious.
            for node in (a, b):
                view._point_marker(draw, node.x, node.y, node.z, halo, r=11, label=None)
                view._point_marker(draw, node.x, node.y, node.z, col, r=8, label=None)
                view._point_marker(draw, node.x, node.y, node.z, inner, r=4, label=None)
        except Exception:
            continue


def draw_graph_end_legend(view, draw):
    """Tiny top-left key for the currently enabled Graph ends categories."""
    try:
        if not bool(getattr(view, 'show_graph_ends', tk.BooleanVar(value=False)).get()):
            return
    except Exception:
        return

    try:
        show_clear = bool(view.show_graph_end_clear.get())
    except Exception:
        show_clear = True
    try:
        show_stair = bool(view.show_graph_end_stair.get())
    except Exception:
        show_stair = True

    entries = []
    if show_clear:
        entries.append((view.GRAPH_END_CLEAR, "clear gap"))
    if show_stair:
        entries.append((view.GRAPH_END_STAIR, "stair-like"))

    x0, y0 = 10, 10
    if not entries:
        w, h = 126, 27
        try:
            draw.rectangle((x0, y0, x0 + w, y0 + h),
                           fill=(0, 0, 0), outline=(70, 70, 70))
            draw.text((x0 + 7, y0 + 7), "Graph ends: none",
                      fill=(155, 155, 155))
        except Exception:
            pass
        return

    # Keep the familiar compact two-column layout when both filters are on;
    # collapse to a smaller key when only one category is visible.
    w, h = (184, 44) if len(entries) == 2 else (112, 44)
    try:
        draw.rectangle((x0, y0, x0 + w, y0 + h),
                       fill=(0, 0, 0), outline=(70, 70, 70))
        draw.text((x0 + 7, y0 + 4), "Graph ends", fill=(220, 220, 220))

        y = y0 + 25
        if len(entries) == 1:
            col, label = entries[0]
            draw.line((x0 + 8, y, x0 + 24, y), fill=col, width=3)
            draw.text((x0 + 29, y - 6), label, fill=(220, 220, 220))
        else:
            col, label = entries[0]
            draw.line((x0 + 8, y, x0 + 24, y), fill=col, width=3)
            draw.text((x0 + 29, y - 6), label, fill=(220, 220, 220))
            sx = x0 + 91
            col, label = entries[1]
            draw.line((sx, y, sx + 16, y), fill=col, width=3)
            draw.text((sx + 21, y - 6), label, fill=(220, 220, 220))
    except Exception:
        pass


def ref_model_support_z(view, x, y, node_z):
    """Return the highest usable support at (x,y) at/below the active node.

    The character's feet belong on real terrain/CModel support, never on
    node.z itself.  Collision-triangle winding is not assumed: sufficiently
    horizontal faces are accepted in either winding direction.  Underground
    nodes do not fall back to terrain that is physically above the node.
    """
    x = float(x); y = float(y); node_z = float(node_z)
    terrain_z = float(view._terrain_z(x, y))
    best_z = terrain_z if terrain_z <= node_z + 0.05 else None

    index = getattr(view, '_wf_edge_collision_index', None)
    if not isinstance(index, dict):
        index = None
    if index:
        cell = float(index.get('cell', 4.0) or 4.0)
        triangles = index.get('triangles') or []
        grid = index.get('grid') or {}
        gx = int(math.floor(x / cell))
        gy = int(math.floor(y / cell))
        candidate_ids = set()
        # One-cell halo handles points lying exactly on spatial-cell borders.
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                candidate_ids.update(grid.get((gx + ddx, gy + ddy), ()))

        for ti in candidate_ids:
            try:
                tri, bbox = triangles[ti]
                if (x < bbox[0] - 1e-6 or x > bbox[1] + 1e-6 or
                        y < bbox[2] - 1e-6 or y > bbox[3] + 1e-6):
                    continue
                v0, v1, v2 = tri
                e1x = float(v1[0]) - float(v0[0])
                e1y = float(v1[1]) - float(v0[1])
                e1z = float(v1[2]) - float(v0[2])
                e2x = float(v2[0]) - float(v0[0])
                e2y = float(v2[1]) - float(v0[1])
                e2z = float(v2[2]) - float(v0[2])

                # Reject walls/near-vertical faces, but do not depend on
                # triangle winding.  |normal.z|/|normal| >= 0.50 permits
                # ordinary floors, ramps and stair treads while excluding walls.
                nx = e1y * e2z - e1z * e2y
                ny = e1z * e2x - e1x * e2z
                nz = e1x * e2y - e1y * e2x
                nlen = math.sqrt(nx*nx + ny*ny + nz*nz)
                if nlen <= 1e-12 or abs(nz) / nlen < 0.50:
                    continue

                # Barycentric test in XY projection.
                dx0 = x - float(v0[0])
                dy0 = y - float(v0[1])
                d00 = e1x*e1x + e1y*e1y
                d01 = e1x*e2x + e1y*e2y
                d11 = e2x*e2x + e2y*e2y
                d20 = dx0*e1x + dy0*e1y
                d21 = dx0*e2x + dy0*e2y
                denom = d00*d11 - d01*d01
                if abs(denom) < 1e-12:
                    continue
                u = (d11*d20 - d01*d21) / denom
                v = (d00*d21 - d01*d20) / denom
                if u < -0.01 or v < -0.01 or u + v > 1.01:
                    continue

                hit_z = float(v0[2]) + u*e1z + v*e2z
                # A standing AIN node is above its floor.  Never promote a
                # ceiling/upper deck above the actual node into foot support.
                if hit_z > node_z + 0.05:
                    continue
                if best_z is None or hit_z > best_z:
                    best_z = hit_z
            except Exception:
                continue

    if best_z is None:
        # Last-resort diagnostic fallback for an underground/off-mesh point:
        # preserve the known nav lift rather than placing feet on terrain
        # that is above the selected node.
        return node_z - float(NAV_NODE_Z_LIFT)
    return float(best_z)


def draw_ref_model(view, draw, ref_model_geometry=None):
    """Draw one runtime-loaded Delta01 standing soldier beside the active node."""
    try:
        if not bool(view.show_ref_model.get()) or view._motion_lod_active():
            return
    except Exception:
        return
    active_id = view.active_node_id
    if active_id is None:
        return

    active = None
    for n in view.scene.get("nodes") or []:
        try:
            if int(n.id) == int(active_id):
                active = n
                break
        except Exception:
            continue
    if active is None:
        return

    if ref_model_geometry is None:
        return
    verts, edges = ref_model_geometry()
    if not verts or not edges:
        return

    try:
        dist = max(0.0, float(view.ref_model_distance.get()))
    except Exception:
        dist = 0.8
    try:
        angle_deg = float(view.ref_model_angle.get()) % 360.0
    except Exception:
        angle_deg = 0.0

    # Orbit in WORLD XY, not camera-relative space. The runtime-loaded soldier's
    # natural forward axis at 0 degrees is local +Y, so distance must follow
    # that rotated forward vector rather than the old +X orbit convention.
    angle_rad = math.radians(angle_deg)
    dx = -math.sin(angle_rad) * dist
    dy =  math.cos(angle_rad) * dist

    char_x = float(active.x) + dx
    char_y = float(active.y) + dy
    support_z = view._ref_model_support_z(char_x, char_y, float(active.z))
    col = view.REF_MODEL

    # The orbital angle also rotates the reference model around its own
    # vertical axis. Without this, the soldier merely slides around the
    # node while continuing to face one fixed world direction.
    ca = math.cos(angle_rad)
    sa = math.sin(angle_rad)

    def _rotate_ref_xy(v):
        vx, vy, vz = v
        return (
            ca * vx - sa * vy,
            sa * vx + ca * vy,
            vz)

    def _ref_lines():
        for ia, ib in edges:
            try:
                va = _rotate_ref_xy(verts[ia])
                vb = _rotate_ref_xy(verts[ib])
                yield (
                    (char_x + va[0], char_y + va[1], support_z + va[2]),
                    (char_x + vb[0], char_y + vb[1], support_z + vb[2]),
                    col, 1)
            except Exception:
                continue

    view._draw_lines_batch(draw, _ref_lines())


def draw_nodes(view, draw):
    app = view.app
    selected_nodes = view.scene.get("nodes") or []
    selected_count = len(selected_nodes)
    dense = selected_count > 350
    very_dense = selected_count > 900
    motion_lod = view._motion_lod_active()

    # Links first. In moving frames, cap node links harder; the dots and
    # selected node remain visible, and full links return on release.
    active_id = view.active_node_id
    edges = view.scene.get("edges") or []
    motion_edge_cap = 1400 if motion_lod else None
    edge_limit = 4200 if very_dense else None
    EDGE_COL = view.EDGE
    EDGE_DIM = (50, 130, 120)
    BLOCKED_COL = view.EDGE_BLOCKED
    BLOCKED_HALO = (105, 15, 15)
    HALO_COL = view.SELECTED_LINK_HALO
    CORE_COL = view.SELECTED_LINK_CORE
    blocked_edges = getattr(view, '_wf_blocked_edges', set()) or set()
    try:
        _ew = max(1, int(view.node_edge_width.get()))
    except Exception:
        _ew = 1

    def _edge_lines():
        count = 0
        for a, b, both_selected in edges:
            if motion_edge_cap is not None and count >= motion_edge_cap:
                break
            if edge_limit is not None and count > edge_limit:
                break
            count += 1
            aid = getattr(a, 'id', None)
            bid = getattr(b, 'id', None)
            try:
                edge_key = (int(aid), int(bid)) if int(aid) < int(bid) else (int(bid), int(aid))
            except Exception:
                edge_key = None
            is_blocked = edge_key in blocked_edges if edge_key is not None else False
            pa = (a.x, a.y, a.z)
            pb = (b.x, b.y, b.z)
            if is_blocked:
                # Collision failure outranks selection coloring: a bad link
                # must stay visibly red even when one endpoint is active.
                if active_id is not None and (aid == active_id or bid == active_id) and not motion_lod:
                    yield pa, pb, BLOCKED_HALO, _ew + 3
                yield pa, pb, BLOCKED_COL, _ew + (1 if active_id is not None and (aid == active_id or bid == active_id) else 0)
                continue
            if active_id is not None and (aid == active_id or bid == active_id):
                if not motion_lod:
                    yield pa, pb, HALO_COL, _ew + 3
                yield pa, pb, CORE_COL, _ew + 1
                continue
            col = EDGE_COL if both_selected else EDGE_DIM
            yield pa, pb, col, _ew

    view._draw_lines_batch(draw, _edge_lines())

    # Neighbor nodes, dim and tiny. In huge selections this layer becomes
    # visual noise and can hide the entity meshes, so auto-hide it.
    if not dense:
        for n in view.scene.get("neighbors") or []:
            if not view._section_point_visible(n.x, n.y, n.z):
                continue
            view._point_marker(draw, n.x, n.y, n.z, view.NODE_NEIGHBOR, r=2, label=None)

    # Selected nodes. Node IDs are only rasterized once the camera is close
    # enough for them to be useful. This avoids building a wall of tiny text
    # when a large locked set is viewed from a distance.
    try:
        _node_id_min_scale = float(getattr(view, "node_id_min_scale", 8.0))
    except Exception:
        _node_id_min_scale = 8.0
    show_node_ids_3d = bool(view.show_labels.get()) and float(view.scale) >= _node_id_min_scale
    show_radii = bool(getattr(view, "show_radii", tk.BooleanVar(value=False)).get()) and not motion_lod

    _node_id_font = None
    if show_node_ids_3d:
        try:
            _node_id_font = view._get_3d_label_font()
        except Exception:
            _node_id_font = None

    # Cache the current viewport once. ID text is only drawn for nodes whose
    # projected marker is actually on-screen; off-screen locked nodes cost no
    # text raster work.
    try:
        _node_id_view_w = int(getattr(view, "_proj_cw", view.canvas.winfo_width()))
        _node_id_view_h = int(getattr(view, "_proj_ch", view.canvas.winfo_height()))
    except Exception:
        _node_id_view_w = 0
        _node_id_view_h = 0

    app_sel = set(getattr(app, 'selected_nodes', set()) or set())
    locked_active = bool(getattr(app, '_locked_3d_node_set_active', False))
    NODE_LOCKED_DIM = (120, 100, 60)

    # Radius visibility follows EXPLICIT node selection.
    # A large visible/locked graph does not make every node show a radius,
    # but a user-selected group does. Cap only pathological huge selections.
    _radius_ids = set()
    _radius_unit = ()
    if show_radii:
        _radius_cap = 64
        _requested = set()
        for _rid in app_sel:
            try:
                _requested.add(int(_rid))
            except Exception:
                pass
        if active_id is not None:
            try:
                _requested.add(int(active_id))
            except Exception:
                pass

        if len(_requested) <= _radius_cap:
            _radius_ids = _requested
        else:
            _scene_by_id = {int(_n.id): _n for _n in selected_nodes}
            _active_obj = _scene_by_id.get(int(active_id)) if active_id is not None else None
            if _active_obj is not None:
                _ranked = []
                for _rid in _requested:
                    _obj = _scene_by_id.get(_rid)
                    if _obj is None:
                        continue
                    _d2 = ((_obj.x - _active_obj.x) ** 2
                           + (_obj.y - _active_obj.y) ** 2
                           + (_obj.z - _active_obj.z) ** 2)
                    _ranked.append((_d2, _rid))
                _ranked.sort(key=lambda item: item[0])
                _radius_ids = {rid for _d2, rid in _ranked[:_radius_cap]}
            else:
                _radius_ids = set(sorted(_requested)[:_radius_cap])

        _radius_unit = getattr(view, '_radius_unit_circle_32', None)
        if _radius_unit is None:
            _steps = 32
            _radius_unit = tuple(
                (math.cos(math.tau * i / _steps),
                 math.sin(math.tau * i / _steps))
                for i in range(_steps + 1))
            view._radius_unit_circle_32 = _radius_unit

    for n in selected_nodes:
        if not view._section_point_visible(n.x, n.y, n.z):
            continue
        is_active = (n.id == view.active_node_id)
        is_sel = n.id in app_sel
        if is_active:
            col = view.NODE_ACTIVE
        elif is_sel or not locked_active:
            col = view.NODE_SELECTED
        else:
            col = NODE_LOCKED_DIM

        if is_active:
            tz = view._terrain_z(n.x, n.y)
            view._line(draw, (n.x, n.y, tz), (n.x, n.y, n.z), view.GUIDE, 1)

        label = None

        r = 6 if is_active else (1 if dense else (4 if is_sel and locked_active else 3))
        if is_active and not motion_lod:
            view._point_marker(draw, n.x, n.y, n.z, view.SELECTED_NODE_HALO, r=r+3, label=None)
        elif is_sel and locked_active and not motion_lod:
            view._point_marker(draw, n.x, n.y, n.z, view.SELECTED_NODE_HALO, r=r+2, label=None)
        view._point_marker(draw, n.x, n.y, n.z, col, r=r, label=label)

        # Node ID is deliberately independent from the marker colour.
        # Only on-screen nodes are labeled; a large locked set can therefore
        # stay loaded without rasterizing IDs for nodes outside the viewport.
        if show_node_ids_3d and getattr(view, "_fit_capture_bounds", None) is None:
            try:
                sx, sy, _ = view._project(n.x, n.y, n.z)
                if not (0 <= sx < _node_id_view_w and 0 <= sy < _node_id_view_h):
                    continue
                tx, ty = sx + r + 4, sy - r - 3
                label_text = str(n.id)
                # One-pixel black shadow keeps light IDs readable over cyan
                # links, CModel geometry and other bright wireframe lines.
                if _node_id_font is not None:
                    draw.text((tx + 1, ty + 1), label_text, fill=(0, 0, 0), font=_node_id_font)
                    draw.text((tx, ty), label_text, fill=view.NODE_ID_TEXT, font=_node_id_font)
                else:
                    draw.text((tx + 1, ty + 1), label_text, fill=(0, 0, 0))
                    draw.text((tx, ty), label_text, fill=view.NODE_ID_TEXT)
            except Exception:
                pass

        # Radius circles are shown for the active/explicitly-selected
        # nodes only. Merely being part of the visible 3D graph does not
        # qualify a node.
        if show_radii and int(n.id) in _radius_ids:
            try:
                rr = float(n.acceptance_radius())
            except Exception:
                rr = 2.0
            if rr > 0:
                prev = None
                for ux, uy in _radius_unit:
                    p = (n.x + ux * rr, n.y + uy * rr, n.z)
                    if prev is not None:
                        view._line(draw, prev, p, (120, 105, 0), 1)
                    prev = p


def line(view, draw, a, b, color, width=1):
    try:
        if getattr(view, "_draw_budget_active", False):
            budget = getattr(view, "_draw_line_budget", None)
            if budget is not None and view._draw_line_count >= int(budget):
                view._draw_line_skipped_budget += 1
                return False
    except Exception:
        pass
    clipped = view._clip_segment_to_section(a, b)
    if clipped is None:
        return False
    a, b = clipped
    if getattr(view, "_fit_capture_bounds", None) is not None:
        view._fit_capture_point(a)
        view._fit_capture_point(b)
        try:
            view._draw_line_count += 1
        except Exception:
            pass
        return True
    ax, ay, _ = view._project(*a)
    bx, by, _ = view._project(*b)
    # Cheap screen reject with broad margin.
    try:
        do_cull = bool(getattr(view, "perf_offscreen_cull", tk.BooleanVar(value=True)).get())
    except Exception:
        do_cull = True
    if do_cull:
        # Use the per-frame cached canvas size instead of querying
        # Tk for every line.  Tk calls in a hot line loop cost more than
        # they look.
        cw = int(getattr(view, "_proj_cw", max(1, view.canvas.winfo_width())))
        ch = int(getattr(view, "_proj_ch", max(1, view.canvas.winfo_height())))
        m = 256
        if (ax < -m and bx < -m) or (ax > cw + m and bx > cw + m) or (ay < -m and by < -m) or (ay > ch + m and by > ch + m):
            try:
                view._draw_line_skipped_cull += 1
            except Exception:
                pass
            return False
    # Model geometry can be routed to a supersampled layer. Keep culling in
    # normal viewport coordinates, then scale only the final raster draw.
    try:
        coord_scale = float(getattr(view, "_draw_coord_scale", 1.0) or 1.0)
    except Exception:
        coord_scale = 1.0
    try:
        width_scale = float(getattr(view, "_draw_width_scale", 1.0) or 1.0)
    except Exception:
        width_scale = 1.0
    if abs(coord_scale - 1.0) > 1e-9:
        ax *= coord_scale; ay *= coord_scale
        bx *= coord_scale; by *= coord_scale
    front_boost = getattr(view, "_front_view_width_boost", 1)
    raster_width = max(1, int(round(float(width) * width_scale * front_boost)))
    draw.line((ax, ay, bx, by), fill=color, width=raster_width)
    try:
        if getattr(view, "_draw_budget_active", False):
            view._draw_line_count += 1
    except Exception:
        pass
    return True

def get_3d_label_font(view, size_name=None, px=None):
    try:
        from PIL import ImageFont
        if px is None:
            if size_name is None:
                size_name = str(getattr(view, "label_size", tk.StringVar(value="Normal")).get() or "Normal")
            px = 9 if size_name == "Small" else (16 if size_name == "Large" else 12)

        key = max(6, int(px))
        cache = getattr(view, "_wf3d_font_cache", None)
        if cache is None:
            cache = view._wf3d_font_cache = {}

        if key not in cache:
            font = None

            # Reuse the first scalable font path that worked.
            resolved = getattr(view, "_wf3d_font_path", None)
            candidates = []
            if resolved:
                candidates.append(resolved)

            # Windows is the primary target. Consolas matches the editor UI
            # and is present on normal Windows 10/11 installations.
            try:
                if sys.platform.startswith("win"):
                    windir = os.environ.get("WINDIR", r"C:\Windows")
                    candidates.extend((
                        os.path.join(windir, "Fonts", "consola.ttf"),
                        os.path.join(windir, "Fonts", "lucon.ttf"),
                        os.path.join(windir, "Fonts", "cour.ttf"),
                    ))
            except Exception:
                pass

            # Cross-platform/dev fallbacks.
            candidates.extend((
                "DejaVuSansMono.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "/System/Library/Fonts/Menlo.ttc",
                "/Library/Fonts/Courier New.ttf",
            ))

            seen = set()
            for candidate in candidates:
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                try:
                    font = ImageFont.truetype(candidate, key)
                    view._wf3d_font_path = candidate
                    break
                except Exception:
                    continue

            if font is None:
                # Newer Pillow can scale its built-in fallback font. Keep
                # compatibility with older versions as a last resort.
                try:
                    font = ImageFont.load_default(size=key)
                except TypeError:
                    font = ImageFont.load_default()

            cache[key] = font

        return cache[key]
    except Exception:
        return None

def point_marker(view, draw, x, y, z, color, r=5, label=None):
    if not view._section_point_visible(x, y, z):
        return
    if getattr(view, "_fit_capture_bounds", None) is not None:
        view._fit_capture_point((x, y, z))
        return
    sx, sy, _ = view._project(x, y, z)
    draw.ellipse((sx-r, sy-r, sx+r, sy+r), outline=color, fill=None, width=2)
    draw.line((sx-r-3, sy, sx+r+3, sy), fill=color, width=1)
    draw.line((sx, sy-r-3, sx, sy+r+3), fill=color, width=1)
    if label and view.show_labels.get() and not bool(getattr(view, "_render_suppress_labels", False)):
        try:
            font = view._get_3d_label_font()
            draw.text((sx + r + 5, sy - r - 4), str(label), fill=color, font=font)
        except Exception:
            draw.text((sx + r + 5, sy - r - 4), str(label), fill=color)
