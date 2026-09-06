"""Mouse, keyboard, selection, and connection interaction for the 3D view.

The view instance is explicit; all persistent state remains owned by
Wireframe3DView.
"""

import math
import tkinter as tk

from view3d.wf3d_debug import _wf3d_dbg

def prev_node(view):
    ids = view.scene.get("node_ids") or []
    if not ids:
        return
    try:
        i = ids.index(view.active_node_id)
    except Exception:
        i = 0
    view.active_node_id = ids[(i - 1) % len(ids)]
    view.schedule_render()

def next_node(view):
    ids = view.scene.get("node_ids") or []
    if not ids:
        return
    try:
        i = ids.index(view.active_node_id)
    except Exception:
        i = -1
    view.active_node_id = ids[(i + 1) % len(ids)]
    view.schedule_render()

def start_drag(view, event, mode):
    if mode == "pan" and view._section_is_enabled():
        hit = view._section_handle_hit(event.x, event.y)
        if hit in ("section_lower", "section_upper",
                   "section_xmin", "section_xmax", "section_ymin", "section_ymax"):
            mode = hit
            # A handle drag is visual-only until release. Cancel any older
            # queued context rebuild so nothing heavy fires underneath it.
            job = getattr(view, '_section_context_refresh_job', None)
            if job is not None:
                try:
                    view.after_cancel(job)
                except Exception:
                    pass
                view._section_context_refresh_job = None
    view._drag_last = (event.x, event.y)
    view._drag_mode = mode
    view._drag_press_xy = (event.x, event.y)
    view._drag_moved = False
    try:
        # Ctrl-left is explicitly a pan/navigation gesture; do not treat
        # its release as a node-pick click.
        view._drag_ctrl = bool(int(getattr(event, "state", 0)) & 0x0004)
    except Exception:
        view._drag_ctrl = False
    view.focus_set()

def drag(view, event):
    if not view._drag_last:
        return
    lx, ly = view._drag_last
    dx = event.x - lx
    dy = event.y - ly
    view._drag_last = (event.x, event.y)
    try:
        if view._drag_press_xy is not None:
            px, py = view._drag_press_xy
            if abs(event.x - px) + abs(event.y - py) > 5:
                view._drag_moved = True
    except Exception:
        view._drag_moved = True
    if view._drag_mode in ("section_lower", "section_upper"):
        view._section_apply_drag_delta(view._drag_mode, dy)
    elif view._drag_mode in ("section_xmin", "section_xmax", "section_ymin", "section_ymax"):
        view._section_apply_xy_drag_delta(view._drag_mode, dx, dy)
    elif view._drag_mode == "orbit":
        # Any real free orbit leaves the cardinal Side [S] cycle.  This
        # makes the next S deterministic: it starts at Front again.
        if dx or dy:
            view._side_view_index = None
        view.yaw += dx * 0.008
        view.pitch += dy * 0.006
        lim = math.radians(88.0)
        view.pitch = max(math.radians(-70.0), min(lim, view.pitch))
    else:
        view.pan_x += dx
        view.pan_y += dy
    if view._connect3d_source_id is not None:
        view._update_3d_connect_candidate(event.x, event.y, schedule=False)
    view.schedule_render()

