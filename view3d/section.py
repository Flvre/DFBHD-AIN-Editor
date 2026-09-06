"""Section clipping and overlay subsystem for the 3D view.

The view instance is explicit; camera/render ownership remains with
Wireframe3DView.
"""

import math

from entity.entity_data import DEFAULT_RADIUS, OBSTACLE_RADII
from view3d.wf3d_debug import _wf3d_dbg

def reset_section_for_new_scene(view):
    """Invalidate Section coordinates whenever the loaded map/graph changes."""
    try:
        view.section_enabled.set(False)
        view.section_z_enabled.set(True)
        view.section_xy_enabled.set(False)
        view.section_z_locked.set(False)
        view.section_xy_locked.set(False)
    except Exception:
        pass
    view._section_initialized = False
    view._section_xy_initialized = False
    view._section_guide_xy_bounds = None
    view._section_guide_z_bounds = None
    view._section_handle_positions = {}
    view._section_hover_handle = None
    try:
        view._update_section_toolbar()
    except Exception:
        pass

def section_is_enabled(view):
    try:
        return bool(view.section_enabled.get())
    except Exception:
        return False

def section_range(view):
    """Return normalized Z slab while Section is active.

    Z applies when its toggle is ON, or while Lock Z is holding the last
    crop after the visible Z controls have been turned off.
    """
    if not view._section_is_enabled():
        return None
    try:
        active = bool(view.section_z_enabled.get()) or bool(view.section_z_locked.get())
    except Exception:
        active = bool(view.section_z_enabled.get())
    if not active:
        return None
    try:
        lo = float(view.section_lower_z.get())
        hi = float(view.section_upper_z.get())
    except Exception:
        return None
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi

def section_xy_range(view):
    """Return XY crop when enabled or held by Lock XY."""
    if not view._section_is_enabled() or not view._section_xy_initialized:
        return None
    try:
        active = bool(view.section_xy_enabled.get()) or bool(view.section_xy_locked.get())
    except Exception:
        active = bool(view.section_xy_enabled.get())
    if not active:
        return None
    try:
        x0 = float(view.section_xy_min_x.get())
        x1 = float(view.section_xy_max_x.get())
        y0 = float(view.section_xy_min_y.get())
        y1 = float(view.section_xy_max_y.get())
    except Exception:
        return None
    if x0 > x1: x0, x1 = x1, x0
    if y0 > y1: y0, y1 = y1, y0
    return x0, x1, y0, y1

def section_z_visible(view, z):
    rng = view._section_range()
    if rng is None:
        return True
    try:
        zz = float(z)
        return rng[0] - 1e-7 <= zz <= rng[1] + 1e-7
    except Exception:
        return True

def section_point_visible(view, x, y, z):
    if not view._section_z_visible(z):
        return False
    xr = view._section_xy_range()
    if xr is None:
        return True
    try:
        xx, yy = float(x), float(y)
        return (xr[0] - 1e-7 <= xx <= xr[1] + 1e-7 and
                xr[2] - 1e-7 <= yy <= xr[3] + 1e-7)
    except Exception:
        return True

def capture_section_guide_bounds(view):
    """Freeze the visual Section guide envelope from the current unfiltered view."""
    try:
        b = (view.scene or {}).get("bounds")
        if b and len(b) >= 6:
            x0, y0, z0, x1, y1, z1 = map(float, b[:6])

            sx = max(4.0, x1-x0)
            sy = max(4.0, y1-y0)
            xy_pad = max(1.5, min(8.0, max(sx, sy) * 0.05))
            view._section_guide_xy_bounds = (
                x0-xy_pad, y0-xy_pad, x1+xy_pad, y1+xy_pad)

            sz = max(4.0, z1-z0)
            z_pad = max(1.0, min(5.0, sz * 0.08))
            view._section_guide_z_bounds = (z0-z_pad, z1+z_pad)
            return
    except Exception:
        pass

    # Local stable fallback around the current camera target.
    try:
        tx, ty, tz = map(float, view.target)
    except Exception:
        tx = ty = tz = 0.0
    view._section_guide_xy_bounds = (tx-20.0, ty-20.0, tx+20.0, ty+20.0)
    view._section_guide_z_bounds = (tz-6.0, tz+6.0)

