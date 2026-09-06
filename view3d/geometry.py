"""Terrain, entity, tunnel, and CModel geometry helpers for the 3D view.

The view instance owns all caches and Tk state; functions receive it
explicitly.
"""

import math
import tkinter as tk

from cmodel.cmodel_geom import _cmodel_husk_segments, _rotated_segments_for_heading
from terrain.terrain import _terrain_sample_height
import view3d.wf3d_debug as _wf3d_debug_mod
from view3d.wf3d_debug import _wf3d_dbg

def terrain_z(view, x, y):
    app = view.app
    try:
        key = (round(float(x), 3), round(float(y), 3))
        cache = getattr(view, "_wf_terrain_z_cache", None)
        if cache is None:
            cache = view._wf_terrain_z_cache = {}
        if key in cache:
            return cache[key]
        info = getattr(app, "terrain_info", None)
        z = _terrain_sample_height(info, x, y)
        if z is None:
            # Missing 3D terrain samples are expected when no terrain source is loaded.
            # Fall back silently instead of writing diagnostic noise to the console.
            z = float(getattr(app, "terrain_z", 0.0) or 0.0)
        else:
            z = float(z)
        if len(cache) > 8192:
            cache.clear()
        cache[key] = z
        return z
    except Exception:
        return float(getattr(app, "terrain_z", 0.0) or 0.0)

def clear_3d_geometry_caches(view):
    """Clear camera-independent 3D geometry caches after visibility/base-Z rules change."""
    try:
        view._wf_entity_line_cache.clear()
    except Exception:
        view._wf_entity_line_cache = {}
    try:
        view._wf_cmodel_world_cache.clear()
    except Exception:
        view._wf_cmodel_world_cache = {}
    try:
        view._wf_context_footprint_cache.clear()
    except Exception:
        view._wf_context_footprint_cache = {}
    try:
        view._wf_terrain_z_cache.clear()
    except Exception:
        view._wf_terrain_z_cache = {}

def allow_underground_geometry(view):
    try:
        return bool(getattr(view, "show_underground_geometry", tk.BooleanVar(value=True)).get())
    except Exception:
        return True

def entity_base_z(view, e, ex=None, ey=None):
    """Return entity origin Z for 3D rendering.

    Old safety logic snapped entities more than ~60m from terrain back to
    terrain height. That helps with bad/missing Z values, but it breaks
    authored underground tunnels/bunkers. With Underground geometry enabled
    we preserve the entity's stored/origin Z exactly.
    """
    try:
        if ex is None:
            ex = float(e.get("x", 0.0))
        if ey is None:
            ey = float(e.get("y", 0.0))
    except Exception:
        ex, ey = 0.0, 0.0
    try:
        terrain = view._terrain_z(ex, ey)
    except Exception:
        terrain = 0.0
    try:
        base_z = float(e.get("z", terrain))
    except Exception:
        base_z = terrain
    if not view._allow_underground_geometry():
        try:
            if abs(base_z - terrain) > 60:
                base_z = terrain
        except Exception:
            pass
    return base_z

def is_tunnel_entity(view, e):
    """Return True for NovaLogic R_Tun* tunnel pieces."""
    try:
        graphic = str(e.get("graphic") or "").strip().lower()
        if graphic.startswith("r_tun"):
            return True
        tid = int(e.get("type_id", 0))
        # Known tunnel type IDs observed in JO/DFBHD-style reports.
        return tid in (1149, 1150, 1151, 1178, 1179, 1180)
    except Exception:
        return False

def tunnel_context_radius(view):
    """Return tunnel context radius in meters, inf for All, or None for Off."""
    try:
        mode = str(getattr(view, "tunnel_context_mode", tk.StringVar(value="Off")).get() or "Off").strip()
        if mode.lower() == "off":
            return None
        if mode.lower() == "all":
            return float("inf")
        if mode.endswith("m"):
            mode = mode[:-1]
        return max(1.0, float(mode))
    except Exception:
        return None

