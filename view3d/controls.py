"""Toolbar, display controls, node locks, and zone filters for the 3D view.

All persistent state remains on the explicit view object.
"""

import re
import tkinter as tk
from tkinter import messagebox

from config.editor_config import _load_editor_cfg, _save_editor_cfg

def build_ui(view):
    top = tk.Frame(view, bg="#202020", height=34)
    top.pack(side="top", fill="x")
    top.pack_propagate(False)

    def btn(txt, cmd, w=None, side="left"):
        b = tk.Button(top, text=txt, command=cmd, bg="#303030", fg="#eeeeee",
                      activebackground="#3f3f3f", activeforeground="#ffffff",
                      relief="flat", font=("Consolas", 9), padx=6, pady=1)
        if w:
            b.configure(width=w)
        b.pack(side=side, padx=2, pady=4)
        return b

    btn("Fit [F]", view.fit_view)
    btn("Top [T]", view.top_view)
    btn("Side [S]", view.side_view)
    view._section_button = btn("Section", view._toggle_section)
    view._section_z_button = btn("Z", view._toggle_section_z)
    view._section_xy_button = btn("XY", view._toggle_section_xy)
    view._section_lock_z_button = btn("Lock Z", view._toggle_section_lock_z)
    view._section_lock_xy_button = btn("Lock XY", view._toggle_section_lock_xy)
    view._section_z_button.pack_forget()
    view._section_xy_button.pack_forget()
    view._section_lock_z_button.pack_forget()
    view._section_lock_xy_button.pack_forget()
    view._section_value_label = tk.Label(
        top, text="", bg="#202020", fg="#bdbdbd",
        font=("Consolas", 8), anchor="w")
    # Keep the panel visibility control at the far-right edge of the 3D toolbar.
    # It is packed before the other right-side utility controls so Tk keeps it
    # as the outermost/rightmost button.
    view._panel_button = btn("Hide panel", view._toggle_side_panel, side="right")
    view._update_section_toolbar()
    view._hints_visible = _load_editor_cfg().get('3d_hints_visible', True)
    view._hint_labels = []
    view._hints_button = tk.Button(top, text='ⓘ', command=view._toggle_hints,
                      bg="#303030", fg="#eeeeee",
                      activebackground="#3f3f3f", activeforeground="#ffffff",
                      relief="flat", font=("Consolas", 11), padx=4, pady=1)
    view._hints_button.pack(side="right", padx=2, pady=4)
    view._refresh_button = btn("Refresh [R]", view.refresh)
    view._debug_3d_button = btn("3D Debug", view.show_3d_debug)

    view._developer_toolbar_separator = tk.Frame(top, bg="#555555", width=1)
    view._developer_toolbar_separator.pack(side="left", fill="y", padx=6)

    def top_check(text, var, cmd=None):
        cb = tk.Checkbutton(top, text=text, variable=var, command=cmd or view.schedule_render,
                            bg="#202020", fg="#eeeeee", selectcolor="#303030",
                            activebackground="#202020", activeforeground="#ffffff",
                            font=("Consolas", 9), bd=0)
        cb.pack(side="left", padx=3)
        return cb

    # Grid / Nodes / Radius live in Scene Layers; Node ID lives in Labels.
    # CModel3D is controlled from the right-side layer/context controls.

    def small_btn(txt, cmd):
        b = tk.Button(top, text=txt, command=cmd, bg="#282828", fg="#dddddd",
                      activebackground="#3a3a3a", activeforeground="#ffffff",
                      relief="flat", font=("Consolas", 8), padx=4, pady=1)
        b.pack(side="left", padx=1, pady=5)
        return b

    view._context_local_button = small_btn("Local", view._context_local)
    # Context-scope controls and 3D Debug are diagnostic/advanced tools and
    # are only exposed while Developer mode is enabled.
    view._context_smaller_button = small_btn("Near-", view._context_smaller)
    view._context_larger_button = small_btn("Near+", view._context_larger)
    view._context_all_button = small_btn("AllCtx", view._context_all)

    view.info_var = tk.StringVar(value="")
    view._render_details_visible = False
    view._details_button = tk.Button(
        top, text="Details", command=view._toggle_render_details,
        bg="#282828", fg="#dddddd", activebackground="#3a3a3a",
        activeforeground="#ffffff", relief="flat", font=("Consolas", 8),
        padx=6, pady=1)
    view._details_button.pack(side="right", padx=(2, 6), pady=5)
    view._info_label = tk.Label(top, textvariable=view.info_var, bg="#202020", fg="#cccccc",
                                font=("Consolas", 9), anchor="e")
    view._sync_developer_controls()

    # Body: viewport canvas + collapsible right settings panel.
    # In embedded mode the main editor already owns the left side for node
    # editing, so the 3D view controls live on the right. The popup uses
    # the same layout for consistency.
    view._body = tk.Frame(view, bg="#000000")
    view._body.pack(side="top", fill="both", expand=True)

    view.canvas = tk.Canvas(view._body, bg="#000000", highlightthickness=0, bd=0)
    view.canvas.pack(side="left", fill="both", expand=True)

    # The 3D settings panel is now scrollable.  We keep the
    # same outer view._side_panel so the existing Hide/Show toggle still
    # works, but build the real controls inside a canvas-backed frame.
    view._side_panel = tk.Frame(view._body, bg="#181818", width=240)
    view._side_panel.pack(side="right", fill="y")
    view._side_panel.pack_propagate(False)

    view._side_panel_fixed_header = tk.Frame(view._side_panel, bg="#181818")
    view._side_panel_fixed_header.pack(side="top", fill="x")

    view._side_scroll_canvas = tk.Canvas(
        view._side_panel, bg="#181818", highlightthickness=0, bd=0,
        yscrollincrement=18
    )
    view._side_scrollbar = tk.Scrollbar(
        view._side_panel, orient="vertical", command=view._side_scroll_canvas.yview,
        bg="#282828", troughcolor="#141414", activebackground="#3a3a3a"
    )
    view._side_scroll_canvas.configure(yscrollcommand=view._side_scrollbar.set)
    view._side_scrollbar.pack(side="right", fill="y")
    view._side_scroll_canvas.pack(side="left", fill="both", expand=True)

    view._side_panel_inner = tk.Frame(view._side_scroll_canvas, bg="#181818")
    view._side_panel_window = view._side_scroll_canvas.create_window(
        (0, 0), window=view._side_panel_inner, anchor="nw"
    )

    def _update_side_scrollregion(_event=None):
        try:
            view._side_scroll_canvas.configure(scrollregion=view._side_scroll_canvas.bbox("all"))
            view._side_scroll_canvas.itemconfigure(
                view._side_panel_window,
                width=max(1, view._side_scroll_canvas.winfo_width())
            )
        except Exception:
            pass

    view._side_panel_inner.bind("<Configure>", _update_side_scrollregion)
    view._side_scroll_canvas.bind("<Configure>", _update_side_scrollregion)
    view._side_scroll_canvas.bind("<Enter>", view._bind_side_panel_mousewheel)
    view._side_scroll_canvas.bind("<Leave>", view._unbind_side_panel_mousewheel)
    view._side_panel_inner.bind("<Enter>", view._bind_side_panel_mousewheel)
    view._side_panel_inner.bind("<Leave>", view._unbind_side_panel_mousewheel)

    view._build_side_panel(view._side_panel_inner)
    view._install_side_panel_wheel_bindings(view._side_panel_inner)

    view._helpbar = tk.Label(
        view,
        text="Right-drag/Ctrl-drag: orbit   Left-drag: pan   Wheel/+/-: zoom   T/F/S/R: view   G/N/L/Y: controls",
        bg="#181818", fg="#aaaaaa", font=("Consolas", 8), anchor="w"
    )
    view._helpbar.pack(side="bottom", fill="x")