def end_drag(view, event):
    mode = view._drag_mode
    moved = bool(getattr(view, "_drag_moved", False))
    ctrl = bool(getattr(view, "_drag_ctrl", False))
    view._drag_last = None
    view._drag_mode = None
    view._drag_press_xy = None
    view._drag_moved = False
    view._drag_ctrl = False
    if mode in ("section_lower", "section_upper",
                "section_xmin", "section_xmax", "section_ymin", "section_ymax"):
        try:
            view.canvas.configure(cursor="arrow")
        except Exception:
            pass
        view._refresh_section_context_now()

    # During 3D connect mode, a click commits to the highlighted/nearest
    # candidate. Pan/orbit drags remain normal camera navigation.
    if mode == "pan" and not moved and not ctrl and view._connect3d_source_id is not None:
        view._update_3d_connect_candidate(event.x, event.y, schedule=False)
        target = view._connect3d_candidate_id
        if target is not None:
            view._commit_3d_connection(target)
            return
        view.schedule_render()
        return

    # A plain left click in the 3D viewport selects the closest visible
    # node. Actual mouse movement remains pan/orbit, preserving camera controls.
    # Empty-space clicks clear the live editor selection; a locked working
    # set remains visible in the viewport while its live selection clears.
    if mode == "pan" and not moved and not ctrl:
        try:
            if view._select_node_from_3d_click(event.x, event.y):
                return
            app = view.app
            has_selection = (
                getattr(app, 'selected_id', None) is not None
                or bool(getattr(app, 'selected_nodes', set()) or set())
            )
            if has_selection:
                if hasattr(app, 'clear_selection_from_3d'):
                    app.clear_selection_from_3d()
                elif hasattr(app, 'select_node'):
                    app.select_node(None)
                view.active_node_id = None
                view.schedule_render()
                return
        except Exception as exc:
            _wf3d_dbg(f"3D_PICK: exception={exc}", force=True)

    # A plain right click on a selected node in the locked set opens the
    # node-edit context menu. Right-drag remains orbit. During connect mode,
    # a plain right click cancels instead.
    if mode == "orbit" and not moved and not ctrl:
        if view._connect3d_source_id is not None:
            view._cancel_3d_connection()
            return
        if view._show_3d_node_context_menu(event):
            return

    # Drag preview uses a reduced line budget. Force one final full
    # detail redraw when the camera stops moving.
    view.schedule_render()

def cancel_box_select(view):
    """Remove every transient 3D rubber-band artifact and reset its state."""
    rid = getattr(view, '_box_sel_rect_id', None)
    if rid is not None:
        try:
            view.canvas.delete(rid)
        except Exception:
            pass
    try:
        view.canvas.delete('wf3d_box_select')
    except Exception:
        pass
    view._box_sel_rect_id = None
    view._box_sel_origin = None
    view._box_sel_mode = None

def start_box_select(view, event, mode):
    if not view._is_lock_active():
        if mode == "remove":
            view._start_drag(event, "pan")
        return
    view._cancel_box_select()
    view._box_sel_origin = (event.x, event.y)
    view._box_sel_mode = mode
    view.focus_set()

def box_select_drag(view, event):
    if not hasattr(view, '_box_sel_origin') or view._box_sel_origin is None:
        if hasattr(view, '_drag_last') and view._drag_last:
            view._drag(event)
        return
    ox, oy = view._box_sel_origin
    if view._box_sel_rect_id is not None:
        view.canvas.delete(view._box_sel_rect_id)
    col = "#00ff00" if view._box_sel_mode == "add" else "#ff4444"
    view._box_sel_rect_id = view.canvas.create_rectangle(
        ox, oy, event.x, event.y,
        outline=col, width=1, dash=(4, 2),
        tags=('wf3d_box_select',))