def tunnel_entity_key(view, e):
    try:
        return (
            int(e.get("type_id", 0)),
            round(float(e.get("x", 0.0)), 3),
            round(float(e.get("y", 0.0)), 3),
            round(float(e.get("z", 0.0)), 3),
        )
    except Exception:
        return None

def tunnel_focus_key(view):
    try:
        label = str(view.tunnel_piece_focus.get() or "All")
        if label == "All":
            return None
        return (view._tunnel_focus_map or {}).get(label)
    except Exception:
        return None

def entity_matches_tunnel_key(view, e, key):
    try:
        return view._tunnel_entity_key(e) == key
    except Exception:
        return False

def on_tunnel_focus_changed(view):
    try:
        view._clear_3d_geometry_caches()
    except Exception:
        pass
    try:
        view.rebuild_scene()
        view.fit_view()
    except Exception:
        pass
    view.schedule_render()

def set_tunnel_focus_choices(view, choices):
    """Replace the Tunnel focus dropdown entries."""
    try:
        labels = ["All"] + [c[0] for c in choices]
        view._tunnel_focus_map = {c[0]: c[1] for c in choices}
        cur = str(view.tunnel_piece_focus.get() or "All")
        if cur not in labels:
            view.tunnel_piece_focus.set("All")
        if view._tunnel_piece_om is not None:
            menu = view._tunnel_piece_om["menu"]
            menu.delete(0, "end")
            for label in labels:
                menu.add_command(label=label, command=lambda v=label: (view.tunnel_piece_focus.set(v), view._on_tunnel_focus_changed()))
    except Exception:
        pass

def entity_height_guess(view, e, segs):
    cat = e.get("_wf_kind") or str(e.get("category") or "").lower()
    if cat == "building":
        # Approximate fallback height for entities without usable 3DI geometry.
        return 12.0
    return 3.0