def developer_mode_enabled(view):
    """Return the owning editor's current Developer mode state."""
    try:
        var = getattr(getattr(view, 'app', None), 'dev_mode', None)
        return bool(var.get()) if var is not None else False
    except Exception:
        return False

def sync_developer_controls(view):
    """Show 3D reverse-engineering controls only while Developer mode is active."""
    dev = view._developer_mode_enabled()
    previous = getattr(view, '_developer_controls_visible', None)

    refresh = getattr(view, '_refresh_button', None)
    debug = getattr(view, '_debug_3d_button', None)
    toolbar_sep = getattr(view, '_developer_toolbar_separator', None)
    context_local = getattr(view, '_context_local_button', None)
    context_smaller = getattr(view, '_context_smaller_button', None)
    context_larger = getattr(view, '_context_larger_button', None)
    context_all = getattr(view, '_context_all_button', None)
    details = getattr(view, '_details_button', None)
    info = getattr(view, '_info_label', None)

    if dev:
        # Restore the complete Developer-only toolbar group in its original
        # order immediately after Refresh.  Using explicit ``after`` anchors
        # keeps live mode switching deterministic after pack_forget().
        ordered = (
            (debug, dict(side='left', padx=2, pady=4), refresh),
            (toolbar_sep, dict(side='left', fill='y', padx=6), debug),
            (context_local, dict(side='left', padx=1, pady=5), toolbar_sep),
            (context_smaller, dict(side='left', padx=1, pady=5), context_local),
            (context_larger, dict(side='left', padx=1, pady=5), context_smaller),
            (context_all, dict(side='left', padx=1, pady=5), context_larger),
        )
        for widget, opts, after_widget in ordered:
            try:
                if widget is not None and not widget.winfo_manager():
                    if after_widget is not None and after_widget.winfo_manager():
                        opts = dict(opts, after=after_widget)
                    widget.pack(**opts)
            except Exception:
                pass
        try:
            if details is not None and not details.winfo_manager():
                details.pack(side='right', padx=(2, 6), pady=5)
        except Exception:
            pass
        view._sync_render_details_visibility()
    else:
        for widget in (debug, toolbar_sep, context_local, context_smaller, context_larger, context_all, details, info):
            try:
                if widget is not None and widget.winfo_manager():
                    widget.pack_forget()
            except Exception:
                pass

    try:
        helpbar = getattr(view, '_helpbar', None)
        if helpbar is not None:
            helpbar.configure(text=(
                "Right-drag/Ctrl-drag: orbit   Left-drag: pan   Wheel/+/-: zoom   "
                "T/F/S/R: view   G/N/L/Y: controls   Local/Near-/Near+/AllCtx: context"
                if dev else
                "Right-drag/Ctrl-drag: orbit   Left-drag: pan   Wheel/+/-: zoom   "
                "T/F/S/R: view   G/N/L/Y: controls"
            ))
    except Exception:
        pass

    perf_dev = getattr(view, '_perf_dev_frame', None)
    if perf_dev is not None:
        try:
            if dev and not perf_dev.winfo_manager():
                perf_dev.pack(side="top", fill="x")
            elif not dev and perf_dev.winfo_manager():
                perf_dev.pack_forget()
                view.perf_drag_preview.set(True)
                view.perf_motion_lod.set(True)
                view.perf_offscreen_cull.set(True)
        except Exception:
            pass

    vm_chk = getattr(view, '_visual_mesh_broken_chk', None)
    if vm_chk is not None:
        try:
            if dev and not vm_chk.winfo_manager():
                anchor = getattr(view, '_cmodel_overlay_chk', None)
                if anchor is not None and anchor.winfo_manager():
                    vm_chk.pack(side="top", fill="x", padx=4, pady=1, before=anchor)
                else:
                    vm_chk.pack(side="top", fill="x", padx=4, pady=1)
            elif not dev and vm_chk.winfo_manager():
                vm_chk.pack_forget()
                view.show_visual_mesh.set(False)
        except Exception:
            pass

    view._developer_controls_visible = dev
    view._sync_node_set_zone_labels(refresh_controls=True)
    try:
        view.schedule_render()
    except Exception:
        pass