def box_select_release(view, event):
    origin = getattr(view, '_box_sel_origin', None)
    if origin is None:
        view._cancel_box_select()
        return

    ox, oy = origin
    mode = getattr(view, '_box_sel_mode', None)
    def _refresh_selection():
        try:
            view.app.refresh_3d_views(
                rebuild=True, refit=False, reason='3d_selection')
        except Exception:
            view.schedule_render()
    # Clear the visual/state FIRST. Every early-return below is now safe.
    view._cancel_box_select()

    x0, x1 = min(ox, event.x), max(ox, event.x)
    y0, y1 = min(oy, event.y), max(oy, event.y)
    if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:
        nid = view._pick_visible_node_id(ox, oy)
        if nid is not None:
            app = view.app
            old_sel = set(getattr(app, 'selected_nodes', set()) or set())
            if mode == "add":
                if nid in old_sel:
                    new_sel = old_sel - {nid}
                else:
                    new_sel = old_sel | {nid}
            else:
                new_sel = old_sel - {nid}
            app.selected_nodes = new_sel
            app.selected_id = max(new_sel) if new_sel else None
            if new_sel:
                try:
                    app._show_info_for_node_selection()
                except Exception:
                    pass
            try:
                app._update_inspector()
                app._update_stats()
            except Exception:
                pass
            try:
                app._recolor_selection(old_sel, new_sel)
            except Exception:
                pass
            action = "deselected" if nid in old_sel and mode == "add" else "selected"
            app.status(f"3D {action} node {nid} — {len(new_sel)} total")
            _refresh_selection()
        return
    locked_ids = None
    try:
        locked_ids = view.app._get_3d_locked_node_ids()
    except Exception:
        pass
    if locked_ids is None:
        return
    hits = set()
    try:
        nodes = list((view.scene or {}).get("nodes") or [])
    except Exception:
        nodes = []
    for n in nodes:
        try:
            nid = int(n.id)
            if nid not in locked_ids:
                continue
            if not view._section_point_visible(float(n.x), float(n.y), float(n.z)):
                continue
            px, py, depth = view._project(float(n.x), float(n.y), float(n.z))
            if x0 <= px <= x1 and y0 <= py <= y1:
                hits.add(nid)
        except Exception:
            continue
    if not hits:
        return
    app = view.app
    old_sel = set(getattr(app, 'selected_nodes', set()) or set())
    if mode == "add":
        new_sel = old_sel | hits
    else:
        new_sel = old_sel - hits
    app.selected_nodes = new_sel
    app.selected_id = max(new_sel) if new_sel else None
    if new_sel:
        try:
            app._show_info_for_node_selection()
        except Exception:
            pass
    try:
        app._update_inspector()
        app._update_stats()
    except Exception:
        pass
    try:
        app._recolor_selection(old_sel, new_sel)
    except Exception:
        pass
    try:
        app.status(f"3D box {'selected' if mode == 'add' else 'deselected'} {len(hits)} node(s) — {len(new_sel)} total")
    except Exception:
        pass
    _refresh_selection()

def pick_visible_node_id(view, sx, sy, max_dist=14):
    """Return the closest visible 3D node id near a screen-space click."""
    best = None
    best_d2 = float(max_dist) * float(max_dist)
    try:
        nodes = list((view.scene or {}).get("nodes") or [])
    except Exception:
        nodes = []
    for n in nodes:
        try:
            if not view._section_point_visible(float(n.x), float(n.y), float(n.z)):
                continue
            px, py, depth = view._project(float(n.x), float(n.y), float(n.z))
            dx = float(px) - float(sx)
            dy = float(py) - float(sy)
            d2 = dx * dx + dy * dy
            if d2 <= best_d2:
                # Prefer the visually closest point; depth is only a
                # tie-breaker so overlapping vertical nodes stay usable.
                best = int(n.id)
                best_d2 = d2
        except Exception:
            continue
    return best

def select_node_from_3d_click(view, sx, sy):
    """Select a visible 3D node using the normal editor inspector path."""
    nid = view._pick_visible_node_id(sx, sy)
    if nid is None:
        return False
    app = view.app
    try:
        # In locked-set mode, do not let an accidental click outside the
        # pinned group alter/reset the working set.  The picker only scans
        # visible locked nodes, but keep the guard for safety.
        locked = app._get_3d_locked_node_ids() if hasattr(app, "_get_3d_locked_node_ids") else None
        if locked is not None and int(nid) not in locked:
            return False
    except Exception:
        pass

    # Picking a node is a selection action, not a camera-navigation action.
    # Keep the current camera target/pivot exactly where the user left it;
    # otherwise clicking adjacent nodes repeatedly yanks the view around.
    # Explicit camera controls such as Fit remain responsible for retargeting.
    if hasattr(app, "select_node_from_3d"):
        app.select_node_from_3d(int(nid))
    elif hasattr(app, "select_node"):
        app.select_node(int(nid))
    else:
        return False
    # The editor selection API toggles an already-selected node off.  Read
    # the resulting state back instead of leaving the pre-click active ID
    # behind, which otherwise leaves stale 3D highlighting until another
    # interaction forces a redraw.
    try:
        view.active_node_id = (
            int(nid) if getattr(app, 'selected_id', None) == int(nid)
            else None)
    except Exception:
        view.active_node_id = None
    try:
        view.schedule_render()
    except Exception:
        pass
    return True

