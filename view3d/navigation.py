"""Camera navigation, fitting, and cardinal views for the 3D viewport."""

import math

def focus_selected_building(view):
    """One-click focus for the current node/building.

    This is intentionally just a scope preset: it does not alter loaded data
    or hide UI state permanently. It is the fast escape hatch when the view
    becomes CModel spaghetti.
    """
    view.show_buildings.set(True)
    view.show_vehicles.set(False)
    view.show_decorations.set(False)
    view.show_visual_mesh.set(False)
    view.show_cmodel_overlay.set(True)
    view.building_scope.set("Selected")
    view.decoration_scope.set("Off")
    view.visual_scope.set("Selected")
    view.collision_scope.set("Selected")
    view.cmodel_color_mode.set("Addr Block")
    view.context_radius.set(24.0)
    view.max_buildings.set(1)
    view.max_vehicles.set(0)
    view.max_decor.set(0)
    view.rebuild_scene()
    view.fit_view()
    view.schedule_render()

def fit_capture_point(view, p):
    """Feed one world-space point into the current Fit capture.

    Capture stores both true XYZ bounds (for a useful orbit target) and
    camera-basis U/V bounds (for an exact orthographic fit at the current
    yaw/pitch).  It deliberately ignores depth because this renderer is
    orthographic.
    """
    cap = getattr(view, "_fit_capture_bounds", None)
    if cap is None:
        return False
    try:
        x = float(p[0]); y = float(p[1]); z = float(p[2])
        cy = cap["cy"]; sy = cap["sy"]
        cp = cap["cp"]; sp = cap["sp"]
        u = cy * x - sy * y
        v = sp * (sy * x + cy * y) + cp * z

        cap["mnx"] = min(cap["mnx"], x); cap["mxx"] = max(cap["mxx"], x)
        cap["mny"] = min(cap["mny"], y); cap["mxy"] = max(cap["mxy"], y)
        cap["mnz"] = min(cap["mnz"], z); cap["mxz"] = max(cap["mxz"], z)
        cap["umin"] = min(cap["umin"], u); cap["umax"] = max(cap["umax"], u)
        cap["vmin"] = min(cap["vmin"], v); cap["vmax"] = max(cap["vmax"], v)
        cap["count"] += 1
        return True
    except Exception:
        # Returning True means capture mode is active even if this malformed
        # point itself could not contribute.  The caller must still avoid
        # raster drawing while a Fit capture is in progress.
        return True

def capture_visible_fit_bounds(view):
    """Run the normal visibility pipeline without rasterizing it.

    Because _draw_nodes/_draw_entities already own the current layer, scope,
    block, preset and Section rules, reusing them here keeps Fit tied to what
    the user can actually see instead of the synthetic scene bounds.
    """
    inf = float("inf")
    cap = {
        "mnx": inf, "mny": inf, "mnz": inf,
        "mxx": -inf, "mxy": -inf, "mxz": -inf,
        "umin": inf, "umax": -inf, "vmin": inf, "vmax": -inf,
        "count": 0,
        "cy": math.cos(float(view.yaw)), "sy": math.sin(float(view.yaw)),
        "cp": math.cos(float(view.pitch)), "sp": math.sin(float(view.pitch)),
    }

    class _FitNullDraw:
        def line(view, *args, **kwargs): pass
        def ellipse(view, *args, **kwargs): pass
        def text(view, *args, **kwargs): pass
        def polygon(view, *args, **kwargs): pass

    old_cap = getattr(view, "_fit_capture_bounds", None)
    old_preview = bool(getattr(view, "_render_preview", False))
    old_suppress = bool(getattr(view, "_render_suppress_labels", False))
    old_budget_active = bool(getattr(view, "_draw_budget_active", False))
    old_budget = getattr(view, "_draw_line_budget", None)
    old_count = getattr(view, "_draw_line_count", 0)
    old_skip_budget = getattr(view, "_draw_line_skipped_budget", 0)
    old_skip_cull = getattr(view, "_draw_line_skipped_cull", 0)
    try:
        view._fit_capture_bounds = cap
        # Fit must describe the same still frame the user can actually see.
        # Keep motion-preview suppression, but RESPECT the normal still-frame
        # line budget.  The previous v95_3 capture disabled the budget and
        # therefore fitted dense context geometry that the real renderer would
        # never reach/draw, making the useful building look tiny after Fit.
        view._render_preview = False
        view._render_suppress_labels = True
        view._draw_budget_active = True
        view._draw_line_budget = view._performance_line_budget()
        view._draw_line_count = 0
        view._draw_line_skipped_budget = 0
        view._draw_line_skipped_cull = 0

        draw = _FitNullDraw()
        has_nodes = bool((view.scene or {}).get("nodes"))
        if has_nodes and view.show_node_overlay.get():
            view._draw_nodes(draw)
        view._draw_entities(draw)
    finally:
        view._fit_capture_bounds = old_cap
        view._render_preview = old_preview
        view._render_suppress_labels = old_suppress
        view._draw_budget_active = old_budget_active
        view._draw_line_budget = old_budget
        view._draw_line_count = old_count
        view._draw_line_skipped_budget = old_skip_budget
        view._draw_line_skipped_cull = old_skip_cull

    return cap if cap["count"] > 0 and cap["umin"] != inf else None