def sync_node_set_zone_labels(view, refresh_controls=False):
    """Use creator-facing Zone wording outside Developer mode.

    The stored field remains b14.  Only the 3D Node Set presentation changes:
    normal mode says Zone, while Developer mode exposes the raw b14 name.
    """
    dev = view._developer_mode_enabled()
    try:
        check = getattr(view, '_assign_zone_check', None)
        if check is not None:
            check.configure(text=("Set b14 on lock" if dev else "Set Zone on lock"))
    except Exception:
        pass
    try:
        help_label = getattr(view, '_node_set_help_label', None)
        if help_label is not None:
            help_label.configure(
                text=(
                    "Lock a group of nodes that can be editable directly in 3D View. "
                    "Use b14 lock to assign selected nodes to a b14 zone. Useful for interiors."
                    if dev else
                    "Lock a group of nodes that can be editable directly in 3D View. "
                    "Use Zone on lock to assign selected nodes to a zone. Useful for interiors."
                )
            )
    except Exception:
        pass
    try:
        if refresh_controls and getattr(view, '_zone_filter_frame', None) is not None:
            view._refresh_zone_filter_controls()
        else:
            view._update_zone_filter_summary()
    except Exception:
        pass

def toggle_render_details(view):
    """Show/hide the top-right 3D render timing/details readout."""
    try:
        view._render_details_visible = not bool(getattr(view, '_render_details_visible', False))
        view._sync_render_details_visibility()
    except Exception:
        pass

def sync_render_details_visibility(view):
    try:
        label = getattr(view, '_info_label', None)
        button = getattr(view, '_details_button', None)
        if label is None:
            return
        if bool(getattr(view, '_render_details_visible', False)):
            if not label.winfo_ismapped():
                label.pack(side="right", padx=(4, 6))
            if button is not None:
                button.configure(text="Details")
        else:
            if label.winfo_ismapped():
                label.pack_forget()
            if button is not None:
                button.configure(text="Details")
    except Exception:
        pass

def bind_events(view):
    view.bind("<Configure>", lambda e: view.schedule_render())
    view.canvas.bind("<Shift-ButtonPress-1>", lambda e: view._start_box_select(e, "add"))
    view.canvas.bind("<Shift-B1-Motion>", view._box_select_drag)
    view.canvas.bind("<Shift-ButtonRelease-1>", view._box_select_release)
    view.canvas.bind("<Control-ButtonPress-1>", lambda e: view._start_box_select(e, "remove"))
    view.canvas.bind("<Control-B1-Motion>", view._box_select_drag)
    view.canvas.bind("<Control-ButtonRelease-1>", view._box_select_release)
    view.canvas.bind("<ButtonPress-1>", lambda e: view._start_drag(e, "pan"))
    view.canvas.bind("<B1-Motion>", view._drag)
    view.canvas.bind("<ButtonPress-3>", lambda e: view._start_drag(e, "orbit"))
    view.canvas.bind("<B3-Motion>", view._drag)
    view.canvas.bind("<Control-ButtonPress-3>", lambda e: view._start_drag(e, "orbit"))
    view.canvas.bind("<Control-B3-Motion>", view._drag)
    view.canvas.bind("<ButtonRelease-1>", view._on_left_release)
    view.canvas.bind("<ButtonRelease-3>", view._end_drag)
    view.canvas.bind("<Motion>", view._on_pointer_motion)
    view.canvas.bind("<MouseWheel>", view._wheel)
    view.canvas.bind("<Button-4>", lambda e: view._zoom_at(1.12))
    view.canvas.bind("<Button-5>", lambda e: view._zoom_at(1/1.12))

    for key, fn in {
        "t": view.top_view, "T": view.top_view,
        "f": view.fit_view, "F": view.fit_view,
        "s": view.side_view, "S": view.side_view,
        "g": view._toggle_grid, "G": view._toggle_grid,
        "u": lambda: view.app._clear_node_selection_shortcut(),
        "U": lambda: view.app._clear_node_selection_shortcut(),
        "n": view._toggle_node_overlay, "N": view._toggle_node_overlay,
        "c": view._toggle_context, "C": view._toggle_context,
        "b": view._toggle_buildings, "B": view._toggle_buildings,
        "v": view._toggle_vehicles, "V": view._toggle_vehicles,
        "d": view._toggle_decor, "D": view._toggle_decor,
        "r": view.refresh, "R": view.refresh,
        "l": view._toggle_levels, "L": view._toggle_levels,
        "y": view._toggle_labels, "Y": view._toggle_labels,
        "bracketleft": view.prev_node,
        "bracketright": view.next_node,
        "plus": lambda: view._zoom_at(1.15),
        "equal": lambda: view._zoom_at(1.15),
        "minus": lambda: view._zoom_at(1/1.15),
        "9": lambda: view._zoom_at(1.15),
        "3": lambda: view._zoom_at(1/1.15),
        "Prior": lambda: view._zoom_at(1.15),
        "Next": lambda: view._zoom_at(1/1.15),
        "Escape": view._escape_action,
    }.items():
        view.bind(f"<KeyPress-{key}>", lambda e, fn=fn: fn())
    _NUMPAD_NODE_DIRECTIONS = {
        104: (0, 1),    # keypad 8
        98:  (0, -1),   # keypad 2
        100: (-1, 0),   # keypad 4
        102: (1, 0),    # keypad 6
    }
    def _numpad_node_nudge(event, dx, dy):
        if getattr(event, 'keycode', None) not in _NUMPAD_NODE_DIRECTIONS:
            return None
        amount = 0.01 if (int(getattr(event, 'state', 0)) & 0x0001) else 1.0
        view.app._nudge_node(dx * amount, dy * amount)
        return 'break'
    for _key, (_dx, _dy) in (
            ('8', (0, 1)), ('2', (0, -1)),
            ('4', (-1, 0)), ('6', (1, 0))):
        view.bind(
            f"<KeyPress-{_key}>",
            lambda e, dx=_dx, dy=_dy: _numpad_node_nudge(e, dx, dy))
    view.bind("<Control-z>", lambda e: (view.app.undo(), 'break'))
    view.bind("<Control-Z>", lambda e: (view.app.undo(), 'break'))
    view.bind("<Control-y>", lambda e: (view.app.redo(), 'break'))
    view.bind("<Control-Y>", lambda e: (view.app.redo(), 'break'))
    view.focus_set()