def initialize_section_range(view):
    """Initialize Z from the CURRENT loaded/selected 3D graph."""
    try:
        nodes = list((view.scene or {}).get("nodes") or [])
    except Exception:
        nodes = []
    if not nodes:
        view._section_initialized = False
        return False

    try:
        center = float(view.target[2])
    except Exception:
        center = float(nodes[len(nodes)//2].z)

    try:
        if view.active_node_id is not None:
            active = next((n for n in nodes if int(n.id) == int(view.active_node_id)), None)
            if active is not None:
                center = float(active.z)
            else:
                zs = sorted(float(n.z) for n in nodes)
                center = zs[len(zs)//2]
        else:
            zs = sorted(float(n.z) for n in nodes)
            center = zs[len(zs)//2]
    except Exception:
        pass

    view.section_lower_z.set(center - 2.0)
    view.section_upper_z.set(center + 2.0)
    view._section_initialized = True
    return True

def initialize_section_xy_range(view):
    """Initialize XY from the CURRENT selected/locked graph of the loaded map."""
    try:
        scene_nodes = list((view.scene or {}).get("nodes") or [])
    except Exception:
        scene_nodes = []
    if not scene_nodes:
        view._section_xy_initialized = False
        return False

    pts = []

    try:
        locked_ids = view.app._get_3d_locked_node_ids()
    except Exception:
        locked_ids = None
    if locked_ids:
        try:
            locked_ids = {int(v) for v in locked_ids}
            pts = [(float(n.x), float(n.y)) for n in scene_nodes if int(n.id) in locked_ids]
        except Exception:
            pts = []

    if not pts:
        try:
            selected_ids = set(getattr(view.app, 'selected_nodes', set()) or set())
            sid = getattr(view.app, 'selected_id', None)
            if sid is not None:
                selected_ids.add(int(sid))
            if selected_ids:
                pts = [(float(n.x), float(n.y)) for n in scene_nodes if int(n.id) in selected_ids]
        except Exception:
            pts = []

    if not pts:
        try:
            pts = [(float(n.x), float(n.y)) for n in scene_nodes]
        except Exception:
            pts = []

    if not pts:
        view._section_xy_initialized = False
        return False

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)

    sx = x1 - x0
    sy = y1 - y0
    px = max(0.5, min(2.0, sx * 0.04))
    py = max(0.5, min(2.0, sy * 0.04))

    if sx < 0.25:
        x0 -= 2.0
        x1 += 2.0
    else:
        x0 -= px
        x1 += px
    if sy < 0.25:
        y0 -= 2.0
        y1 += 2.0
    else:
        y0 -= py
        y1 += py

    view.section_xy_min_x.set(x0)
    view.section_xy_max_x.set(x1)
    view.section_xy_min_y.set(y0)
    view.section_xy_max_y.set(y1)
    view._section_xy_initialized = True
    return True

def section_context_node_visible(view, n):
    """Cheap node test used while deriving the temporary Section scene."""
    try:
        return view._section_point_visible(float(n.x), float(n.y), float(n.z))
    except Exception:
        return True

def section_entity_intersects_context(view, e, kind=None):
    """Return whether an entity should participate in the current Section context.

    Buildings use their footprint for XY intersection so a containing room/
    building remains available even when its origin is outside the crop.
    Decorations/foliage/vehicles are treated as local point/radius context.
    Z is intentionally strict for decoration-like context but conservative
    for buildings because their MIS origin does not describe full model height.
    """
    if not view._section_is_enabled():
        return True

    xr = view._section_xy_range()
    zr = view._section_range()
    try:
        tid = int(e.get("type_id", 0))
        ex = float(e.get("x", 0.0))
        ey = float(e.get("y", 0.0))
        ez = float(e.get("z", getattr(view.app, "terrain_z", 0.0)))
    except Exception:
        return True

    if xr is not None:
        x0, x1, y0, y1 = xr
        if kind == "building":
            try:
                fp = view._entity_context_footprint_bounds(e, tid)
            except Exception:
                fp = None
            if fp is not None:
                fmnx, fmny, fmxx, fmxy = fp
                if fmxx < x0 or fmnx > x1 or fmxy < y0 or fmny > y1:
                    return False
            else:
                r = float(OBSTACLE_RADII.get(tid, DEFAULT_RADIUS))
                if ex + r < x0 or ex - r > x1 or ey + r < y0 or ey - r > y1:
                    return False
        else:
            r = float(OBSTACLE_RADII.get(tid, DEFAULT_RADIUS))
            # Decorations commonly have overly generic obstacle radii.
            # Keep their query local so a tiny Section does not retain
            # decoration context from the other side of a building.
            if kind in ("decoration", "foliage"):
                r = min(r, 1.5)
            if ex + r < x0 or ex - r > x1 or ey + r < y0 or ey - r > y1:
                return False

    if zr is not None and kind in ("decoration", "foliage", "vehicle"):
        z0, z1 = zr
        # Small tolerance covers object origins at floor/support height.
        zpad = 1.25 if kind in ("decoration", "foliage") else 2.5
        if ez < z0 - zpad or ez > z1 + zpad:
            return False

    return True

def schedule_section_context_refresh(view, delay=90):
    """Debounce the heavier Section-aware scene/context rebuild."""
    job = getattr(view, '_section_context_refresh_job', None)
    if job is not None:
        try:
            view.after_cancel(job)
        except Exception:
            pass
        view._section_context_refresh_job = None

    def _run():
        view._section_context_refresh_job = None
        try:
            view.rebuild_scene()
        except Exception as exc:
            _wf3d_dbg(f"SECTION_CONTEXT rebuild failed: {exc}", force=True)
        view.schedule_render()

    try:
        view._section_context_refresh_job = view.after(max(0, int(delay)), _run)
    except Exception:
        _run()

def refresh_section_context_now(view):
    job = getattr(view, '_section_context_refresh_job', None)
    if job is not None:
        try:
            view.after_cancel(job)
        except Exception:
            pass
        view._section_context_refresh_job = None
    try:
        view.rebuild_scene()
    except Exception as exc:
        _wf3d_dbg(f"SECTION_CONTEXT immediate rebuild failed: {exc}", force=True)
    view.schedule_render()

def toggle_section_z(view):
    if not view._section_is_enabled():
        return
    enable = not bool(view.section_z_enabled.get())
    view.section_z_enabled.set(enable)

    # With Lock Z off, disabling Z disables the filter.
    # With Lock Z on, the filter remains applied but controls disappear.
    view._section_handle_positions = {}
    view._update_section_toolbar()
    view._refresh_section_context_now()

def toggle_section_xy(view):
    if not view._section_is_enabled():
        return
    enable = not bool(view.section_xy_enabled.get())
    if enable and not view._section_xy_initialized:
        if not view._initialize_section_xy_range():
            return
    view.section_xy_enabled.set(enable)

    view._section_handle_positions = {}
    view._update_section_toolbar()
    view._refresh_section_context_now()

def toggle_section_lock_z(view):
    if not view._section_is_enabled():
        return
    view.section_z_locked.set(not bool(view.section_z_locked.get()))
    view._section_handle_positions = {}
    view._update_section_toolbar()
    view._refresh_section_context_now()

def toggle_section_lock_xy(view):
    if not view._section_is_enabled():
        return
    # Lock XY cannot hold a crop that has never been initialized.
    if not view._section_xy_initialized:
        if not view._initialize_section_xy_range():
            return
    view.section_xy_locked.set(not bool(view.section_xy_locked.get()))
    view._section_handle_positions = {}
    view._update_section_toolbar()
    view._refresh_section_context_now()

def toggle_section(view):
    enable = not view._section_is_enabled()

    if enable:
        # Never initialize Section from stale/no-map coordinates.
        try:
            _has_scene_nodes = bool((view.scene or {}).get("nodes"))
        except Exception:
            _has_scene_nodes = False
        if not _has_scene_nodes:
            return

        # Capture BEFORE Section filters alter the scene/context.
        view._capture_section_guide_bounds()
        if not view._section_initialized:
            if not view._initialize_section_range():
                return
        view.section_z_enabled.set(True)
        view.section_xy_enabled.set(False)
        view.section_z_locked.set(False)
        view.section_xy_locked.set(False)
    else:
        view._section_guide_xy_bounds = None
        view._section_guide_z_bounds = None

    view.section_enabled.set(enable)
    view._section_handle_positions = {}
    view._update_section_toolbar()
    try:
        view.canvas.configure(cursor="arrow")
    except Exception:
        pass
    view._refresh_section_context_now()

def update_section_toolbar(view):
    enabled = view._section_is_enabled()
    z_on = enabled and bool(view.section_z_enabled.get())
    xy_on = enabled and bool(view.section_xy_enabled.get())
    z_lock = enabled and bool(view.section_z_locked.get())
    xy_lock = enabled and bool(view.section_xy_locked.get())
    light = view._side_panel_light_enabled()

    if light:
        inactive_bg, inactive_fg, inactive_active = "#e3e3e3", "#1f1f1f", "#e1e1e1"
        enabled_bg, enabled_fg, enabled_active = "#c9e8d0", "#114422", "#b8d9c0"
        z_bg, z_fg = "#d9ecff", "#124b70"
        xy_bg, xy_fg = "#ead7ea", "#6b176b"
        lock_bg, lock_fg = "#fff0c9", "#624300"
        label_bg, label_fg = "#f1f1f1", "#000000"
    else:
        inactive_bg, inactive_fg, inactive_active = "#303030", "#eeeeee", "#3f3f3f"
        enabled_bg, enabled_fg, enabled_active = "#3b5d49", "#eeeeee", "#476f57"
        z_bg, z_fg = "#31536a", "#eeeeee"
        xy_bg, xy_fg = "#633663", "#ffffff"
        lock_bg, lock_fg = "#6a572e", "#ffffff"
        label_bg, label_fg = "#202020", "#bdbdbd"

    try:
        if view._section_button is not None:
            view._section_button.configure(
                bg=(enabled_bg if enabled else inactive_bg),
                fg=(enabled_fg if enabled else inactive_fg),
                activebackground=(enabled_active if enabled else inactive_active),
                activeforeground=(enabled_fg if enabled else inactive_fg),
                relief=("sunken" if enabled else "flat"))
    except Exception:
        pass

    specs = (
        (view._section_z_button, z_on, z_bg, z_fg, view._section_button),
        (view._section_xy_button, xy_on, xy_bg, xy_fg, view._section_z_button),
        (view._section_lock_z_button, z_lock, lock_bg, lock_fg, view._section_xy_button),
        (view._section_lock_xy_button, xy_lock, lock_bg, lock_fg, view._section_lock_z_button),
    )
    for b, on, on_bg, on_fg, after in specs:
        try:
            if b is None:
                continue
            b.configure(
                bg=(on_bg if on else inactive_bg),
                fg=(on_fg if on else inactive_fg),
                activebackground=(on_bg if on else inactive_active),
                activeforeground=(on_fg if on else inactive_fg),
                relief=("sunken" if on else "flat"))
            if enabled and not b.winfo_manager():
                b.pack(side="left", padx=2, pady=4, after=after)
            elif not enabled and b.winfo_manager():
                b.pack_forget()
        except Exception:
            pass

    try:
        label = view._section_value_label
        if label is not None:
            label.configure(bg=label_bg, fg=label_fg)
            parts = []
            zr = view._section_range()
            xr = view._section_xy_range()
            if zr is not None and z_on:
                parts.append(f"Z {zr[0]:.2f}–{zr[1]:.2f}")
            if xr is not None and xy_on:
                parts.append(f"XY X {xr[0]:.2f}–{xr[1]:.2f} Y {xr[2]:.2f}–{xr[3]:.2f}")
            if enabled and parts:
                label.configure(text="   ".join(parts))
                if not label.winfo_manager():
                    label.pack(side="left", padx=(2, 4), pady=4,
                               after=view._section_lock_xy_button)
            elif label.winfo_manager():
                label.pack_forget()
    except Exception:
        pass

def section_world_xy_bounds(view):
    try:
        if view._section_guide_xy_bounds is not None:
            return tuple(view._section_guide_xy_bounds)
    except Exception:
        pass
    try:
        tx, ty, _ = view.target
        return float(tx)-20.0, float(ty)-20.0, float(tx)+20.0, float(ty)+20.0
    except Exception:
        return -20.0, -20.0, 20.0, 20.0

def section_scene_z_bounds(view):
    try:
        if view._section_guide_z_bounds is not None:
            z0, z1 = map(float, view._section_guide_z_bounds)
            # Always make sure the editable Z values remain representable,
            # without shrinking/reframing when scene context changes.
            rng = view._section_range()
            if rng is not None:
                z0 = min(z0, float(rng[0]))
                z1 = max(z1, float(rng[1]))
            return z0, z1
    except Exception:
        pass

    rng = view._section_range() or (0.0, 4.0)
    lo, hi = rng
    span = hi-lo
    if span < 6.0:
        mid = (lo+hi)*0.5
        return mid-3.0, mid+3.0
    pad = max(1.0, span*0.08)
    return lo-pad, hi+pad

def section_direct_drag_supported(view):
    """Horizontal world planes become degenerate very near Top view."""
    try:
        return abs(math.cos(float(view.pitch))) >= 0.20
    except Exception:
        return True

def section_handle_hit(view, sx, sy):
    if not view._section_is_enabled():
        return None
    try:
        x = float(sx); y = float(sy)
    except Exception:
        return None
    best = None
    best_d2 = 14.0 * 14.0
    for kind, rec in (view._section_handle_positions or {}).items():
        try:
            hx, hy = float(rec[0]), float(rec[1])
            d2 = (x-hx)*(x-hx) + (y-hy)*(y-hy)
            if d2 <= best_d2:
                best = kind
                best_d2 = d2
        except Exception:
            continue
    return best

def section_motion(view, event):
    if getattr(view, "_drag_last", None):
        return
    hit = view._section_handle_hit(event.x, event.y)
    if hit == view._section_hover_handle:
        return
    view._section_hover_handle = hit
    try:
        if hit in ("section_lower", "section_upper"):
            cur = "sb_v_double_arrow"
        elif hit:
            cur = "fleur"
        else:
            cur = "arrow"
        view.canvas.configure(cursor=cur)
    except Exception:
        pass

def section_apply_drag_delta(view, kind, dy):
    rng = view._section_range()
    if rng is None:
        return
    lo, hi = rng
    min_gap = 0.05
    try:
        if view._section_direct_drag_supported():
            cp = max(0.20, abs(math.cos(float(view.pitch))))
            dz = -float(dy) / max(0.001, cp * float(view.scale))
        else:
            z0, z1 = view._section_scene_z_bounds()
            ch = max(120.0, float(getattr(view, "_proj_ch", view.canvas.winfo_height())))
            bar_h = max(80.0, ch - 140.0)
            dz = -float(dy) * ((z1-z0) / bar_h)
    except Exception:
        return

    if kind == "section_lower":
        lo = min(lo + dz, hi - min_gap)
        view.section_lower_z.set(lo)
    elif kind == "section_upper":
        hi = max(hi + dz, lo + min_gap)
        view.section_upper_z.set(hi)
    else:
        return
    view._update_section_toolbar()

def section_apply_xy_drag_delta(view, kind, dx, dy):
    rng = view._section_xy_range()
    if rng is None:
        return

    x0, x1, y0, y1 = rng
    axis = "x" if kind.startswith("section_x") else "y"

    try:
        tx, ty, tz = map(float, view.target)
        p0 = view._project(tx, ty, tz)
        p1 = view._project(
            tx + (1.0 if axis == "x" else 0.0),
            ty + (1.0 if axis == "y" else 0.0),
            tz
        )
        vx = float(p1[0]-p0[0])
        vy = float(p1[1]-p0[1])
        den = vx*vx + vy*vy
        if den < 1e-6:
            return
        delta = (float(dx)*vx + float(dy)*vy) / den
    except Exception:
        return

    gap = 0.05
    if kind == "section_xmin":
        view.section_xy_min_x.set(min(x0+delta, x1-gap))
    elif kind == "section_xmax":
        view.section_xy_max_x.set(max(x1+delta, x0+gap))
    elif kind == "section_ymin":
        view.section_xy_min_y.set(min(y0+delta, y1-gap))
    elif kind == "section_ymax":
        view.section_xy_max_y.set(max(y1+delta, y0+gap))
    else:
        return

    view._update_section_toolbar()

def clip_segment_to_section(view, a, b, rng=None):
    """Clip against Z slab and independent XY crop volume."""
    zr = view._section_range() if rng is None else rng
    xr = view._section_xy_range()
    if zr is None and xr is None:
        return a, b

    try:
        p0 = [float(a[0]), float(a[1]), float(a[2])]
        p1 = [float(b[0]), float(b[1]), float(b[2])]
    except Exception:
        return a, b

    bounds = (
        ((xr[0], xr[1]) if xr is not None else None),
        ((xr[2], xr[3]) if xr is not None else None),
        ((zr[0], zr[1]) if zr is not None else None),
    )

    t0, t1 = 0.0, 1.0
    for axis, br in enumerate(bounds):
        if br is None:
            continue
        lo, hi = br
        d = p1[axis] - p0[axis]
        if abs(d) < 1e-12:
            if p0[axis] < lo or p0[axis] > hi:
                return None
            continue
        ta = (lo - p0[axis]) / d
        tb = (hi - p0[axis]) / d
        if ta > tb:
            ta, tb = tb, ta
        t0 = max(t0, ta)
        t1 = min(t1, tb)
        if t0 > t1:
            return None

    d = [p1[i] - p0[i] for i in range(3)]
    aa = tuple(p0[i] + d[i] * t0 for i in range(3))
    bb = tuple(p0[i] + d[i] * t1 for i in range(3))
    return aa, bb

def draw_section_overlay(view, draw):
    """Draw Z slab and XY crop as two visually separate nested control volumes."""
    view._section_handle_positions = {}
    if not view._section_is_enabled():
        return

    zr = view._section_range()
    xr = view._section_xy_range()
    if zr is None and xr is None:
        return

    cw = max(1, int(getattr(view, "_proj_cw", view.canvas.winfo_width())))
    ch = max(1, int(getattr(view, "_proj_ch", view.canvas.winfo_height())))
    font = view._get_3d_label_font(px=10)

    # ---- Z: preserve existing horizontal slab visualization ----
    # The filter remains active even when these controls are hidden.
    if zr is not None and bool(view.section_z_enabled.get()):
        lo, hi = zr
        if not view._section_direct_drag_supported():
            z0, z1 = view._section_scene_z_bounds()
            x = 28
            yt, yb = 70, max(90, ch - 70)
            span = max(1e-6, z1-z0)
            def zy(z):
                t = (float(z)-z0)/span
                return yb - max(0.0, min(1.0, t)) * (yb-yt)
            draw.line((x, yt, x, yb), fill=view.SECTION_DIM, width=1)
            try:
                draw.text((12, 48), "SECTION Z", fill=view.SECTION_DIM, font=font)
            except Exception:
                draw.text((12, 48), "SECTION Z", fill=view.SECTION_DIM)
            for kind, z, col, label in (
                ("section_upper", hi, view.SECTION_UPPER, "HIGH"),
                ("section_lower", lo, view.SECTION_LOWER, "LOW")):
                yy = zy(z)
                draw.line((x-7, yy, x+25, yy), fill=col, width=2)
                draw.rectangle((x-5, yy-5, x+5, yy+5),
                               outline=view.SECTION_HANDLE, fill=col, width=1)
                try:
                    draw.text((x+31, yy-7), f"{label} {z:.2f}", fill=col, font=font)
                except Exception:
                    draw.text((x+31, yy-7), f"{label} {z:.2f}", fill=col)
                view._section_handle_positions[kind] = (x, yy)
        else:
            x0, y0, x1, y1 = view._section_world_xy_bounds()
            corners_xy = ((x0,y0),(x1,y0),(x1,y1),(x0,y1))
            for kind, z, col, label in (
                ("section_lower", lo, view.SECTION_LOWER, "LOW"),
                ("section_upper", hi, view.SECTION_UPPER, "HIGH")):
                pts = [view._project(x, y, z)[:2] for x, y in corners_xy]
                draw.line(pts + [pts[0]], fill=col, width=1)

                candidates = list(pts)
                candidates += [((pts[i][0]+pts[(i+1)%4][0])*0.5,
                                (pts[i][1]+pts[(i+1)%4][1])*0.5) for i in range(4)]
                visible = [p for p in candidates
                           if -20 <= p[0] <= cw+20 and 10 <= p[1] <= ch-10]
                hp = max(visible or candidates, key=lambda p: p[0])
                hx = max(12.0, min(float(cw)-12.0, float(hp[0])))
                hy = max(12.0, min(float(ch)-12.0, float(hp[1])))
                draw.rectangle((hx-5, hy-5, hx+5, hy+5),
                               outline=view.SECTION_HANDLE, fill=col, width=1)
                text_x = hx - 84 if hx > cw - 110 else hx + 9
                try:
                    draw.text((text_x, hy-7), f"{label} {z:.2f}", fill=col, font=font)
                except Exception:
                    draw.text((text_x, hy-7), f"{label} {z:.2f}", fill=col)
                view._section_handle_positions[kind] = (hx, hy)

    # ---- XY: independent nested control volume ----
    # The filter remains active even when these controls are hidden.
    if xr is not None and bool(view.section_xy_enabled.get()):
        x0, x1, y0, y1 = xr

        # Use a dedicated display Z range so XY does not share Z's exact
        # top/bottom control planes. Think "box inside a box".
        if zr is not None:
            zlo, zhi = zr
            zspan = max(0.25, zhi-zlo)
            inset = min(0.75, max(0.18, zspan * 0.12))
            dz0 = zlo + inset
            dz1 = zhi - inset
            if dz1 <= dz0:
                mid = (zlo + zhi) * 0.5
                dz0, dz1 = mid - 0.15, mid + 0.15
        else:
            zlo, zhi = view._section_scene_z_bounds()
            mid = (zlo+zhi)*0.5
            dz0, dz1 = mid-1.0, mid+1.0

        # Slight inward display inset in X/Y as well so XY never visually
        # shares the exact corners/edges of the Z guide.
        sx = max(0.2, x1-x0)
        sy = max(0.2, y1-y0)
        ix = min(0.8, max(0.12, sx * 0.06))
        iy = min(0.8, max(0.12, sy * 0.06))
        dx0, dx1 = x0+ix, x1-ix
        dy0, dy1 = y0+iy, y1-iy
        if dx1 <= dx0:
            mid = (x0+x1)*0.5
            dx0, dx1 = mid-0.15, mid+0.15
        if dy1 <= dy0:
            mid = (y0+y1)*0.5
            dy0, dy1 = mid-0.15, mid+0.15

        # Outer display box = green; inner/axis emphasis = magenta.
        box_corners = (
            (dx0,dy0,dz0), (dx1,dy0,dz0), (dx1,dy1,dz0), (dx0,dy1,dz0),
            (dx0,dy0,dz1), (dx1,dy0,dz1), (dx1,dy1,dz1), (dx0,dy1,dz1),
        )
        P = [view._project(*p)[:2] for p in box_corners]
        edges = (
            (0,1),(1,2),(2,3),(3,0),
            (4,5),(5,6),(6,7),(7,4),
            (0,4),(1,5),(2,6),(3,7)
        )
        for a,b in edges:
            draw.line((P[a][0],P[a][1],P[b][0],P[b][1]),
                      fill=view.SECTION_XY_OUTER, width=1)

        # Magenta cross/inner rectangle on the middle slice for immediate
        # visual separation from the green outer XY box.
        zm = (dz0+dz1)*0.5
        mid_pts = [
            view._project(dx0,dy0,zm)[:2],
            view._project(dx1,dy0,zm)[:2],
            view._project(dx1,dy1,zm)[:2],
            view._project(dx0,dy1,zm)[:2],
        ]
        draw.line(mid_pts + [mid_pts[0]], fill=view.SECTION_XY_INNER, width=1)

        # Four XY handles, all placed on the magenta middle rectangle.
        handle_specs = (
            ("section_xmin", ((mid_pts[0][0]+mid_pts[3][0])*0.5,
                              (mid_pts[0][1]+mid_pts[3][1])*0.5), "X−"),
            ("section_xmax", ((mid_pts[1][0]+mid_pts[2][0])*0.5,
                              (mid_pts[1][1]+mid_pts[2][1])*0.5), "X+"),
            ("section_ymin", ((mid_pts[0][0]+mid_pts[1][0])*0.5,
                              (mid_pts[0][1]+mid_pts[1][1])*0.5), "Y−"),
            ("section_ymax", ((mid_pts[3][0]+mid_pts[2][0])*0.5,
                              (mid_pts[3][1]+mid_pts[2][1])*0.5), "Y+"),
        )
        values = {
            "section_xmin": x0, "section_xmax": x1,
            "section_ymin": y0, "section_ymax": y1,
        }
        for kind, hp, label in handle_specs:
            hx = max(12.0, min(float(cw)-12.0, float(hp[0])))
            hy = max(12.0, min(float(ch)-12.0, float(hp[1])))
            draw.rectangle((hx-5,hy-5,hx+5,hy+5),
                           outline=view.SECTION_HANDLE,
                           fill=view.SECTION_XY_INNER, width=1)
            tx = hx + 8
            try:
                draw.text((tx,hy-7),f"{label} {values[kind]:.2f}",
                          fill=view.SECTION_XY_INNER,font=font)
            except Exception:
                draw.text((tx,hy-7),f"{label} {values[kind]:.2f}",
                          fill=view.SECTION_XY_INNER)
            view._section_handle_positions[kind] = (hx,hy)