def escape_action(view):
    """Esc cancels an active 3D connection before leaving the 3D view."""
    if view._connect3d_source_id is not None:
        view._cancel_3d_connection()
        return
    view._request_close()

def show_3d_node_context_menu(view, event):
    """Right-click menu for a selected node inside the locked 3D working set."""
    nid = view._pick_visible_node_id(event.x, event.y, max_dist=16)
    if nid is None:
        return False

    try:
        locked = view.app._get_3d_locked_node_ids()
    except Exception:
        locked = None
    if locked is None or int(nid) not in locked:
        return False

    selected = set(getattr(view.app, 'selected_nodes', set()) or set())
    if int(nid) not in selected:
        return False

    app = getattr(view, 'app', None)
    if app is not None and hasattr(app, '_make_ui_menu'):
        menu = app._make_ui_menu(view, persistent=False)
    else:
        menu = tk.Menu(view, tearoff=0)
    menu.add_command(
        label=f"Connect from node {nid}",
        command=lambda n=int(nid): view._start_3d_connection(n))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        try:
            menu.grab_release()
        except Exception:
            pass
    return True

def start_3d_connection(view, source_id):
    try:
        source_id = int(source_id)
    except Exception:
        return False

    try:
        locked = view.app._get_3d_locked_node_ids()
    except Exception:
        locked = None
    selected = set(getattr(view.app, 'selected_nodes', set()) or set())

    if locked is None or source_id not in locked or source_id not in selected:
        try:
            view.app.status("3D connect: select a node inside the locked node set first")
        except Exception:
            pass
        return False

    view._connect3d_source_id = source_id
    view._connect3d_candidate_id = None
    view._connect3d_mouse_xy = None
    try:
        view.canvas.configure(cursor='crosshair')
        view.focus_set()
    except Exception:
        pass
    try:
        view.app.status(
            f"3D connect: node {source_id} → hover a target and left-click. "
            "Pan/orbit/zoom stay active; right-click or Esc cancels.")
    except Exception:
        pass
    view.schedule_render()
    return True

def cancel_3d_connection(view, silent=False):
    if view._connect3d_source_id is None:
        return False
    view._connect3d_source_id = None
    view._connect3d_candidate_id = None
    view._connect3d_mouse_xy = None
    try:
        view.canvas.configure(cursor='arrow')
    except Exception:
        pass
    if not silent:
        try:
            view.app.status("3D connection cancelled.")
        except Exception:
            pass
    view.schedule_render()
    return True

def update_3d_connect_candidate(view, sx, sy, schedule=True):
    """Pick the nearest eligible visible locked node to the pointer."""
    if view._connect3d_source_id is None:
        return None

    try:
        sx = float(sx); sy = float(sy)
        view._connect3d_mouse_xy = (sx, sy)
    except Exception:
        return None

    try:
        locked = view.app._get_3d_locked_node_ids()
    except Exception:
        locked = None
    if locked is None:
        view._cancel_3d_connection(silent=True)
        return None

    source = int(view._connect3d_source_id)
    best = None
    best_d2 = 18.0 * 18.0
    try:
        nodes = list((view.scene or {}).get('nodes') or [])
    except Exception:
        nodes = []

    for n in nodes:
        try:
            nid = int(n.id)
            if nid == source or nid not in locked:
                continue
            if not view._section_point_visible(float(n.x), float(n.y), float(n.z)):
                continue
            px, py, _depth = view._project(float(n.x), float(n.y), float(n.z))
            dx = float(px) - sx
            dy = float(py) - sy
            d2 = dx * dx + dy * dy
            if d2 <= best_d2:
                best = nid
                best_d2 = d2
        except Exception:
            continue

    changed = best != view._connect3d_candidate_id
    view._connect3d_candidate_id = best
    if schedule and changed:
        view.schedule_render()
    return best