def fit_view(view):
    """Fit the camera to the geometry that is actually visible now.

    The old implementation fitted view.scene['bounds'], which includes raw
    node/entity origins even when those objects are hidden by layers, block
    filters or the Section slab.  One irrelevant point could therefore make
    the useful building occupy a tiny island in the middle of the viewport.

    This replacement captures the same draw pipeline used by render(), after
    Section clipping, then fits its current-camera orthographic U/V extents.
    """
    try:
        cap = view._capture_visible_fit_bounds()
    except Exception:
        cap = None

    if not cap:
        # Nothing drawable in the current visibility/Section state.  Keep the
        # user's camera rather than jumping out to stale full-scene bounds.
        view.schedule_render()
        return

    cw = max(200, view.canvas.winfo_width() or 1000)
    ch = max(200, view.canvas.winfo_height() or 650)

    # Orbit around the true center of the captured visible geometry.
    tx = (cap["mnx"] + cap["mxx"]) * 0.5
    ty = (cap["mny"] + cap["mxy"]) * 0.5
    tz = (cap["mnz"] + cap["mxz"]) * 0.5
    view.target = (tx, ty, tz)

    # Exact view-plane extents at the current yaw/pitch.  Leave comfortable
    # breathing room but do not let a minimum world span recreate the old
    # huge-distance problem.  Tiny selections are controlled by the scale cap.
    span_u = max(1e-6, cap["umax"] - cap["umin"])
    span_v = max(1e-6, cap["vmax"] - cap["vmin"])
    usable_w = max(80.0, float(cw) * 0.84)
    usable_h = max(80.0, float(ch) * 0.82)
    fit_scale = min(usable_w / span_u, usable_h / span_v)
    view.scale = max(0.8, min(46.0, fit_scale))

    # target is chosen for sane future orbiting; compensate any difference
    # between that XYZ center and the exact projected-bounds center with pan.
    cy = cap["cy"]; sy = cap["sy"]; cp = cap["cp"]; sp = cap["sp"]
    target_u = cy * tx - sy * ty
    target_v = sp * (sy * tx + cy * ty) + cp * tz
    center_u = (cap["umin"] + cap["umax"]) * 0.5
    center_v = (cap["vmin"] + cap["vmax"]) * 0.5
    view.pan_x = -(center_u - target_u) * view.scale
    view.pan_y =  (center_v - target_v) * view.scale
    view.schedule_render()