def request_close(view):
    """Close the containing 3D view if the host provided a close action.

    In popup mode this destroys the Toplevel wrapper. In future embedded
    mode it can simply switch back to the 2D editor view.
    """
    cb = getattr(view, "_on_close", None)
    if callable(cb):
        try:
            cb()
            return
        except Exception:
            pass
    try:
        view.destroy()
    except Exception:
        pass

def toggle_grid(view):
    view.show_grid.set(not view.show_grid.get())
    view.schedule_render()

def toggle_node_overlay(view):
    view.show_node_overlay.set(not view.show_node_overlay.get())
    view.schedule_render()

def toggle_buildings(view):
    view.show_buildings.set(not view.show_buildings.get())
    view._sync_context_string()
    view.schedule_render()

def toggle_vehicles(view):
    view.show_vehicles.set(not view.show_vehicles.get())
    view.schedule_render()

def toggle_decor(view):
    view.show_decorations.set(not view.show_decorations.get())
    view._sync_context_string()
    view.schedule_render()

def toggle_context(view):
    if view.show_buildings.get() or view.show_decorations.get() or view.show_vehicles.get():
        view.show_buildings.set(False)
        view.show_vehicles.set(False)
        view.show_decorations.set(False)
    else:
        view.show_buildings.set(True)
        view.show_vehicles.set(True)
        view.show_decorations.set(True)
    view._sync_context_string()
    view.schedule_render()

def context_local(view):
    """Reset 3D context to focused node/local-building defaults."""
    view.context_radius.set(32.0)
    view.max_buildings.set(10)
    view.max_vehicles.set(8)
    view.max_decor.set(18)
    view.show_buildings.set(True)
    view.show_vehicles.set(False)
    view.show_decorations.set(False)
    view.show_visual_mesh.set(False)
    view.show_cmodel_overlay.set(True)
    view.building_scope.set("Local")
    view.decoration_scope.set("Local")
    view.visual_scope.set("Local")
    view.collision_scope.set("Selected")
    view.ground_mode.set("Solid + Grid")
    view.rebuild_scene()
    view.fit_view()
    view.schedule_render()

def context_smaller(view):
    view.context_radius.set(max(12.0, float(view.context_radius.get() or 32.0) * 0.75))
    view.max_buildings.set(max(3, int(round((view.max_buildings.get() or 10) * 0.75))))
    view.max_vehicles.set(max(2, int(round((view.max_vehicles.get() or 8) * 0.75))))
    view.max_decor.set(max(4, int(round((view.max_decor.get() or 18) * 0.75))))
    if str(view.building_scope.get()) == "Full":
        view.building_scope.set("Local")
    if str(view.decoration_scope.get()) == "Full":
        view.decoration_scope.set("Local")
    if str(view.visual_scope.get()) == "Full":
        view.visual_scope.set("Local")
    if str(view.collision_scope.get()) == "Full":
        view.collision_scope.set("Local")
    view.rebuild_scene()
    view.fit_view()
    view.schedule_render()

def context_larger(view):
    view.context_radius.set(min(96.0, float(view.context_radius.get() or 32.0) * 1.35))
    view.max_buildings.set(min(48, int(round((view.max_buildings.get() or 10) * 1.35)) + 1))
    view.max_vehicles.set(min(48, int(round((view.max_vehicles.get() or 8) * 1.35)) + 1))
    view.max_decor.set(min(96, int(round((view.max_decor.get() or 18) * 1.35)) + 2))
    if float(view.context_radius.get() or 0) >= 80.0:
        view.building_scope.set("Full")
        view.decoration_scope.set("Full")
        view.visual_scope.set("Full")
    view.rebuild_scene()
    view.fit_view()
    view.schedule_render()

def context_all(view):
    """Expand 3D context enough for whole selected-node regions.

    This is intentionally separate from the normal/local default because
    selecting thousands of nodes is a valid diagnostic view, but it can be
    much heavier and visually noisy for single-node building inspection.
    """
    view.context_radius.set(192.0)
    view.max_buildings.set(256)
    view.max_vehicles.set(256)
    view.max_decor.set(512)
    view.building_scope.set("Full")
    view.decoration_scope.set("Full")
    view.visual_scope.set("Full")
    view.collision_scope.set("Full")
    view.rebuild_scene()
    view.fit_view()
    view.schedule_render()

def on_levels_toggle(view):
    view._wf_entity_line_cache = {}
    view.schedule_render()

def toggle_levels(view):
    # L is the user-facing level overlay shortcut.  Toggle the visible
    # floor-coordinate markers, which work with real 3D CModel geometry.
    view.show_floor_coordinates.set(not view.show_floor_coordinates.get())
    view.schedule_render()

def toggle_radii(view):
    view.show_radii.set(not view.show_radii.get())
    view.schedule_render()

def toggle_labels(view):
    view.show_labels.set(not view.show_labels.get())
    view.schedule_render()

def sync_context_string(view):
    b = view.show_buildings.get()
    d = view.show_decorations.get()
    if b and d:
        view.context_mode.set("Buildings + Decorations")
    elif b:
        view.context_mode.set("Buildings")
    else:
        view.context_mode.set("None")

def refresh_node_set_status(view):
    try:
        app = view.app
        if hasattr(app, '_3d_node_lock_status_text'):
            txt = app._3d_node_lock_status_text()
        else:
            txt = "Node set: Live selection"
        view.node_set_status.set(txt)
        active = bool(getattr(app, '_locked_3d_node_set_active', False))
        if view._node_set_lock_btn is not None:
            view._node_set_lock_btn.configure(text=("Relock current selection" if active else "Lock selected nodes"))
        if view._node_set_unlock_btn is not None:
            view._node_set_unlock_btn.configure(state=("normal" if active else "disabled"))
    except Exception:
        pass