def refresh_3d_connect_candidate(view):
    if view._connect3d_source_id is None or view._connect3d_mouse_xy is None:
        return
    try:
        sx, sy = view._connect3d_mouse_xy
        view._update_3d_connect_candidate(sx, sy, schedule=False)
    except Exception:
        pass

def commit_3d_connection(view, target_id):
    try:
        source_id = int(view._connect3d_source_id)
        target_id = int(target_id)
    except Exception:
        return False

    if source_id == target_id:
        return False
    if not (0 <= source_id < len(view.app.nodes) and
            0 <= target_id < len(view.app.nodes)):
        view._cancel_3d_connection(silent=True)
        return False

    already = target_id in view.app.nodes[source_id].neighbors
    if not already:
        view.app._add_connection(
            source_id, target_id, refresh_reason='3d_add_connection')
        try:
            view.app.redraw()
            view.app._update_stats()
        except Exception:
            pass

    view._cancel_3d_connection(silent=True)
    try:
        if already:
            view.app.status(
                f"3D connect: node {source_id} is already connected to node {target_id}")
        else:
            view.app.status(f"Connected node {source_id} ↔ {target_id}")
    except Exception:
        pass
    view.schedule_render()
    return not already

def draw_3d_connect_overlay(view, draw):
    """Draw the green pending edge and subtle candidate highlight on top."""
    source_id = view._connect3d_source_id
    mouse_xy = view._connect3d_mouse_xy
    if source_id is None or mouse_xy is None:
        return

    source = None
    candidate = None
    candidate_id = view._connect3d_candidate_id
    for n in (view.scene or {}).get('nodes') or []:
        try:
            nid = int(n.id)
        except Exception:
            continue
        if nid == int(source_id):
            source = n
        if candidate_id is not None and nid == int(candidate_id):
            candidate = n
        if source is not None and (candidate_id is None or candidate is not None):
            break

    if source is None:
        return

    try:
        sx, sy, _ = view._project(float(source.x), float(source.y), float(source.z))
    except Exception:
        return

    ex, ey = mouse_xy
    if candidate is not None:
        try:
            ex, ey, _ = view._project(
                float(candidate.x), float(candidate.y), float(candidate.z))
        except Exception:
            pass

    preview_col = (70, 255, 95)
    view._draw_screen_dashed_line(
        draw, (sx, sy), (ex, ey), preview_col, width=2, dash=8.0, gap=5.0)

    if candidate is not None:
        try:
            r = 10
            draw.ellipse(
                (ex-r, ey-r, ex+r, ey+r),
                outline=preview_col, width=2)
            draw.ellipse(
                (ex-4, ey-4, ex+4, ey+4),
                outline=preview_col, width=1)
        except Exception:
            pass

def wheel(view, event):
    view._zoom_at(1.12 if event.delta > 0 else 1/1.12)

def zoom_at(view, factor):
    view.scale = max(0.25, min(90.0, view.scale * float(factor)))
    if view._connect3d_source_id is not None:
        view._refresh_3d_connect_candidate()

    # Wheel zoom is camera motion too, but unlike pan/orbit it has no drag
    # state for render() to detect.  Keep 0.50x model wires on the cheap
    # normal-resolution raster path while wheel events are arriving, then
    # restore the exact supersampled half-pixel frame once zoom input settles.
    view._zoom_motion_active = True
    old_job = getattr(view, '_zoom_motion_end_job', None)
    if old_job is not None:
        try:
            view.after_cancel(old_job)
        except Exception:
            pass
    try:
        view._zoom_motion_end_job = view.after(90, view._end_zoom_motion)
    except Exception:
        view._zoom_motion_end_job = None
    view.schedule_render()

def end_zoom_motion(view):
    view._zoom_motion_end_job = None
    if not bool(getattr(view, '_zoom_motion_active', False)):
        return
    view._zoom_motion_active = False
    # Force the final exact 0.50x supersampled still frame after the wheel
    # burst ends.  1.00x is unaffected because it never uses that path.
    view.schedule_render()