def fit_view_to_nodes(view):
    """Initial embedded-3D fit: frame the selected/locked node set first.

    Full Fit includes nearby buildings/entities, which can start the camera
    too far away and make the first zoom-in expensive.  Entering 3D should
    immediately focus the node work area; users can still press Fit for the
    wider scene/context view.
    """
    try:
        nodes = list((view.scene or {}).get("nodes") or [])
        if not nodes:
            view.fit_view()
            return
        cw = max(200, view.canvas.winfo_width() or 1000)
        ch = max(200, view.canvas.winfo_height() or 650)
        xs = [float(n.x) for n in nodes]
        ys = [float(n.y) for n in nodes]
        zs = [float(n.z) for n in nodes]
        mnx, mxx = min(xs), max(xs)
        mny, mxy = min(ys), max(ys)
        mnz, mxz = min(zs), max(zs)
        cx = (mnx + mxx) * 0.5
        cy = (mny + mxy) * 0.5
        cz = (mnz + mxz) * 0.5
        view.target = (cx, cy, cz)
        # Give a selected node cluster enough breathing room without
        # letting large nearby buildings force a tiny starting scale.
        span = max(mxx - mnx, mxy - mny, (mxz - mnz) * 2.5, 18.0)
        view.scale = max(1.2, min(46.0, min(cw, ch) * 0.62 / span))
        view.pan_x = 0.0
        view.pan_y = 0.0
        view.schedule_render()
    except Exception:
        view.fit_view()

def activate_initial_view(view):
    """Rebuild and render after the 3D canvas is actually mapped.

    Tk can report stale/very small widget sizes immediately after grid/pack.
    Deferring the first fit avoids the blank/stale view that only updates
    after the user zooms or presses Fit.
    """
    if getattr(view, '_initial_view_done', False):
        return
    view._initial_view_done = True
    try:
        view.update_idletasks()
    except Exception:
        pass
    # Re-entering embedded 3D reapplies the active preset.  Preserve the
    # user's live Graph ends choice across that preset refresh; on the very
    # first activation, allow the preset (including any saved override) to
    # establish its normal initial value.
    _restore_graph_ends = None
    if bool(getattr(view, '_has_activated_initial_view_once', False)):
        try:
            _restore_graph_ends = bool(view.show_graph_ends.get())
        except Exception:
            _restore_graph_ends = None
    view._apply_view_preset()
    if _restore_graph_ends is not None:
        try:
            view.show_graph_ends.set(_restore_graph_ends)
            view._on_graph_ends_changed()
        except Exception:
            pass
    view._has_activated_initial_view_once = True
    view.fit_view_to_nodes()
    try:
        view.focus_set()
    except Exception:
        pass

def top_view(view):
    view._side_view_index = None
    view.yaw = 0.0
    view.pitch = math.radians(89.0)
    view.schedule_render()

def apply_side_view_index(view, index, announce=True):
    """Apply one of the four level side views.

    The sequence is Front -> Right -> Back -> Left.  Keeping the cycle
    state explicit matters because a free mouse orbit deliberately exits
    the cycle; the next Side [S] press then starts again at Front instead
    of guessing the nearest cardinal yaw.
    """
    views = (
        ("Front", 0.0),
        ("Right", 90.0),
        ("Back", 180.0),
        ("Left", -90.0),
    )
    index = int(index) % len(views)
    name, yaw_deg = views[index]
    view._side_view_index = index
    view.yaw = math.radians(yaw_deg)
    view.pitch = 0.0
    if announce:
        try:
            view.app.status(f"3D side view: {name}")
        except Exception:
            pass
    view.schedule_render()

def side_view(view):
    """Cycle Front -> Right -> Back -> Left, restarting after free orbit."""
    current = getattr(view, '_side_view_index', None)
    next_index = 0 if current is None else (int(current) + 1) % 4
    view._apply_side_view_index(next_index)

def front_view(view):
    """Compatibility helper for callers that explicitly require Front."""
    view._apply_side_view_index(0)