def entity_segments3d(view, e, game_path=None, cmodel_segments_fn=None, cmodel_raw_fn=None):
    """Return approximate 3D context lines for an entity.

    CModel data provides collision/outline geometry rather than the visual
    3DI mesh. For CModel fallback rendering, draw full footprint outlines
    at a few approximate floor heights. This shows more structure than a
    single footprint without creating a fake per-segment vertical curtain.
    """
    tid = int(e.get("type_id", 0))
    _wf3d_dbg(f"ENTITY3D: enter type={tid} kind={e.get('_wf_kind')} pos=({float(e.get('x',0.0)):.2f},{float(e.get('y',0.0)):.2f},{float(e.get('z',0.0)):.2f}) heading={float(e.get('heading',0.0)):.3f}")
    key = (
        tid,
        e.get("_wf_kind"),
        round(float(e.get("x", 0.0)), 3),
        round(float(e.get("y", 0.0)), 3),
        round(float(e.get("z", 0.0)), 3),
        round(float(e.get("heading", 0.0)), 3),
        round(float(e.get("pitch", 0.0)), 3),
        bool(getattr(view, "show_building_levels", tk.BooleanVar(value=True)).get()),
        bool(view._allow_underground_geometry()),
        (str(getattr(view, "tunnel_visual_transform", tk.StringVar(value="Fit XZY")).get() or "Fit XZY") if view._is_tunnel_entity(e) else ""),
        _wf3d_debug_mod.WF3D_RENDER_CACHE_VERSION,
    )
    cached = view._wf_entity_line_cache.get(key)
    if cached is not None and not _wf3d_debug_mod.WF3D_DEBUG_ENABLED:
        _wf3d_dbg(f"ENTITY3D: type={tid} CACHE lines={len(cached)}")
        return cached
    if cached is not None:
        _wf3d_dbg(f"ENTITY3D: type={tid} DEBUG bypass stale line cache lines={len(cached)}")

    import math as _math
    gp = game_path
    kind = e.get("_wf_kind")
    ex = float(e.get("x", 0.0)); ey = float(e.get("y", 0.0))
    base_z = view._entity_base_z(e, ex, ey)

    heading = float(e.get("heading", 0.0))
    pitch = float(e.get("pitch", 0.0) or 0.0)
    lines = []

    def _bounds3_xy(seglist):
        xs=[]; ys=[]; zs=[]
        try:
            for (pa, pb) in seglist or []:
                xs.extend((float(pa[0]), float(pb[0])))
                ys.extend((float(pa[1]), float(pb[1])))
                zs.extend((float(pa[2]), float(pb[2])))
        except Exception:
            return None
        if not xs or not ys or not zs:
            return None
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    def _bounds2(seglist):
        xs=[]; ys=[]
        try:
            for (pa, pb) in seglist or []:
                xs.extend((float(pa[0]), float(pb[0])))
                ys.extend((float(pa[1]), float(pb[1])))
        except Exception:
            return None
        if not xs or not ys:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    # Try proper 3D render mesh first.
    #
    # MED/BHD render vertices use X/Z as the ground footprint and Y as the
    # vertical axis.  The compact CModel outline used by the 2D editor is
    # also a footprint, so fit render X/Z to compact X/Y, then place render
    # Y above entity/base terrain Z. The previous path fit render X/Y
    # to the footprint and treated Z as height, which made buildings tilt
    # upward and look vertically inflated.
    segs3d = cmodel_raw_fn(tid, gp)
    compact_for_fit = None
    if segs3d:
        compact_for_fit = cmodel_segments_fn(tid, gp, allow_load=True, log=None)
        rb = _bounds3_xy(segs3d)
        cb = _bounds2(compact_for_fit) if compact_for_fit else None
        sx = sz = sy_vert = 1.0
        rcx = rcz = rymin = 0.0
        ccx = ccy = 0.0
        if rb and cb:
            rminx, rminy, rminz, rmaxx, rmaxy, rmaxz = rb
            cminx, cminy, cmaxx, cmaxy = cb
            rw = max(1e-6, rmaxx - rminx)
            rd = max(1e-6, rmaxz - rminz)   # render Z is footprint depth
            cw = max(1e-6, cmaxx - cminx)
            cd = max(1e-6, cmaxy - cminy)
            sx = cw / rw
            sz = cd / rd
            # Avoid insane fits if a model has a degenerate collision bbox.
            if not (0.005 <= abs(sx) <= 200.0): sx = 1.0
            if not (0.005 <= abs(sz) <= 200.0): sz = sx
            # Render Y is already in model-space metres.  Do not scale it
            # to the footprint; that was the source of the tall/tilted look.
            sy_vert = (abs(sx) + abs(sz)) * 0.5
            # For normal MED-authored models this is near 1.0. Clamp hard
            # just in case a broken compact bbox tries to explode height.
            if not (0.25 <= abs(sy_vert) <= 4.0):
                sy_vert = 1.0
            rcx = (rminx + rmaxx) * 0.5
            rcz = (rminz + rmaxz) * 0.5
            # MED uses the entity transform as a model origin. Local model
            # vertical coordinates are allowed to be negative relative to
            # that origin; do not rebase the lowest vertex to entity Z.
            rymin = 0.0
            ccx = (cminx + cmaxx) * 0.5
            ccy = (cminy + cmaxy) * 0.5
            _wf3d_dbg(f"ENTITY3D: type={tid} FIT_XZY render_bbox=({rminx:.2f},{rminy:.2f},{rminz:.2f})..({rmaxx:.2f},{rmaxy:.2f},{rmaxz:.2f}) compact_bbox=({cminx:.2f},{cminy:.2f})..({cmaxx:.2f},{cmaxy:.2f}) scale_xz=({sx:.3f},{sz:.3f}) scale_y={sy_vert:.3f}")
        else:
            _wf3d_dbg(f"ENTITY3D: type={tid} FIT_XZY skipped rb={bool(rb)} cb={bool(cb)}")

        tunnel_vis_mode = str(getattr(view, "tunnel_visual_transform", tk.StringVar(value="Fit XZY")).get() or "Fit XZY") if view._is_tunnel_entity(e) else "Fit XZY"
        if tunnel_vis_mode != "Fit XZY":
            _wf3d_dbg(f"ENTITY3D: type={tid} TUNNEL_VISUAL_TRANSFORM={tunnel_vis_mode} using raw render axes for diagnostic compare")

        ang = _math.radians(-round(heading))
        ch = _math.cos(ang); sh = _math.sin(ang)
        pitch_ang = _math.radians(pitch)
        cp = _math.cos(pitch_ang); sp = _math.sin(pitch_ang)
        for (a, b) in segs3d:
            if tunnel_vis_mode == "Raw XZY":
                # MED-like raw origin test: render X/Z are map footprint, render Y is height.
                ax0 = float(a[0]); ay0 = float(a[2]); az0 = float(a[1])
                bx0 = float(b[0]); by0 = float(b[2]); bz0 = float(b[1])
            elif tunnel_vis_mode == "Raw XYZ":
                # Alternate-axis diagnostic: render X/Y footprint, render Z height.
                ax0 = float(a[0]); ay0 = float(a[1]); az0 = float(a[2])
                bx0 = float(b[0]); by0 = float(b[1]); bz0 = float(b[2])
            else:
                ax0 = (float(a[0]) - rcx) * sx + ccx
                ay0 = (float(a[2]) - rcz) * sz + ccy   # render Z -> map Y/depth
                az0 = (float(a[1]) - rymin) * sy_vert  # render Y -> world height, origin-anchored
                bx0 = (float(b[0]) - rcx) * sx + ccx
                by0 = (float(b[2]) - rcz) * sz + ccy
                bz0 = (float(b[1]) - rymin) * sy_vert
            # MED entity pitch rotates local depth/height around the local X
            # axis.  A positive 90-degree pitch therefore lays a standing
            # model onto the ground, matching the mission editor orientation.
            ay0, az0 = ay0*cp - az0*sp, ay0*sp + az0*cp
            by0, bz0 = by0*cp - bz0*sp, by0*sp + bz0*cp
            ax = ax0*ch - ay0*sh; ay = ax0*sh + ay0*ch; az = az0
            bx = bx0*ch - by0*sh; by = bx0*sh + by0*ch; bz = bz0
            lines.append((
                (ex + ax, ey + ay, base_z + az),
                (ex + bx, ey + by, base_z + bz)
            ))
        if lines:
            _wf3d_dbg(f"ENTITY3D: type={tid} return render LOD world_lines={len(lines)}")
            view._wf_entity_line_cache[key] = lines
            return lines
        _wf3d_dbg(f"ENTITY3D: type={tid} render LOD transformed to zero lines")
        lines = []
    else:
        _wf3d_dbg(f"ENTITY3D: type={tid} no render LOD mesh, fallback to compact 2D CModel")

    # Fallback: 2D footprint stacked at levels
    segs = compact_for_fit if compact_for_fit is not None else cmodel_segments_fn(tid, gp, allow_load=True, log=None)
    _wf3d_dbg(f"ENTITY3D: type={tid} compact 2D segments={len(segs) if segs else 0}")
    if not segs:
        _wf3d_dbg(f"ENTITY3D: type={tid} no compact 2D CModel either; draw_entities will use marker box")
        view._wf_entity_line_cache[key] = []
        return []

    if kind == "building":
        segs2 = segs
    elif kind == "decoration" and len(segs) > 300:
        segs2 = _cmodel_husk_segments(segs) or segs
    else:
        segs2 = segs

    rot = _rotated_segments_for_heading(segs2, heading)
    transformed = []
    mnx = mny = float("inf")
    mxx = mxy = float("-inf")
    for (a, b) in rot:
        x1, y1 = ex + a[0], ey + a[1]
        x2, y2 = ex + b[0], ey + b[1]
        transformed.append((x1, y1, x2, y2))
        mnx = min(mnx, x1, x2); mxx = max(mxx, x1, x2)
        mny = min(mny, y1, y2); mxy = max(mxy, y1, y2)

    h = view._entity_height_guess(e, segs)
    if kind == "building":
        h = min(max(h, 8.0), 16.0)
    else:
        h = min(max(h, 2.0), 5.0)

    if kind == "building" and bool(getattr(view, "show_building_levels",
                                          tk.BooleanVar(value=True)).get()):
        levels = [base_z]
        if h >= 9.0:
            levels += [base_z + h * 0.33, base_z + h * 0.66]
        else:
            levels += [base_z + h * 0.5]
        levels.append(base_z + h)
        n = len(transformed)
        upper_step = max(1, int(n / 900))
        for li, z in enumerate(levels):
            step = 1 if li == 0 else upper_step
            for idx, (x1, y1, x2, y2) in enumerate(transformed):
                if idx % step == 0:
                    lines.append(((x1, y1, z), (x2, y2, z)))
    else:
        for (x1, y1, x2, y2) in transformed:
            lines.append(((x1, y1, base_z), (x2, y2, base_z)))

    if mnx != float("inf"):
        zt = base_z + h
        c0 = (mnx, mny, base_z); c1 = (mxx, mny, base_z)
        c2 = (mxx, mxy, base_z); c3 = (mnx, mxy, base_z)
        t0 = (mnx, mny, zt);     t1 = (mxx, mny, zt)
        t2 = (mxx, mxy, zt);     t3 = (mnx, mxy, zt)
        lines.extend([
            (t0, t1), (t1, t2), (t2, t3), (t3, t0),
            (c0, t0), (c1, t1), (c2, t2), (c3, t3),
        ])

    _wf3d_dbg(f"ENTITY3D: type={tid} return FALLBACK stacked footprint lines={len(lines)}")
    view._wf_entity_line_cache[key] = lines
    return lines