def lock_selected_nodes_for_3d(view):
    try:
        assign_b14 = None
        if bool(getattr(view, 'assign_b14_on_lock', tk.BooleanVar(value=False)).get()):
            try:
                assign_b14 = int(str(view.assign_b14_value.get()).strip()) & 0xFF
            except Exception:
                if view._developer_mode_enabled():
                    title = "Set b14 on lock"
                    msg = "Enter a valid b14 value from 0 to 255."
                else:
                    title = "Set Zone on lock"
                    msg = "Enter a valid Zone value from 0 to 255."
                messagebox.showerror(title, msg, parent=view)
                return

            ids = set()
            try:
                if hasattr(view.app, '_current_selection_ids_for_3d_lock'):
                    ids = set(view.app._current_selection_ids_for_3d_lock())
            except Exception:
                ids = set()
            values = {}
            try:
                for nid in ids:
                    n = view.app.nodes[int(nid)]
                    z = int(getattr(n, 'b14', 0)) & 0xFF
                    values[z] = values.get(z, 0) + 1
            except Exception:
                pass
            if len(values) > 1:
                if view._developer_mode_enabled():
                    detail = "\n".join(f"  b14 {z}: {c} node(s)" for z, c in sorted(values.items()))
                    msg = (
                        "Warning: selected nodes contain multiple b14 AreaId values.\n\n"
                        "Changing all selected nodes to one b14 AreaId may break room logic,\n"
                        "tactical zones, takedown behavior, or scripted AI behavior that depends\n"
                        "on the current area layout.\n\n"
                        f"Current detected b14 values:\n{detail}\n\n"
                        "b17 SecondaryAreaId values can depend on relationships between different\n"
                        "b14 areas. This operation will not recalculate b17 values or validate\n"
                        "takedown/transition links.\n\n"
                        f"Proceed and set b14={assign_b14} for the selected nodes?"
                    )
                    title = "Dangerous b14 reassignment"
                else:
                    detail = "\n".join(f"  Zone {z}: {c} node(s)" for z, c in sorted(values.items()))
                    msg = (
                        "Warning: selected nodes contain multiple Zone values.\n\n"
                        "Changing all selected nodes to one Zone may break room logic,\n"
                        "tactical zones, takedown behavior, or scripted AI behavior that depends\n"
                        "on the current area layout.\n\n"
                        f"Current detected Zones:\n{detail}\n\n"
                        "Secondary zone metadata can depend on relationships between different\n"
                        "Zones. This operation will not recalculate that metadata or validate\n"
                        "takedown/transition links.\n\n"
                        f"Proceed and set Zone={assign_b14} for the selected nodes?"
                    )
                    title = "Dangerous Zone reassignment"
                if not messagebox.askyesno(title, msg, parent=view):
                    return
        if hasattr(view.app, 'lock_3d_selected_nodes'):
            view.app.lock_3d_selected_nodes(assign_b14=assign_b14)
        else:
            view.status_var.set("3D node lock unavailable") if hasattr(view, 'status_var') else None
    finally:
        view._refresh_node_set_status()
        view._refresh_zone_filter_controls()
        view.rebuild_scene()
        view.schedule_render()

def unlock_3d_node_set(view):
    try:
        if hasattr(view.app, 'unlock_3d_node_set'):
            view.app.unlock_3d_node_set(reason='manual')
    finally:
        view._refresh_node_set_status()
        view.rebuild_scene()
        view.schedule_render()

def refresh(view):
    view.rebuild_scene()
    view.fit_view()
    view.schedule_render()

def zone_action_palette(view):
    """Palette for compact b14 zone action buttons."""
    try:
        app = getattr(view, 'app', None)
        light_var = getattr(app, 'light_panels', None)
        light = bool(light_var.get()) if light_var is not None else False
    except Exception:
        light = False
    if light:
        return {
            'frame': '#f3f3f3',
            'bg': '#dbe8f7',
            'fg': '#10243a',
            'active_bg': '#c6dbf4',
            'active_fg': '#07192a',
            'idle_border': '#f3f3f3',
            'hover_border': '#3f6f9f',
        }
    return {
        'frame': '#181818',
        'bg': '#26384f',
        'fg': '#f0f6ff',
        'active_bg': '#365a80',
        'active_fg': '#ffffff',
        'idle_border': '#181818',
        'hover_border': '#8cbcff',
    }

def zone_button(view, parent, text, cmd):
    """Small b14 action button with a stable hover outline.

    Uses a wrapper frame for the outline so the button never grows/shifts.
    This is a plain hover affordance only; no selected/active state is kept.
    """
    pal = view._zone_action_palette()
    cell = tk.Frame(parent, bg=pal['idle_border'], padx=1, pady=1)
    cell.pack(side="right", padx=(2, 0))
    b = tk.Button(cell, text=text, command=cmd,
                  bg=pal['bg'], fg=pal['fg'], activebackground=pal['active_bg'],
                  activeforeground=pal['active_fg'], relief="flat", font=("Consolas", 7),
                  padx=4, pady=0, bd=0, highlightthickness=0)
    b.pack(side="top", fill="x")

    def _enter(_e=None, c=cell):
        try:
            c.configure(bg=view._zone_action_palette()['hover_border'])
        except Exception:
            pass

    def _leave(_e=None, c=cell):
        try:
            c.configure(bg=view._zone_action_palette()['idle_border'])
        except Exception:
            pass

    b.bind("<Enter>", _enter)
    b.bind("<Leave>", _leave)
    cell.bind("<Enter>", _enter)
    cell.bind("<Leave>", _leave)
    return b

def show_all_zone_nodes(view, zid):
    try:
        try:
            if int(zid) in view._zone_filter_vars:
                view._zone_filter_vars[int(zid)].set(True)
        except Exception:
            pass
        if hasattr(view.app, 'set_3d_b14_zone_scope'):
            view.app.set_3d_b14_zone_scope(int(zid), select=False)
        view._refresh_node_set_status()
        view._refresh_zone_filter_controls()
        view.rebuild_scene()
        view.fit_view()
        view.schedule_render()
    except Exception:
        pass

def select_zone_nodes(view, zid):
    try:
        try:
            if int(zid) in view._zone_filter_vars:
                view._zone_filter_vars[int(zid)].set(True)
        except Exception:
            pass
        if hasattr(view.app, 'set_3d_b14_zone_scope'):
            view.app.set_3d_b14_zone_scope(int(zid), select=True)
        view._refresh_node_set_status()
        view._refresh_zone_filter_controls()
        view.rebuild_scene()
        view.fit_view()
        view.schedule_render()
    except Exception:
        pass

def restore_locked_zone_scope(view):
    try:
        if hasattr(view.app, 'restore_3d_locked_anchor'):
            view.app.restore_3d_locked_anchor()
        view._refresh_node_set_status()
        view._refresh_zone_filter_controls()
        view.rebuild_scene()
        view.fit_view()
        view.schedule_render()
    except Exception:
        pass

def detected_b14_counts(view):
    counts = {}
    try:
        for nid in view._zone_source_node_ids_for_3d():
            n = view.app.nodes[int(nid)]
            z = int(getattr(n, 'b14', 0)) & 0xFF
            counts[z] = counts.get(z, 0) + 1
    except Exception:
        pass
    return counts

def sync_zone_filter_disclosure(view):
    """Show only the Zone filter summary while its long control chain is folded."""
    try:
        collapsed = bool(view._zone_filter_collapsed.get())
    except Exception:
        collapsed = True
    try:
        btn = getattr(view, '_zone_filter_toggle_btn', None)
        if btn is not None:
            btn.configure(text=("+ " if collapsed else "- ") + str(view._zone_filter_summary.get()))
    except Exception:
        pass
    try:
        details = getattr(view, '_zone_filter_details_frame', None)
        if details is not None:
            if collapsed:
                if details.winfo_manager():
                    details.pack_forget()
            elif not details.winfo_manager():
                help_label = getattr(view, '_node_set_help_label', None)
                if help_label is not None and help_label.winfo_manager():
                    details.pack(side="top", fill="x", padx=0, pady=0, before=help_label)
                else:
                    details.pack(side="top", fill="x", padx=0, pady=0)
    except Exception:
        pass
    try:
        view.after_idle(lambda: view._side_scroll_canvas.configure(
            scrollregion=view._side_scroll_canvas.bbox("all")))
    except Exception:
        pass

def toggle_zone_filter_details(view):
    try:
        view._zone_filter_collapsed.set(not bool(view._zone_filter_collapsed.get()))
    except Exception:
        return
    view._sync_zone_filter_disclosure()

def refresh_zone_filter_controls(view):
    """Rebuild/check b14 zone visibility controls for the current 3D set."""
    counts = view._detected_b14_counts()
    if counts == getattr(view, '_last_zone_counts', None):
        return
    view._last_zone_counts = dict(counts) if counts else {}
    try:
        # Preserve existing on/off choices for zones that still exist; new
        # zones default visible so lock/filter never hides data by surprise.
        for zid in sorted(counts):
            if zid not in view._zone_filter_vars:
                view._zone_filter_vars[zid] = tk.BooleanVar(value=True)
        for zid in list(view._zone_filter_vars.keys()):
            if zid not in counts:
                del view._zone_filter_vars[zid]

        frame = getattr(view, '_zone_filter_frame', None)
        if frame is not None:
            for child in frame.winfo_children():
                child.destroy()
            dev = view._developer_mode_enabled()
            if not counts:
                _zpal = view._side_section_palette()
                tk.Label(frame, text=("No b14 zones detected" if dev else "No zones detected"),
                         bg=_zpal['body_bg'], fg=_zpal.get('control_fg', '#eeeeee'),
                         font=("Consolas", 9), anchor="w").pack(side="top", fill="x", padx=2, pady=1)
            else:
                for zid in sorted(counts):
                    var = view._zone_filter_vars[zid]
                    row = tk.Frame(frame, bg="#181818")
                    row.pack(side="top", fill="x", padx=2, pady=0)
                    text = (f"b14 {zid}  ({counts[zid]} nodes)" if dev
                            else f"Zone {zid}  ({counts[zid]} nodes)")
                    tk.Checkbutton(row, text=text, variable=var, command=view._on_zone_filter_changed,
                                   bg="#181818", fg="#eeeeee", selectcolor="#303030",
                                   activebackground="#181818", activeforeground="#ffffff",
                                   font=("Consolas", 8), bd=0, anchor="w").pack(side="left", fill="x", expand=True)
                    view._zone_button(row, "select", lambda z=zid: view._select_zone_nodes(z))
                    view._zone_button(row, "all", lambda z=zid: view._show_all_zone_nodes(z))
                view._install_side_panel_wheel_bindings(frame)
        view._update_zone_filter_summary(counts)
        # These widgets are rebuilt after the panel theme was applied, so re-theme the
        # subtree to keep them in light mode (no-op in dark mode).
        try:
            app = getattr(view, 'app', None)
            if frame is not None and app is not None and getattr(app, '_panel_light', False):
                app._apply_panel_theme(True, root=frame)
        except Exception:
            pass
    except Exception:
        pass

def update_zone_filter_summary(view, counts=None):
    try:
        if counts is None:
            counts = view._detected_b14_counts()
        dev = view._developer_mode_enabled()
        prefix = "b14 filter" if dev else "Zone filter"
        if not counts:
            view._zone_filter_summary.set(f"{prefix}: no zones")
            view._sync_zone_filter_disclosure()
            return
        enabled = [z for z, v in view._zone_filter_vars.items() if z in counts and bool(v.get())]
        view._zone_filter_summary.set(f"{prefix}: {len(enabled)}/{len(counts)} shown")
        view._sync_zone_filter_disclosure()
    except Exception:
        pass

def enabled_b14_filter_set(view):
    counts = view._detected_b14_counts()
    if not counts:
        return None
    enabled = set()
    for zid in counts:
        var = view._zone_filter_vars.get(zid)
        if var is None or bool(var.get()):
            enabled.add(int(zid))
    return enabled