def group_palette_color(view, group_index):
    """Readable fixed palette for 3DI/CModel Addr-block groups."""
    palette = (
        (0, 255, 170),   # green-cyan
        (255, 70, 70),   # red
        (70, 120, 255),  # blue
        (255, 170, 0),   # orange
        (170, 80, 255),  # purple
        (80, 255, 80),   # green
        (255, 255, 80),  # yellow
        (80, 230, 255),  # cyan
        (255, 100, 220), # pink
        (170, 170, 170), # grey
    )
    try:
        return palette[int(group_index) % len(palette)]
    except Exception:
        return view.CMODEL_OVERLAY

def selected_cmodel_group(view):
    visible = view._visible_building_blocks()
    if visible is None:
        return None
    return visible

def selected_cmodel_group_mode(view):
    visible = view._visible_building_blocks()
    if visible is None:
        return "Only"
    return "Only"

def selected_decor_cmodel_group(view):
    visible = view._visible_decor_blocks()
    if visible is None:
        return None
    return visible

def selected_decor_cmodel_group_mode(view):
    visible = view._visible_decor_blocks()
    if visible is None:
        return "Only"
    return "Only"

def selected_cmodel_detail_mode(view):
    v = str(getattr(view, "cmodel_detail_mode", tk.StringVar(value="Raw Wire")).get() or "Raw Wire").strip().lower()
    if "ghost" in v:
        return "Structure + Ghost"
    if "structure" in v:
        return "Structure"
    return "Raw Wire"

def dim_color(view, color, factor=0.32):
    try:
        r, g, b = color
        return (max(0, min(255, int(r * factor))),
                max(0, min(255, int(g * factor))),
                max(0, min(255, int(b * factor))))
    except Exception:
        return (70, 70, 70)

def model_line_weight(view):
    """Return the requested model-wire width multiplier.

    PIL's normal ImageDraw path cannot draw below one output pixel. The
    0.50x mode is therefore rendered on a separate 2x geometry layer with a
    one-pixel source stroke and then downsampled to the viewport.
    """
    try:
        var = getattr(view, "model_line_width", None)
        raw = str(var.get() if var is not None else "1.00x").strip().lower()
        value = float(raw.rstrip("x"))
        return 0.5 if value < 0.75 else 1.0
    except Exception:
        return 1.0

def model_line_width_px(view):
    """Base model-wire width before the model-layer supersampling scale."""
    return 1

def model_line_color(view, color, factor=None):
    """Return the original source color unchanged.

    The previous implementation simulated thinness by blending colors toward the
    background. That only made lines darker. Real thinness is now handled by
    the supersampled model layer in render().
    """
    return color