def set_all_zone_filters(view, visible=True):
    try:
        for var in view._zone_filter_vars.values():
            var.set(bool(visible))
        view._on_zone_filter_changed()
    except Exception:
        pass

def on_zone_filter_changed(view):
    try:
        view._update_zone_filter_summary()
        view.rebuild_scene()
        view.schedule_render()
    except Exception:
        pass

def refresh_cmodel_block_menu(view, entities):
    view._refresh_block_layers_for(entities, "building",
        getattr(view, '_bld_block_frame', None),
        '_building_block_visible', '_bld_block_layer_ids')
    view._refresh_block_layers_for(entities, "decoration",
        getattr(view, '_decor_block_frame', None),
        '_decor_block_visible', '_decor_block_layer_ids')

def reset_block_visibility(view):
    for vis_attr in ('_building_block_visible', '_decor_block_visible'):
        vis = getattr(view, vis_attr, {})
        for bv in vis.values():
            bv.set(True)
    view._invalidate_block_caches()

def invalidate_block_caches(view):
    for attr in ("_wf_cmodel_world_cache", "_wf_visual_world_cache"):
        try:
            c = getattr(view, attr, None)
            if isinstance(c, dict):
                c.clear()
        except Exception:
            pass

def visible_building_blocks(view):
    vis = getattr(view, '_building_block_visible', {})
    if not vis:
        return None
    hidden = {k for k, v in vis.items() if not v.get()}
    if not hidden:
        return None
    return {k for k, v in vis.items() if v.get()}


def refresh_node_focus_color_button(view):
    btn = getattr(view, '_node_focus_color_button', None)
    if btn is None:
        return
    text = str(getattr(view, 'node_focus_geometry_color', tk.StringVar(
        value=view.NODE_FOCUS_DEFAULT_HEX)).get() or view.NODE_FOCUS_DEFAULT_HEX).upper()
    rgb = view._node_focus_hex_to_rgb(text)
    # Pick readable text independently of dark/light panel mode because the
    # swatch itself is the selected scene color.
    lum = 0.2126*rgb[0] + 0.7152*rgb[1] + 0.0722*rgb[2]
    fg = '#111111' if lum >= 150 else '#ffffff'
    try:
        btn.configure(text=text, bg=text, fg=fg,
                      activebackground=text, activeforeground=fg,
                      highlightbackground=view._side_section_palette().get(
                          'control_border', '#686868'),
                      highlightcolor=view._side_section_palette().get(
                          'control_border', '#686868'))
    except Exception:
        pass

def set_node_focus_color(view, value, *, schedule=True, persist=False):
    """Change only the draw-time Node Focus color; never rebuild geometry."""
    rgb = view._node_focus_hex_to_rgb(value)
    text = view._node_focus_rgb_to_hex(rgb)
    view._node_focus_rgb = rgb
    try:
        view.node_focus_geometry_color.set(text)
    except Exception:
        pass
    view._refresh_node_focus_color_button()
    if persist:
        try:
            cfg = _load_editor_cfg()
            cfg['3d_node_focus_geometry_color'] = text
            _save_editor_cfg(cfg)
        except Exception:
            pass
    if schedule:
        # schedule_render() is already 16 ms coalesced. Deliberately do NOT
        # rebuild the scene, invalidate CModel/3DI caches, or refit camera.
        view.schedule_render()

def open_node_focus_color_dialog(view):
    """Centered themed RGB editor with coalesced live 3D preview."""
    try:
        before_hex = str(view.node_focus_geometry_color.get() or
                         view.NODE_FOCUS_DEFAULT_HEX).upper()
    except Exception:
        before_hex = view.NODE_FOCUS_DEFAULT_HEX
    start_rgb = view._node_focus_hex_to_rgb(before_hex)

    win = tk.Toplevel(view)
    win.title("Node Focus Geometry Color")
    win.transient(view.winfo_toplevel())
    win.resizable(False, False)
    pal = view._side_section_palette()
    bg = pal.get('body_bg', '#181818')
    fg = pal.get('control_fg', '#eeeeee')
    sub = pal.get('sub_label_fg', '#aaaaaa')
    ctrl_bg = pal.get('control_bg', '#353535')
    ctrl_active = pal.get('control_active_bg', '#494949')
    border = pal.get('control_border', '#686868')
    select_bg = '#ffffff' if view._side_panel_light_enabled() else '#303030'
    win.configure(bg=bg)

    outer = tk.Frame(win, bg=bg, padx=12, pady=10)
    outer.pack(fill='both', expand=True)
    tk.Label(outer, text="Node Focus geometry color", bg=bg, fg=fg,
             font=("Consolas", 10, "bold"), anchor='w').pack(fill='x')
    tk.Label(outer,
             text="Live preview changes only the 3D draw color; geometry is not rebuilt.",
             bg=bg, fg=sub, font=("Consolas", 8), anchor='w',
             justify='left', wraplength=330).pack(fill='x', pady=(2, 8))

    swatch = tk.Frame(outer, bg=before_hex, height=30,
                      highlightthickness=1, highlightbackground=border)
    swatch.pack(fill='x', pady=(0, 8))
    swatch.pack_propagate(False)

    r_var = tk.IntVar(value=start_rgb[0])
    g_var = tk.IntVar(value=start_rgb[1])
    b_var = tk.IntVar(value=start_rgb[2])
    hex_var = tk.StringVar(value=before_hex)
    syncing = {'value': False}

    def _apply_rgb(*_args):
        if syncing['value']:
            return
        try:
            rgb = (int(r_var.get()), int(g_var.get()), int(b_var.get()))
        except Exception:
            return
        text = view._node_focus_rgb_to_hex(rgb)
        syncing['value'] = True
        try:
            hex_var.set(text)
            swatch.configure(bg=text)
        finally:
            syncing['value'] = False
        view._set_node_focus_color(text, schedule=True, persist=False)

    # Small channel markers make it immediately obvious which slider edits
    # red, green, or blue without relying on the single-letter labels alone.
    # These are UI-only swatches and have no effect on render/cache state.
    for label, var, channel_color in (("R", r_var, "#E53935"),
                                       ("G", g_var, "#35A853"),
                                       ("B", b_var, "#3F7EE8")):
        row = tk.Frame(outer, bg=bg)
        row.pack(fill='x', pady=1)
        marker = tk.Frame(row, width=9, height=9, bg=channel_color,
                          highlightthickness=1, highlightbackground=border)
        marker.pack(side='left', padx=(2, 5))
        marker.pack_propagate(False)
        tk.Label(row, text=label, width=2, bg=bg, fg=channel_color,
                 font=("Consolas", 9, "bold")).pack(side='left')
        sc = tk.Scale(row, from_=0, to=255, orient='horizontal',
                      variable=var, command=lambda _v: _apply_rgb(),
                      showvalue=True, length=245, resolution=1,
                      bg=bg, fg=fg, troughcolor=ctrl_bg,
                      activebackground=ctrl_active, highlightthickness=0,
                      bd=0, font=("Consolas", 8))
        sc.pack(side='left', fill='x', expand=True)

    hex_row = tk.Frame(outer, bg=bg)
    hex_row.pack(fill='x', pady=(7, 2))
    tk.Label(hex_row, text="Hex", bg=bg, fg=fg,
             font=("Consolas", 9)).pack(side='left')
    ent = tk.Entry(hex_row, textvariable=hex_var, width=10,
                   bg=select_bg, fg=fg, insertbackground=fg,
                   relief='flat', font=("Consolas", 9),
                   highlightthickness=1, highlightbackground=border,
                   highlightcolor=border)
    ent.pack(side='left', padx=(8, 0))

    def _apply_hex(_event=None):
        text = str(hex_var.get() or '').strip().upper()
        if not re.fullmatch(r'#[0-9A-F]{6}', text):
            return
        rgb = view._node_focus_hex_to_rgb(text)
        syncing['value'] = True
        try:
            r_var.set(rgb[0]); g_var.set(rgb[1]); b_var.set(rgb[2])
            swatch.configure(bg=text)
        finally:
            syncing['value'] = False
        view._set_node_focus_color(text, schedule=True, persist=False)

    ent.bind('<Return>', _apply_hex)
    ent.bind('<FocusOut>', _apply_hex)

    buttons = tk.Frame(outer, bg=bg)
    buttons.pack(fill='x', pady=(10, 0))

    def _button(parent, text, cmd, side='right'):
        b = tk.Button(parent, text=text, command=cmd, bg=ctrl_bg, fg=fg,
                      activebackground=ctrl_active, activeforeground=fg,
                      relief='flat', bd=0, highlightthickness=1,
                      highlightbackground=border, highlightcolor=border,
                      font=("Consolas", 9), padx=8, pady=2)
        b.pack(side=side, padx=(5, 0) if side == 'right' else (0, 5))
        return b

    def _reset():
        text = view.NODE_FOCUS_DEFAULT_HEX
        rgb = view._node_focus_hex_to_rgb(text)
        syncing['value'] = True
        try:
            r_var.set(rgb[0]); g_var.set(rgb[1]); b_var.set(rgb[2])
            hex_var.set(text); swatch.configure(bg=text)
        finally:
            syncing['value'] = False
        view._set_node_focus_color(text, schedule=True, persist=False)

    def _cancel():
        view._set_node_focus_color(before_hex, schedule=True, persist=False)
        try:
            win.destroy()
        except Exception:
            pass

    def _ok():
        _apply_hex()
        try:
            accepted = str(view.node_focus_geometry_color.get() or before_hex)
        except Exception:
            accepted = before_hex
        view._set_node_focus_color(accepted, schedule=True, persist=True)
        try:
            win.destroy()
        except Exception:
            pass

    _button(buttons, "Reset to default", _reset, side='left')
    _button(buttons, "OK", _ok, side='right')
    _button(buttons, "Cancel", _cancel, side='right')

    win.protocol('WM_DELETE_WINDOW', _cancel)
    win.bind('<Escape>', lambda _e: _cancel())
    win.bind('<Return>', lambda _e: _ok())
    win.update_idletasks()
    try:
        host = view.winfo_toplevel()
        hx = host.winfo_rootx(); hy = host.winfo_rooty()
        hw = host.winfo_width(); hh = host.winfo_height()
        ww = win.winfo_reqwidth(); wh = win.winfo_reqheight()
        win.geometry(f"+{max(0, hx + (hw-ww)//2)}+{max(0, hy + (hh-wh)//2)}")
    except Exception:
        pass
    try:
        win.grab_set()
    except Exception:
        pass
    ent.focus_set()

def sync_graph_end_filter_controls(view):
    """Show/hide the pink/orange Graph Ends filters to match the master toggle."""
    clear_check = getattr(view, '_graph_end_clear_check', None)
    stair_check = getattr(view, '_graph_end_stair_check', None)
    if clear_check is None or stair_check is None:
        return
    try:
        enabled = bool(view.show_graph_ends.get())
        if enabled:
            # Re-pack unconditionally.  The two rows live in a permanent
            # Graph Ends group, so this is deterministic even after a full
            # side-panel rebuild or preset restore.
            clear_check.pack_forget()
            stair_check.pack_forget()
            clear_check.pack(side='top', fill='x', padx=12, pady=(0, 0))
            stair_check.pack(side='top', fill='x', padx=12, pady=(0, 1))
        else:
            clear_check.pack_forget()
            stair_check.pack_forget()
        try:
            view.after_idle(lambda: view._side_scroll_canvas.configure(
                scrollregion=view._side_scroll_canvas.bbox('all')))
        except Exception:
            pass
    except Exception:
        pass

def visible_decor_blocks(view):
    vis = getattr(view, '_decor_block_visible', {})
    if not vis:
        return None
    hidden = {k for k, v in vis.items() if not v.get()}
    if not hidden:
        return None
    return {k for k, v in vis.items() if v.get()}
