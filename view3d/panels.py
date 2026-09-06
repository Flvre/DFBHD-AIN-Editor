"""3D side-panel construction, styles, and view presets.

Functions receive the view instance explicitly and never import the
application class.
"""

import tkinter as tk

from config.editor_config import _load_editor_cfg, _save_editor_cfg


def install_side_panel_wheel_bindings(editor, widget):
    """Make wheel scrolling work anywhere over the 3D settings panel.

    The scrollbar itself already works, but users should be able to hover
    any control in the panel and scroll naturally without aiming for the
    bar or empty canvas background.
    """
    try:
        widget.bind("<MouseWheel>", editor._on_side_panel_mousewheel, add="+")
        widget.bind("<Button-4>", editor._on_side_panel_mousewheel, add="+")
        widget.bind("<Button-5>", editor._on_side_panel_mousewheel, add="+")
    except Exception:
        pass
    try:
        for child in widget.winfo_children():
            editor._install_side_panel_wheel_bindings(child)
    except Exception:
        pass


def bind_side_panel_mousewheel(editor, _event=None):
    """Route mouse-wheel events to the scrollable 3D settings panel."""
    try:
        editor._side_scroll_canvas.bind_all("<MouseWheel>", editor._on_side_panel_mousewheel)
        editor._side_scroll_canvas.bind_all("<Button-4>", editor._on_side_panel_mousewheel)
        editor._side_scroll_canvas.bind_all("<Button-5>", editor._on_side_panel_mousewheel)
    except Exception:
        pass


def unbind_side_panel_mousewheel(editor, _event=None):
    try:
        editor._side_scroll_canvas.unbind_all("<MouseWheel>")
        editor._side_scroll_canvas.unbind_all("<Button-4>")
        editor._side_scroll_canvas.unbind_all("<Button-5>")
    except Exception:
        pass


def on_side_panel_mousewheel(editor, event):
    try:
        if getattr(event, "num", None) == 4:
            delta = -3
        elif getattr(event, "num", None) == 5:
            delta = 3
        else:
            # Windows reports multiples of 120.  Positive delta scrolls up.
            delta = -int(event.delta / 120) * 3
            if delta == 0:
                delta = -1 if event.delta > 0 else 1

        # If the event came from some unrelated widget, only hijack it when
        # the pointer is actually over the side panel. This preserves canvas
        # zoom/pan behavior outside the menu.
        root_x = editor.winfo_pointerx()
        root_y = editor.winfo_pointery()
        left = editor._side_panel.winfo_rootx()
        top = editor._side_panel.winfo_rooty()
        right = left + editor._side_panel.winfo_width()
        bottom = top + editor._side_panel.winfo_height()
        if not (left <= root_x < right and top <= root_y < bottom):
            return None

        # Only scroll when the content actually overflows the viewport.
        # With every section collapsed the panel fits, and scrolling into
        # empty space read as a bug — pin to the top instead of drifting.
        bbox = editor._side_scroll_canvas.bbox("all")
        if bbox:
            content_h = bbox[3] - bbox[1]
            view_h = editor._side_scroll_canvas.winfo_height()
            if content_h <= view_h:
                editor._side_scroll_canvas.yview_moveto(0.0)
                return "break"
        editor._side_scroll_canvas.yview_scroll(delta, "units")
        return "break"
    except Exception:
        return None


def side_panel_light_enabled(editor):
    try:
        app = getattr(editor, 'app', None)
        light_var = getattr(app, 'light_panels', None)
        return bool(light_var.get()) if light_var is not None else False
    except Exception:
        return False


def side_section_palette(editor):
    """Palette for the 3D right-panel sections.

    Expanded sections get a slightly stronger header and a thin outline so
    open categories are easier to scan.  The colors stay neutral; this is
    structure/readability polish, not a selected-state indicator.
    """
    if editor._side_panel_light_enabled():
        return {
            'panel_bg': '#eeeeee',
            'header_collapsed': '#cccccc',
            'header_expanded': '#cccccc',
            'header_hover': '#c0c0c0',
            'header_fg': '#111111',
            'header_fg_collapsed': '#555555',
            'header_fg_expanded': '#111111',
            'header_border_collapsed': '#bbbbbb',
            'header_border_expanded': '#8f8f8f',
            'header_accent': '#999999',
            'accent_active': '#111111',
            'body_bg': '#e4e4e4',
            'body_border': '#cccccc',
            'sub_label_fg': '#777777',
            'button_bg': '#d2d2d2',
            'button_active_bg': '#bebebe',
            'button_fg': '#111111',
            'control_bg': '#d0d0d0',
            'control_active_bg': '#bcbcbc',
            'control_fg': '#111111',
            'control_disabled_fg': '#777777',
            'control_border': '#929292',
            'option_bg': '#d6d6d6',
            'option_active_bg': '#c2c2c2',
            'option_border': '#989898',
            'scrollbar_bg': '#d0d0d0',
            'scrollbar_trough': '#eeeeee',
        }
    return {
        'panel_bg': '#111111',
        'header_collapsed': '#2a2a2a',
        'header_expanded': '#2a2a2a',
        'header_hover': '#333333',
        'header_fg_collapsed': '#cccccc',
        'header_fg_expanded': '#ffffff',
        'header_fg': '#eeeeee',
        'header_border_collapsed': '#1a1a1a',
        'header_border_expanded': '#666666',
        'header_accent': '#555555',
        'accent_active': '#999999',
        'body_bg': '#181818',
        'body_border': '#181818',
        'sub_label_fg': '#aaaaaa',
        'button_bg': '#353535',
        'button_active_bg': '#474747',
        'button_fg': '#eeeeee',
        'control_bg': '#353535',
        'control_active_bg': '#494949',
        'control_fg': '#eeeeee',
        'control_disabled_fg': '#7f7f7f',
        'control_border': '#686868',
        'option_bg': '#333333',
        'option_active_bg': '#474747',
        'option_border': '#666666',
        'scrollbar_bg': '#303030',
        'scrollbar_trough': '#111111',
    }


def update_side_section_styles(editor):
    """Refresh 3D side-panel section colors after expand/collapse or theme changes."""
    try:
        pal = editor._side_section_palette()
        for wname in ('_side_panel', '_side_scroll_canvas', '_side_panel_inner'):
            w = getattr(editor, wname, None)
            if w is not None:
                try:
                    w.configure(bg=pal['panel_bg'])
                except Exception:
                    pass
        sb = getattr(editor, '_side_scrollbar', None)
        if sb is not None:
            try:
                sb.configure(bg=pal['scrollbar_bg'], troughcolor=pal['scrollbar_trough'],
                             activebackground=pal['button_active_bg'])
            except Exception:
                pass
        row = getattr(editor, '_side_section_fold_row', None)
        if row is not None:
            try:
                row.configure(bg=pal['panel_bg'])
            except Exception:
                pass
        fhdr = getattr(editor, '_side_panel_fixed_header', None)
        if fhdr is not None:
            try:
                fhdr.configure(bg=pal['panel_bg'])
            except Exception:
                pass
        for b in getattr(editor, '_side_section_fold_buttons', []) or []:
            try:
                b.configure(bg=pal['button_bg'], fg=pal['button_fg'],
                            activebackground=pal['button_active_bg'], activeforeground=pal['button_fg'],
                            relief='flat', bd=0, highlightthickness=0)
            except Exception:
                pass
        for frame in getattr(editor, '_side_control_outline_frames', []) or []:
            try:
                frame.configure(bg=pal['control_border'])
            except Exception:
                pass

        states = getattr(editor, '_side_section_states', {}) or {}
        headers = getattr(editor, '_side_section_headers', {}) or {}
        bodies = getattr(editor, '_side_section_bodies', {}) or {}
        outers = getattr(editor, '_side_section_outers', {}) or {}
        header_wraps = getattr(editor, '_side_section_header_wraps', {}) or {}
        accents = getattr(editor, '_side_section_accents', {}) or {}
        for title, state in states.items():
            try:
                collapsed = bool(state.get())
            except Exception:
                collapsed = True
            header = headers.get(title)
            body = bodies.get(title)
            outer = outers.get(title)
            hwrap = header_wraps.get(title)
            accent = accents.get(title)
            hbg = pal['header_collapsed'] if collapsed else pal['header_expanded']
            hfg = pal.get('header_fg_collapsed', pal['header_fg']) if collapsed else pal.get('header_fg_expanded', pal['header_fg'])
            if outer is not None:
                try:
                    outer.configure(bg=pal['panel_bg'])
                except Exception:
                    pass
            if hwrap is not None:
                try:
                    hwrap.configure(bg=hbg)
                except Exception:
                    pass
            if accent is not None:
                try:
                    accent.configure(bg=pal.get('accent_active', '#ffffff') if not collapsed else pal.get('header_accent', '#666666'))
                except Exception:
                    pass
            if header is not None:
                try:
                    header.configure(
                        bg=hbg, fg=hfg,
                        activebackground=pal['header_hover'], activeforeground=pal.get('header_fg_expanded', pal['header_fg']),
                        highlightthickness=0,
                        relief='flat', bd=0)
                except Exception:
                    pass
            if body is not None:
                try:
                    body.configure(bg=pal['body_bg'], bd=0, relief='flat',
                                   highlightthickness=0)
                except Exception:
                    pass

        # Buttons and option toggles inside expanded sections used to sit
        # almost flush with the body background, especially in light mode.
        # Give every ordinary control a neutral 1 px outline and a slightly
        # stronger fill. Preset quick buttons keep their own active/hover
        # palette and are deliberately excluded here.
        preset_buttons = set((getattr(editor, '_preset_quick_buttons', {}) or {}).values())

        _is_light = editor._side_panel_light_enabled()
        _csel = '#ffffff' if _is_light else '#303030'
        _body_bg = pal['body_bg']
        _ctrl_fg = pal.get('control_fg', '#eeeeee')
        _sub_fg = pal.get('sub_label_fg', '#aaaaaa')
        _outline_frames = set(getattr(editor, '_side_control_outline_frames', []) or [])

        def _style_controls(widget):
            try:
                children = widget.winfo_children()
            except Exception:
                children = ()
            for child in children:
                try:
                    cls = str(child.winfo_class())
                except Exception:
                    cls = ''
                if cls == 'Button' and child not in preset_buttons:
                    try:
                        child.configure(
                            bg=pal['control_bg'], fg=pal['control_fg'],
                            activebackground=pal['control_active_bg'], activeforeground=pal['control_fg'],
                            disabledforeground=pal['control_disabled_fg'],
                            relief='flat', bd=0, highlightthickness=1,
                            highlightbackground=pal['control_border'],
                            highlightcolor=pal['control_border'])
                    except Exception:
                        pass
                elif cls == 'Checkbutton':
                    try:
                        cur_fg = str(child.cget('fg'))
                        is_warning = 'cc4444' in cur_fg or 'ff4444' in cur_fg
                        if is_warning:
                            _chk_fg = '#cc4444' if _is_light else '#ff4444'
                        else:
                            _chk_fg = _ctrl_fg
                        child.configure(
                            bg=_body_bg, fg=_chk_fg, selectcolor=_csel,
                            activebackground=_body_bg, activeforeground=_chk_fg)
                    except Exception:
                        pass
                elif cls == 'Label':
                    try:
                        cur_fg = str(child.cget('fg'))
                        cur_font = child.cget('font')
                        is_warning = 'ff6666' in cur_fg or 'ff4444' in cur_fg
                        if not is_warning:
                            child.configure(bg=_body_bg)
                            font_str = str(cur_font)
                            if 'bold' in font_str.lower():
                                child.configure(fg=_sub_fg)
                            else:
                                child.configure(fg=_ctrl_fg)
                    except Exception:
                        pass
                elif cls == 'Frame':
                    if child in _outline_frames:
                        try:
                            child.configure(bg=pal['control_border'])
                        except Exception:
                            pass
                    else:
                        try:
                            w = int(child.cget('width'))
                            if w != 2:
                                child.configure(bg=_body_bg)
                        except Exception:
                            try:
                                child.configure(bg=_body_bg)
                            except Exception:
                                pass
                elif cls == 'Menubutton':
                    try:
                        child.configure(
                            bg=pal['option_bg'], fg=pal['control_fg'],
                            activebackground=pal['option_active_bg'], activeforeground=pal['control_fg'],
                            relief='flat', bd=0, highlightthickness=1,
                            highlightbackground=pal['option_border'],
                            highlightcolor=pal['option_border'])
                    except Exception:
                        pass
                    try:
                        menu = child.nametowidget(child.cget('menu'))
                        menu.configure(
                            bg=pal['option_bg'], fg=pal['control_fg'],
                            activebackground=pal['option_active_bg'], activeforeground=pal['control_fg'])
                    except Exception:
                        pass
                _style_controls(child)

        for body in bodies.values():
            if body is not None:
                _style_controls(body)

        # The Node Focus color button deliberately uses the chosen geometry
        # color as its fill, so restore it after generic panel theming.
        try:
            editor._refresh_node_focus_color_button()
        except Exception:
            pass

        # Tk Scale value text does not inherit the recursive Label/Button
        # recoloring above.  Without an explicit refresh it keeps whichever
        # foreground was active when the 3D panel was first created, so
        # toggling light/dark panels makes the reference-distance value look
        # reversed (white on light, black on dark).
        _ref_dist_scale = getattr(editor, '_ref_dist_scale', None)
        if _ref_dist_scale is not None:
            try:
                _ref_dist_scale.configure(
                    bg=_body_bg, fg=_ctrl_fg,
                    activebackground=pal['control_active_bg'],
                    troughcolor=pal['option_bg'],
                    highlightthickness=0)
            except Exception:
                pass

        try:
            editor._update_preset_quick_buttons()
        except Exception:
            pass
    except Exception:
        pass


def build_side_panel(editor, panel):
    """Categorized 3D view controls. Top bar stays for fast toggles only."""
    if not hasattr(editor, "_side_section_states"):
        editor._side_section_states = {}
    if not hasattr(editor, "_side_section_bodies"):
        editor._side_section_bodies = {}
    if not hasattr(editor, "_side_section_headers"):
        editor._side_section_headers = {}
    if not hasattr(editor, "_side_section_outers"):
        editor._side_section_outers = {}
    if not hasattr(editor, "_side_section_header_wraps"):
        editor._side_section_header_wraps = {}
    if not hasattr(editor, "_side_section_accents"):
        editor._side_section_accents = {}
    editor._side_section_fold_buttons = []
    # Buttons that need a dependable custom-colored outline use a 1 px
    # wrapper frame. Tk's own Button highlight border is unreliable on
    # Windows (especially with flat buttons), so keep these wrappers and
    # recolor them whenever the panel theme changes.
    editor._side_control_outline_frames = []

    def _refresh_side_scrollregion_soon():
        try:
            editor.after_idle(lambda: editor._side_scroll_canvas.configure(
                scrollregion=editor._side_scroll_canvas.bbox("all")
            ))
        except Exception:
            pass

    def _set_all_sections(collapsed):
        try:
            for title, state in editor._side_section_states.items():
                body = editor._side_section_bodies.get(title)
                header = editor._side_section_headers.get(title)
                state.set(bool(collapsed))
                if body is None:
                    continue
                if collapsed:
                    body.pack_forget()
                else:
                    body.pack(side="top", fill="x", padx=6, pady=(0, 4))
                if header is not None:
                    header.configure(text=("+ " if collapsed else "- ") + title)
            try:
                editor._update_side_section_styles()
            except Exception:
                pass
            _refresh_side_scrollregion_soon()
        except Exception:
            pass

    def section(title, collapsed=True):
        pal = editor._side_section_palette()
        outer = tk.Frame(panel, bg=pal['panel_bg'])
        outer.pack(side="top", fill="x", padx=0, pady=(8, 0))

        state = tk.BooleanVar(value=bool(collapsed))
        editor._side_section_states[title] = state
        editor._side_section_outers[title] = outer

        def toggle():
            try:
                new_state = not state.get()
                state.set(new_state)
                if new_state:
                    body.pack_forget()
                else:
                    body.pack(side="top", fill="x", padx=6, pady=(0, 4))
                header.configure(text=("+ " if new_state else "- ") + title)
                editor._update_side_section_styles()
                _refresh_side_scrollregion_soon()
            except Exception:
                pass

        header_wrap = tk.Frame(outer, bg=pal['header_collapsed' if collapsed else 'header_expanded'])
        header_wrap.pack(side="top", fill="x", padx=6, pady=(2, 0))
        accent = tk.Frame(header_wrap, bg=pal.get('header_accent', '#666666'), width=2)
        accent.pack(side="left", fill="y")
        accent.pack_propagate(False)
        header = tk.Button(
            header_wrap, text=("+ " if collapsed else "- ") + title, command=toggle,
            bg=pal['header_collapsed' if collapsed else 'header_expanded'],
            fg=pal.get('header_fg_collapsed' if collapsed else 'header_fg_expanded', pal['header_fg']),
            activebackground=pal['header_hover'],
            activeforeground=pal.get('header_fg_expanded', pal['header_fg']),
            relief="flat", anchor="w",
            font=("Consolas", 9, "bold"), padx=6, pady=3, bd=0, highlightthickness=0
        )
        header.pack(side="left", fill="both", expand=True)

        body = tk.Frame(outer, bg=pal['body_bg'], bd=0, relief="flat", highlightthickness=0)
        editor._side_section_bodies[title] = body
        editor._side_section_headers[title] = header
        editor._side_section_header_wraps[title] = header_wrap
        editor._side_section_accents[title] = accent
        if not collapsed:
            body.pack(side="top", fill="x", padx=6, pady=(0, 4))
        editor._update_side_section_styles()
        return body

    # Tiny global controls pinned above the scroll area.
    pal = editor._side_section_palette()
    fold_row = tk.Frame(editor._side_panel_fixed_header, bg=pal['panel_bg'])
    editor._side_section_fold_row = fold_row
    fold_row.pack(side="top", fill="x", padx=6, pady=(4, 2))
    for _txt, _cmd in (("Expand all", lambda: _set_all_sections(False)),
                       ("Collapse all", lambda: _set_all_sections(True))):
        cell = tk.Frame(fold_row, bg=pal['control_border'], bd=0, highlightthickness=0)
        cell.pack(side="left", padx=1, fill="x", expand=True)
        editor._side_control_outline_frames.append(cell)
        b = tk.Button(cell, text=_txt, command=_cmd,
                      bg=pal['button_bg'], fg=pal['button_fg'], activebackground=pal['button_active_bg'],
                      activeforeground=pal['button_fg'], relief="flat", font=("Consolas", 8),
                      padx=3, pady=1, bd=0, highlightthickness=0)
        b.pack(side="top", fill="both", expand=True, padx=1, pady=1)
        editor._side_section_fold_buttons.append(b)

    def chk(parent, text, var, cmd=None, fg_override=None):
        _cbg = pal['body_bg']
        _cfg = fg_override or pal.get('control_fg', '#eeeeee')
        _csel = '#303030' if not editor._side_panel_light_enabled() else '#ffffff'
        tk.Checkbutton(parent, text=text, variable=var, command=cmd or editor.schedule_render,
                       bg=_cbg, fg=_cfg, selectcolor=_csel,
                       activebackground=_cbg, activeforeground=_cfg,
                       font=("Consolas", 9), bd=0, anchor="w").pack(side="top", fill="x", padx=4, pady=1)

    def opt(parent, var, values, cmd=None):
        om = tk.OptionMenu(parent, var, *values, command=cmd)
        _obg = pal.get('option_bg', '#303030')
        _ofg = pal.get('control_fg', '#eeeeee')
        _oabg = pal.get('option_active_bg', '#444444')
        _obrd = pal.get('option_border', '#666666')
        om.configure(bg=_obg, fg=_ofg, activebackground=_oabg,
                     activeforeground=_ofg, relief="flat", font=("Consolas", 8),
                     highlightthickness=1, highlightbackground=_obrd,
                     highlightcolor=_obrd)
        om["menu"].configure(bg=_obg, fg=_ofg, activebackground=_oabg,
                              activeforeground=_ofg)
        om.pack(side="top", fill="x", padx=4, pady=2)
        return om

    def outlined_button(parent, text, command, *, padx=3, pady=1):
        """Create a flat button with a reliable 1 px theme-colored outline."""
        pal = editor._side_section_palette()
        cell = tk.Frame(parent, bg=pal['control_border'], bd=0, highlightthickness=0)
        button = tk.Button(
            cell, text=text, command=command,
            bg=pal['control_bg'], fg=pal['control_fg'],
            activebackground=pal['control_active_bg'], activeforeground=pal['control_fg'],
            disabledforeground=pal['control_disabled_fg'],
            relief="flat", bd=0, highlightthickness=0,
            font=("Consolas", 8), padx=padx, pady=pady)
        button.pack(side="top", fill="both", expand=True, padx=1, pady=1)
        editor._side_control_outline_frames.append(cell)
        return cell, button

    f = section("Mode Preset", collapsed=True)
    opt(f, editor.view_preset,
        ("Normal", "Interior Edit", "⚠ Full View"),
        lambda *_: editor._apply_view_preset())
    quick = tk.Frame(f, bg="#181818")
    quick.pack(side="top", fill="x", padx=4, pady=(2, 2))
    editor._preset_quick_frame = quick

    editor._preset_quick_buttons = {}
    editor._preset_quick_frames = {}
    editor._preset_hover_mode = None
    editor._preset_desc_var = tk.StringVar(value=editor._preset_description(editor.view_preset.get()))

    for label, mode in (("Normal", "Normal"), ("Interior", "Interior Edit")):
        cell = tk.Frame(quick, bg="#181818", padx=1, pady=1)
        cell.pack(side="left", padx=1, fill="x", expand=True)
        b = tk.Button(cell, text=label, command=lambda m=mode: editor._set_view_preset(m),
                      bg="#282828", fg="#dddddd", activebackground="#3a3a3a",
                      activeforeground="#ffffff", relief="flat", font=("Consolas", 8),
                      padx=3, pady=1, bd=0, highlightthickness=0)
        b.pack(side="top", fill="x", expand=True)
        b.bind("<Enter>", lambda _e, m=mode: editor._show_preset_hover_desc(m))
        b.bind("<Leave>", lambda _e: editor._clear_preset_hover_desc())
        editor._preset_quick_buttons[mode] = b
        editor._preset_quick_frames[mode] = cell

    # User preset overrides live directly under the quick preset buttons.
    # Save captures only the controls owned by the built-in preset system;
    # transient map state (camera, selection, locked nodes/blocks) is never
    # persisted. Reset removes only the selected preset's override.
    preset_actions = tk.Frame(f, bg="#181818")
    preset_actions.pack(side="top", fill="x", padx=4, pady=(0, 2))
    editor._preset_action_frame = preset_actions
    save_cell, editor._preset_save_btn = outlined_button(
        preset_actions, "Save preset", editor._save_current_view_preset)
    save_cell.pack(side="left", padx=1, fill="x", expand=True)
    reset_cell, editor._preset_reset_btn = outlined_button(
        preset_actions, "Reset preset", editor._reset_current_view_preset)
    reset_cell.pack(side="left", padx=1, fill="x", expand=True)

    editor._preset_desc_label = tk.Label(
        f, textvariable=editor._preset_desc_var,
        bg="#181818", fg="#bbbbbb", font=("Consolas", 8),
        wraplength=205, justify="left", anchor="w")
    editor._preset_desc_label.pack(side="top", fill="x", padx=5, pady=(1, 2))
    editor._hint_labels.append((editor._preset_desc_label, {'side': 'top', 'fill': 'x', 'padx': 5, 'pady': (1, 2)}))
    editor._update_preset_quick_buttons()
    editor._refresh_preset_override_buttons()

    # A separate 3D working set. This does not replace the
    # editor's real selection; it only filters which selected nodes the 3D
    # view uses as its temporary visible/editable node context.
    f = section("Node Set", collapsed=False)
    tk.Label(f, textvariable=editor.node_set_status, bg="#181818", fg="#dddddd",
             font=("Consolas", 8), anchor="w", wraplength=205, justify="left").pack(side="top", fill="x", padx=5, pady=(2, 2))
    node_set_row = tk.Frame(f, bg="#181818")
    node_set_row.pack(side="top", fill="x", padx=4, pady=(1, 2))
    lock_cell, editor._node_set_lock_btn = outlined_button(
        node_set_row, "Lock selected nodes", editor._lock_selected_nodes_for_3d)
    lock_cell.pack(side="left", padx=1, fill="x", expand=True)
    unlock_cell, editor._node_set_unlock_btn = outlined_button(
        node_set_row, "Unlock", editor._unlock_3d_node_set)
    unlock_cell.pack(side="left", padx=1, fill="x", expand=True)

    # Optional destructive b14 assignment.  This is deliberately separated
    # from the safe lock/filter workflow; b17 is never modified here.
    assign_row = tk.Frame(f, bg="#181818")
    assign_row.pack(side="top", fill="x", padx=4, pady=(3, 1))
    editor._assign_zone_check = tk.Checkbutton(
        assign_row, text="Set b14 on lock", variable=editor.assign_b14_on_lock,
        bg="#181818", fg="#eeeeee", selectcolor="#303030",
        activebackground="#181818", activeforeground="#ffffff",
        font=("Consolas", 8), bd=0, anchor="w")
    editor._assign_zone_check.pack(side="left")
    tk.Label(assign_row, text="to", bg="#181818", fg="#aaaaaa",
             font=("Consolas", 8)).pack(side="left", padx=(4, 2))
    tk.Entry(assign_row, textvariable=editor.assign_b14_value, width=4,
             bg="#101010", fg="#eeeeee", insertbackground="#ffffff",
             relief="flat", font=("Consolas", 8)).pack(side="left")

    zone_head = tk.Frame(f, bg="#181818")
    editor._zone_filter_head_frame = zone_head
    zone_head.pack(side="top", fill="x", padx=4, pady=(4, 1))
    editor._zone_filter_toggle_btn = tk.Button(
        zone_head, text="", command=editor._toggle_zone_filter_details,
        bg="#181818", fg="#dddddd",
        activebackground="#282828", activeforeground="#ffffff",
        relief="flat", bd=0, highlightthickness=0,
        font=("Consolas", 8), anchor="w", padx=0, pady=0)
    editor._zone_filter_toggle_btn.pack(side="left", fill="x", expand=True)
    refresh_cell, _refresh_btn = outlined_button(
        zone_head, "↻", editor._refresh_zone_filter_controls, padx=3, pady=0)
    refresh_cell.pack(side="right", padx=1)

    # The whole control chain below the summary folds as one unit. It is
    # built and refreshed normally even while hidden so filter state never
    # depends on whether the disclosure is open.
    editor._zone_filter_details_frame = tk.Frame(f, bg="#181818")
    editor._zone_filter_details_frame.pack(side="top", fill="x", padx=0, pady=0)
    zone_buttons = tk.Frame(editor._zone_filter_details_frame, bg="#181818")
    zone_buttons.pack(side="top", fill="x", padx=4, pady=(0, 1))
    show_cell, _show_btn = outlined_button(
        zone_buttons, "Show all", lambda: editor._set_all_zone_filters(True))
    show_cell.pack(side="left", padx=1, fill="x", expand=True)
    hide_cell, _hide_btn = outlined_button(
        zone_buttons, "Hide all", lambda: editor._set_all_zone_filters(False))
    hide_cell.pack(side="left", padx=1, fill="x", expand=True)
    restore_cell, _restore_btn = outlined_button(
        zone_buttons, "Restore", editor._restore_locked_zone_scope)
    restore_cell.pack(side="left", padx=1, fill="x", expand=True)
    editor._zone_filter_frame = tk.Frame(editor._zone_filter_details_frame, bg="#181818")
    editor._zone_filter_frame.pack(side="top", fill="x", padx=4, pady=(0, 2))

    editor._node_set_help_label = tk.Label(
        f,
        text="Lock a group of nodes that can be editable directly in 3D View. Use b14 lock to assign selected nodes to a b14 zone. Useful for interiors.",
        bg="#181818", fg="#c8c8c8", font=("Consolas", 8),
        wraplength=205, justify="left")
    editor._node_set_help_label.pack(side="top", fill="x", padx=5, pady=(0, 3))
    editor._hint_labels.append((editor._node_set_help_label, {'side': 'top', 'fill': 'x', 'padx': 5, 'pady': (0, 3)}))
    editor._sync_node_set_zone_labels(refresh_controls=False)
    editor._refresh_node_set_status()
    editor._refresh_zone_filter_controls()
    editor._sync_zone_filter_disclosure()

    f = section("Block Filter", collapsed=False)
    _bf_bg = pal['body_bg']
    tk.Label(f, text="CModel color", bg=_bf_bg, fg=pal.get('sub_label_fg', '#aaaaaa'),
             font=("Consolas", 9), anchor="w").pack(side="top", fill="x", padx=5)
    opt(f, editor.cmodel_color_mode, ("Source", "Addr Block"), lambda *_: editor.schedule_render())
    tk.Label(f, text="CModel detail", bg=_bf_bg, fg=pal.get('sub_label_fg', '#aaaaaa'),
             font=("Consolas", 9), anchor="w").pack(side="top", fill="x", padx=5)
    opt(f, editor.cmodel_detail_mode, ("Raw Wire", "Structure", "Structure + Ghost"), lambda *_: editor.schedule_render())

    editor._building_block_visible = {}
    editor._decor_block_visible = {}
    editor._block_layer_frames = {}

    editor._bld_block_frame = tk.Frame(f, bg=_bf_bg)
    editor._bld_block_frame.pack(side="top", fill="x", padx=0, pady=(4, 0))
    editor._decor_block_frame = tk.Frame(f, bg=_bf_bg)
    editor._decor_block_frame.pack(side="top", fill="x", padx=0, pady=(0, 0))


    f = section("Scene Layers", collapsed=False)
    _sl_fg = pal.get('sub_label_fg', '#666666')
    _sl_bg = pal['body_bg']
    tk.Label(f, text="OVERLAYS", bg=_sl_bg, fg=_sl_fg,
             font=("Consolas", 9, "bold"), anchor="w").pack(side="top", fill="x", padx=4, pady=(2, 2))
    chk(f, "Grid", editor.show_grid)
    chk(f, "Nodes", editor.show_node_overlay)
    chk(f, "Radius", editor.show_radii)

    # Keep Graph Ends and its two category filters inside one permanent
    # layout group.  Earlier builds put the filters in a separately-managed
    # frame; when the side panel was rebuilt/restored, that child could stay
    # unmanaged even while the master checkbox was visibly enabled.
    editor._graph_ends_group = tk.Frame(f, bg=_sl_bg)
    editor._graph_ends_group.pack(side="top", fill="x")
    chk(editor._graph_ends_group, "Graph ends", editor.show_graph_ends,
        editor._on_graph_ends_changed)

    editor._graph_end_clear_check = tk.Checkbutton(
        editor._graph_ends_group, text="  Pink: clear gaps",
        variable=editor.show_graph_end_clear,
        command=editor._on_graph_end_filter_changed,
        bg=_sl_bg, fg='#ff46d2',
        selectcolor=('#303030' if not editor._side_panel_light_enabled() else '#ffffff'),
        activebackground=_sl_bg, activeforeground='#ff46d2',
        font=("Consolas", 9), bd=0, anchor="w")
    editor._graph_end_stair_check = tk.Checkbutton(
        editor._graph_ends_group, text="  Orange: stair-like",
        variable=editor.show_graph_end_stair,
        command=editor._on_graph_end_filter_changed,
        bg=_sl_bg, fg='#ff9137',
        selectcolor=('#303030' if not editor._side_panel_light_enabled() else '#ffffff'),
        activebackground=_sl_bg, activeforeground='#ff9137',
        font=("Consolas", 9), bd=0, anchor="w")

    # The group itself never moves.  Only these two child rows are packed or
    # forgotten, so restoring/rebuilding the side panel cannot lose their
    # insertion point under the master Graph Ends row.
    if not hasattr(editor, '_graph_ends_visibility_trace'):
        try:
            editor._graph_ends_visibility_trace = editor.show_graph_ends.trace_add(
                'write', lambda *_args: editor.after_idle(
                    editor._sync_graph_end_filter_controls))
        except Exception:
            editor._graph_ends_visibility_trace = None
    editor._sync_graph_end_filter_controls()

    tk.Label(f, text="ENTITIES", bg=_sl_bg, fg=_sl_fg,
             font=("Consolas", 9, "bold"), anchor="w").pack(side="top", fill="x", padx=4, pady=(8, 2))
    chk(f, "Buildings", editor.show_buildings)
    chk(f, "Vehicles", editor.show_vehicles)
    chk(f, "Decorations", editor.show_decorations)
    _foliage_red = '#cc4444' if editor._side_panel_light_enabled() else '#ff4444'
    chk(f, '⚠ Foliage', editor.show_foliage, lambda: editor.schedule_render(), fg_override=_foliage_red)
    tk.Label(f, text="RENDER", bg=_sl_bg, fg=_sl_fg,
             font=("Consolas", 9, "bold"), anchor="w").pack(side="top", fill="x", padx=4, pady=(8, 2))
    # Visual mesh temporarily disabled — two render bugs:
    # 1. _parse_render_mesh_3d aborts on unknown face types, losing geometry
    # 2. Fit transform mispositions tunnel sub-pieces (degenerate compact bbox)
    # Created but hidden; sync_developer_controls shows it in dev mode.
    _cbg = pal['body_bg']
    _cfg_vm = pal.get('control_fg', '#eeeeee')
    _csel_vm = '#303030' if not editor._side_panel_light_enabled() else '#ffffff'
    editor._cmodel_overlay_chk = tk.Checkbutton(
        f, text="Collision / CModel3D", variable=editor.show_cmodel_overlay,
        command=editor.schedule_render, bg=_cbg, fg=_cfg_vm, selectcolor=_csel_vm,
        activebackground=_cbg, activeforeground=_cfg_vm,
        font=("Consolas", 9), bd=0, anchor="w")
    editor._cmodel_overlay_chk.pack(side="top", fill="x", padx=4, pady=1)
    editor._visual_mesh_broken_chk = tk.Checkbutton(
        f, text="Visual mesh (broken)", variable=editor.show_visual_mesh,
        command=editor.schedule_render, bg=_cbg, fg=_cfg_vm, selectcolor=_csel_vm,
        activebackground=_cbg, activeforeground=_cfg_vm,
        font=("Consolas", 9), bd=0, anchor="w")
    try:
        if bool(getattr(getattr(editor, 'app', None), 'dev_mode', tk.BooleanVar(value=False)).get()):
            editor._visual_mesh_broken_chk.pack(side="top", fill="x", padx=4, pady=1,
                                                before=editor._cmodel_overlay_chk)
    except Exception:
        pass

    # Node Focus belongs to the 3D renderer, so its controls live here with
    # the other 3D render layers rather than in the application's View menu.
    chk(f, "Node focus", editor.node_focus_mode, editor._on_node_focus_toggled)
    _nf_row = tk.Frame(f, bg=_sl_bg)
    _nf_row.pack(side="top", fill="x", padx=4, pady=(1, 2))
    tk.Label(_nf_row, text="Geometry color", bg=_sl_bg,
             fg=pal.get('control_fg', '#eeeeee'),
             font=("Consolas", 8), anchor="w").pack(side="left")
    editor._node_focus_color_button = tk.Button(
        _nf_row, text=editor.node_focus_geometry_color.get(),
        command=editor._open_node_focus_color_dialog,
        relief="flat", bd=0, highlightthickness=1,
        font=("Consolas", 8), padx=5, pady=1)
    editor._node_focus_color_button.pack(side="right", padx=(4, 0))
    editor._refresh_node_focus_color_button()

    f = section("Labels")
    chk(f, "Node ID", editor.show_labels)
    chk(f, "Floor coordinates", editor.show_floor_coordinates)
    tk.Label(f, text="Label size", bg="#181818", fg="#aaaaaa",
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5)
    opt(f, editor.label_size, ("Small", "Normal", "Large"), lambda *_: editor.schedule_render())
    tk.Label(f, text="Model line thickness", bg="#181818", fg="#aaaaaa",
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5, pady=(3, 0))
    opt(f, editor.model_line_width, ("0.50x", "1.00x"), lambda *_: editor.schedule_render())
    tk.Label(f, text="Node edge thickness", bg="#181818", fg="#aaaaaa",
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5, pady=(3, 0))
    opt(f, editor.node_edge_width, ("1", "2", "3"), lambda *_: editor.schedule_render())

    f = section("Reference Model")
    chk(f, "Model next to node", editor.show_ref_model)
    tk.Label(f, text="Angle around node (°)", bg=pal['body_bg'], fg=pal.get('label_fg', '#aaaaaa'),
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5)
    _ref_angle_frame = tk.Frame(f, bg=pal['body_bg'])
    _ref_angle_frame.pack(side="top", fill="x", padx=5)
    _ref_angle_scale = tk.Scale(
        _ref_angle_frame, variable=editor.ref_model_angle,
        from_=0.0, to=359.0, resolution=1.0, orient="horizontal",
        bg=pal['body_bg'], fg=pal.get('control_fg', '#eeeeee'),
        activebackground=pal.get('control_active_bg', '#444444'),
        highlightthickness=0, troughcolor=pal.get('option_bg', '#303030'),
        bd=0, showvalue=True, font=("Consolas", 8),
        command=lambda *_: editor.schedule_render())
    editor._ref_angle_scale = _ref_angle_scale
    _ref_angle_scale.pack(fill="x")
    tk.Label(f, text="Distance (m)", bg=pal['body_bg'], fg=pal.get('label_fg', '#aaaaaa'),
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5)
    _ref_dist_frame = tk.Frame(f, bg=pal['body_bg'])
    _ref_dist_frame.pack(side="top", fill="x", padx=5)
    _ref_dist_scale = tk.Scale(
        _ref_dist_frame, variable=editor.ref_model_distance,
        from_=0.3, to=3.0, resolution=0.1, orient="horizontal",
        bg=pal['body_bg'], fg=pal.get('control_fg', '#eeeeee'),
        activebackground=pal.get('control_active_bg', '#444444'),
        highlightthickness=0, troughcolor=pal.get('option_bg', '#303030'),
        bd=0, showvalue=True, font=("Consolas", 8),
        command=lambda *_: editor.schedule_render())
    editor._ref_dist_scale = _ref_dist_scale
    _ref_dist_scale.pack(fill="x")

    f = section("Context")
    row = tk.Frame(f, bg="#181818"); row.pack(side="top", fill="x", padx=4, pady=2)
    for text, cmd in (("Focus", editor._focus_selected_building), ("Local", editor._context_local), ("Near-", editor._context_smaller),
                      ("Near+", editor._context_larger), ("All", editor._context_all)):
        tk.Button(row, text=text, command=cmd, bg="#282828", fg="#dddddd",
                  activebackground="#3a3a3a", activeforeground="#ffffff",
                  relief="flat", font=("Consolas", 8), padx=3, pady=1).pack(side="left", padx=1)

    tk.Label(f, text="Buildings", bg="#181818", fg="#aaaaaa",
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5)
    opt(f, editor.building_scope, ("Selected", "Tight", "Local", "Full"), lambda *_: editor._on_context_scope_changed(refit=True))
    tk.Label(f, text="Decorations", bg="#181818", fg="#aaaaaa",
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5)
    opt(f, editor.decoration_scope, ("Off", "Tight", "Local", "Full"), lambda *_: editor._on_context_scope_changed())
    # Visual mesh scope hidden — visual mesh is temporarily disabled.
    editor.visual_scope.set("Off")
    tk.Label(f, text="Collision / CModel3D", bg="#181818", fg="#aaaaaa",
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5)
    opt(f, editor.collision_scope, ("Off", "Selected", "Tight", "Local", "Full"), lambda *_: editor._on_context_scope_changed())

    _h = tk.Label(f, text="Interior preset: Buildings=Tight, Collision=Selected.",
             bg="#181818", fg="#888888", font=("Consolas", 8),
             wraplength=205, justify="left")
    _h.pack(side="top", fill="x", padx=5, pady=2)
    editor._hint_labels.append((_h, {'side': 'top', 'fill': 'x', 'padx': 5, 'pady': 2}))

    f = section("Performance")
    _perf_dev_frame = tk.Frame(f, bg="#181818")
    _c1 = chk(_perf_dev_frame, "Drag preview", editor.perf_drag_preview)
    _c2 = chk(_perf_dev_frame, "Motion LOD", editor.perf_motion_lod)
    _c3 = chk(_perf_dev_frame, "Offscreen cull", editor.perf_offscreen_cull)
    editor._perf_dev_frame = _perf_dev_frame
    _is_dev = False
    try:
        _is_dev = bool(getattr(getattr(editor, 'app', None), 'dev_mode', tk.BooleanVar(value=False)).get())
    except Exception:
        pass
    if _is_dev:
        _perf_dev_frame.pack(side="top", fill="x")
    chk(f, "Keep decorations while moving", editor.perf_keep_decorations_motion,
        editor._on_keep_decorations_motion_changed)
    chk(f, "Keep CModel blocks while moving", editor.perf_keep_cmodel_blocks_motion,
        editor._on_keep_cmodel_blocks_motion_changed)
    tk.Label(f, text="Max draw lines", bg="#181818", fg="#aaaaaa",
             font=("Consolas", 8), anchor="w").pack(side="top", fill="x", padx=5)
    opt(f, editor.perf_line_budget, ("5000", "10000", "15000", "25000", "Unlimited"),
        lambda *_: editor.schedule_render())
    _h = tk.Label(f, text="Hard cap for 3D scene lines. Lower values now stop later model/CModel geometry from being processed once the cap is reached.",
             bg="#181818", fg="#888888", font=("Consolas", 8),
             wraplength=205, justify="left")
    _h.pack(side="top", fill="x", padx=5, pady=3)
    editor._hint_labels.append((_h, {'side': 'top', 'fill': 'x', 'padx': 5, 'pady': 3}))
    editor._update_side_section_styles()
    if not editor._hints_visible:
        for lbl, _ in editor._hint_labels:
            try:
                lbl.pack_forget()
            except Exception:
                pass


def on_keep_decorations_motion_changed(editor):
    """Persist the 3D decoration-during-motion preference and redraw."""
    try:
        cfg = _load_editor_cfg()
        cfg['3d_keep_decorations_during_motion'] = bool(editor.perf_keep_decorations_motion.get())
        _save_editor_cfg(cfg)
    except Exception:
        pass
    editor.schedule_render()


def keep_decorations_during_motion_active(editor):
    """True only for active mouse pan/orbit preview frames when opted in."""
    try:
        var = getattr(editor, 'perf_keep_decorations_motion', None)
        return (bool(getattr(editor, '_render_preview', False)) and
                bool(var.get()) if var is not None else False)
    except Exception:
        return False


def on_keep_cmodel_blocks_motion_changed(editor):
    """Persist the 3D CModel-during-motion preference and redraw."""
    try:
        cfg = _load_editor_cfg()
        cfg['3d_keep_cmodel_blocks_during_motion'] = bool(editor.perf_keep_cmodel_blocks_motion.get())
        _save_editor_cfg(cfg)
    except Exception:
        pass
    editor.schedule_render()


def keep_cmodel_blocks_during_motion_active(editor):
    """True only for active mouse pan/orbit preview frames when opted in."""
    try:
        var = getattr(editor, 'perf_keep_cmodel_blocks_motion', None)
        return (bool(getattr(editor, '_render_preview', False)) and
                bool(var.get()) if var is not None else False)
    except Exception:
        return False


def toggle_hints(editor):
    editor._hints_visible = not editor._hints_visible
    for lbl, pack_opts in editor._hint_labels:
        try:
            if editor._hints_visible:
                lbl.pack(**pack_opts)
            else:
                lbl.pack_forget()
        except Exception:
            pass
    try:
        cfg = _load_editor_cfg()
        cfg['3d_hints_visible'] = editor._hints_visible
        _save_editor_cfg(cfg)
    except Exception:
        pass


def toggle_side_panel(editor):
    try:
        if editor._side_panel is not None and editor._side_panel.winfo_ismapped():
            editor._side_panel.pack_forget()
            editor._side_panel_visible = False
            if editor._panel_button is not None:
                editor._panel_button.configure(text="Show panel")
        else:
            editor._side_panel.pack(side="right", fill="y")
            editor._side_panel_visible = True
            if editor._panel_button is not None:
                editor._panel_button.configure(text="Hide panel")
    except Exception:
        pass
    editor.schedule_render()


def preset_description(editor, mode):
    """Short contextual help for the active/hovered 3D view preset."""
    m = str(mode or "Normal")
    if m == "Normal":
        return "Normal preset — default option. Useful to see surroundings."
    if m in ("Interior Edit", "Interior Focus"):
        return "Interior preset — useful when working with a building that contains rooms, floors, and stairs."
    if m in ("X-Ray", "Navigation X-Ray", "⚠ Full View"):
        return "⚠ Full View — shows the exact collision layer of all nearby buildings. Useful for precision. May cause lag on dense maps."
    return "Preset help unavailable for this mode."


def show_preset_hover_desc(editor, mode):
    try:
        editor._preset_hover_mode = str(mode)
    except Exception:
        editor._preset_hover_mode = None
    try:
        editor._preset_desc_var.set(editor._preset_description(mode))
    except Exception:
        pass
    try:
        editor._update_preset_quick_buttons()
    except Exception:
        pass


def clear_preset_hover_desc(editor):
    editor._preset_hover_mode = None
    editor._restore_active_preset_desc()
    try:
        editor._update_preset_quick_buttons()
    except Exception:
        pass


def restore_active_preset_desc(editor):
    try:
        editor._preset_desc_var.set(editor._preset_description(editor.view_preset.get()))
    except Exception:
        pass


def preset_button_palette(editor):
    """Palette for 3D preset quick buttons, including light-panel mode."""
    try:
        app = getattr(editor, 'app', None)
        light_var = getattr(app, 'light_panels', None)
        light = bool(light_var.get()) if light_var is not None else False
    except Exception:
        light = False

    if light:
        return {
            'frame': '#f3f3f3',
            'inactive_bg': '#d2d2d2',
            'inactive_fg': '#111111',
            'inactive_active_bg': '#bebebe',
            'active_bg': '#1f7a2e',
            'active_fg': '#ffffff',
            'active_active_bg': '#2e9340',
            'hover_border': '#5f5f5f',
            'hover_active_border': '#a8e6b0',
            'idle_border': '#969696',
            'desc_bg': '#f3f3f3',
            'desc_fg': '#303030',
        }

    return {
        'frame': '#181818',
        'inactive_bg': '#353535',
        'inactive_fg': '#eeeeee',
        'inactive_active_bg': '#494949',
        'active_bg': '#0b5a24',
        'active_fg': '#ffffff',
        'active_active_bg': '#0f7430',
        'hover_border': '#b8b8b8',
        'hover_active_border': '#c8f0d0',
        'idle_border': '#686868',
        'desc_bg': '#181818',
        'desc_fg': '#bbbbbb',
    }


def update_preset_quick_buttons(editor):
    """Highlight the active quick preset and outline the hovered one.

    Uses a 1px wrapper frame as the outline instead of Button highlight,
    because Tk button highlight is unreliable on Windows and can disappear
    or change widget geometry.
    """
    buttons = getattr(editor, '_preset_quick_buttons', {}) or {}
    frames = getattr(editor, '_preset_quick_frames', {}) or {}
    pal = editor._preset_button_palette()
    try:
        active = str(editor.view_preset.get())
    except Exception:
        active = "Normal"
    hover = getattr(editor, '_preset_hover_mode', None)
    hover = str(hover) if hover is not None else None

    try:
        getattr(editor, '_preset_quick_frame', None).configure(bg=pal['frame'])
    except Exception:
        pass
    try:
        getattr(editor, '_preset_action_frame', None).configure(bg=pal['frame'])
    except Exception:
        pass
    try:
        getattr(editor, '_preset_desc_label', None).configure(
            bg=pal['desc_bg'], fg=pal['desc_fg'])
    except Exception:
        pass

    for mode, btn in buttons.items():
        try:
            smode = str(mode)
            is_active = (smode == active)
            is_hover = (smode == hover)
            cell = frames.get(mode)

            if is_active:
                bg = pal['active_bg']
                fg = pal['active_fg']
                active_bg = pal['active_active_bg']
                border = pal['hover_active_border'] if is_hover else pal['active_bg']
            else:
                bg = pal['inactive_bg']
                fg = pal['inactive_fg']
                active_bg = pal['inactive_active_bg']
                border = pal['hover_border'] if is_hover else pal['idle_border']

            if cell is not None:
                cell.configure(bg=border)
            btn.configure(
                bg=bg, fg=fg,
                activebackground=active_bg, activeforeground=fg,
                relief="flat",
                bd=0,
                highlightthickness=0)
        except Exception:
            pass

    if hover is None:
        editor._restore_active_preset_desc()
    editor._refresh_preset_override_buttons()


def capture_view_preset_settings(editor):
    data = {}
    for name in editor._view_preset_setting_names():
        var = getattr(editor, name, None)
        if var is None or not hasattr(var, 'get'):
            continue
        try:
            value = var.get()
            # Force Tk scalar wrappers to plain JSON-friendly values.
            if isinstance(value, (str, bool, int, float)):
                data[name] = value
            else:
                data[name] = str(value)
        except Exception:
            pass
    return data


def get_view_preset_override(editor, mode=None):
    preset = editor._canonical_view_preset_name(
        editor.view_preset.get() if mode is None else mode)
    try:
        overrides = _load_editor_cfg().get('3d_preset_overrides', {})
        if not isinstance(overrides, dict):
            return None
        data = overrides.get(preset)
        return dict(data) if isinstance(data, dict) else None
    except Exception:
        return None


def apply_view_preset_override(editor, mode=None):
    data = editor._get_view_preset_override(mode)
    if not data:
        return False
    applied = False
    for name in editor._view_preset_setting_names():
        if name not in data:
            continue
        var = getattr(editor, name, None)
        if var is None or not hasattr(var, 'set'):
            continue
        try:
            var.set(data[name])
            applied = True
        except Exception:
            pass
    if applied:
        # Block visibility is transient/map-specific and intentionally not
        # part of a saved preset. Match factory preset application by
        # returning to an unfiltered CModel block view.
        try:
            editor._reset_block_visibility()
        except Exception:
            pass
    return applied


def refresh_preset_override_buttons(editor):
    btn = getattr(editor, '_preset_reset_btn', None)
    if btn is None:
        return
    try:
        btn.configure(state=("normal" if editor._get_view_preset_override() else "disabled"))
    except Exception:
        pass


def save_current_view_preset(editor):
    preset = editor._canonical_view_preset_name(editor.view_preset.get())
    if preset not in ("Normal", "Interior Edit", "X-Ray"):
        return
    try:
        cfg = _load_editor_cfg()
        overrides = cfg.get('3d_preset_overrides', {})
        if not isinstance(overrides, dict):
            overrides = {}
        overrides[preset] = editor._capture_view_preset_settings()
        cfg['3d_preset_overrides'] = overrides
        _save_editor_cfg(cfg)
        editor._refresh_preset_override_buttons()
        if hasattr(editor, 'info_var'):
            editor.info_var.set(f"Saved 3D preset: {preset}")
    except Exception:
        pass


def reset_current_view_preset(editor):
    preset = editor._canonical_view_preset_name(editor.view_preset.get())
    try:
        cfg = _load_editor_cfg()
        overrides = cfg.get('3d_preset_overrides', {})
        if isinstance(overrides, dict) and preset in overrides:
            overrides.pop(preset, None)
            if overrides:
                cfg['3d_preset_overrides'] = overrides
            else:
                cfg.pop('3d_preset_overrides', None)
            _save_editor_cfg(cfg)
    except Exception:
        pass
    # Reset means factory values now, not merely on the next preset switch.
    editor._apply_view_preset(use_override=False)
    editor._refresh_preset_override_buttons()
    try:
        if hasattr(editor, 'info_var'):
            editor.info_var.set(f"Reset 3D preset: {preset}")
    except Exception:
        pass


def set_view_preset(editor, mode):
    # If the quick preset button is clicked while that preset is already active,
    # do nothing.  This prevents Normal -> Normal from unexpectedly rebuilding,
    # refitting, and changing the currently useful 3D context view.
    try:
        if str(editor.view_preset.get()) == str(mode):
            editor._update_preset_quick_buttons()
            return
        editor.view_preset.set(mode)
    except Exception:
        pass
    editor._apply_view_preset()


def apply_view_preset(editor, use_override=True):
    """Apply the 3D workflow preset or its saved user override.

    Factory definitions remain hardcoded below and are never rewritten.
    A saved override simply replaces their owned control values at apply
    time, so Reset can always return to a known-good built-in preset.
    """
    p = editor.view_preset.get()
    if use_override and editor._apply_view_preset_override(p):
        editor.rebuild_scene()
        editor.schedule_render()
        try:
            editor._update_preset_quick_buttons()
        except Exception:
            pass
        return
    # Visual mesh is temporarily disabled across all presets due to two
    # render bugs in _parse_render_mesh_3d / entity_segments3d:
    #   1. Face parser aborts on unknown face types, losing building geometry
    #   2. Per-entity fit transform mispositions tunnel sub-pieces
    # Re-enable show_visual_mesh and the Visual/Full Scene presets once fixed.
    if p in ("Normal", "Local Building"):
        editor.show_grid.set(True); editor.ground_mode.set("Solid + Grid")
        editor.show_node_overlay.set(True)
        editor.show_radii.set(False); editor.show_graph_ends.set(False); editor.show_building_levels.set(True)
        editor.show_buildings.set(True); editor.show_vehicles.set(False); editor.show_decorations.set(True)
        editor.show_visual_mesh.set(False); editor.show_cmodel_overlay.set(True)
        editor.building_scope.set("Local"); editor.decoration_scope.set("Local"); editor.visual_scope.set("Off"); editor.collision_scope.set("Selected")
        editor.cmodel_color_mode.set("Addr Block"); editor.cmodel_detail_mode.set("Structure + Ghost"); editor._reset_block_visibility()
        editor.tunnel_draw_mode.set("CModel only"); editor.tunnel_visual_transform.set("Fit XZY")
        editor.context_radius.set(64.0); editor.max_buildings.set(32); editor.max_vehicles.set(8); editor.max_decor.set(64)
        editor.perf_drag_preview.set(True); editor.perf_offscreen_cull.set(True); editor.perf_line_budget.set("15000")
    elif p in ("Interior Edit", "Interior Focus"):
        editor.show_grid.set(True); editor.ground_mode.set("Cutaway")
        editor.show_node_overlay.set(True)
        editor.show_radii.set(False); editor.show_graph_ends.set(False); editor.show_building_levels.set(True)
        editor.show_buildings.set(True); editor.show_vehicles.set(False); editor.show_decorations.set(True)
        editor.show_visual_mesh.set(False); editor.show_cmodel_overlay.set(True)
        editor.building_scope.set("Tight"); editor.decoration_scope.set("Local"); editor.visual_scope.set("Off"); editor.collision_scope.set("Selected")
        editor.cmodel_color_mode.set("Addr Block"); editor.cmodel_detail_mode.set("Structure"); editor._reset_block_visibility()
        editor.tunnel_draw_mode.set("CModel only"); editor.tunnel_visual_transform.set("Fit XZY")
        editor.context_radius.set(36.0); editor.max_buildings.set(24); editor.max_vehicles.set(0); editor.max_decor.set(32)
        editor.perf_drag_preview.set(True); editor.perf_offscreen_cull.set(True); editor.perf_line_budget.set("15000")
    elif p in ("X-Ray", "Navigation X-Ray", "⚠ Full View"):
        editor.show_grid.set(True); editor.ground_mode.set("Cutaway")
        editor.show_node_overlay.set(True)
        editor.show_radii.set(False); editor.show_graph_ends.set(False); editor.show_building_levels.set(True)
        editor.show_buildings.set(True); editor.show_vehicles.set(False); editor.show_decorations.set(False)
        editor.show_visual_mesh.set(False); editor.show_cmodel_overlay.set(True)
        editor.building_scope.set("Local"); editor.decoration_scope.set("Off"); editor.visual_scope.set("Off"); editor.collision_scope.set("Local")
        editor.cmodel_color_mode.set("Addr Block"); editor.cmodel_detail_mode.set("Structure + Ghost"); editor._reset_block_visibility()
        editor.tunnel_draw_mode.set("CModel only"); editor.tunnel_visual_transform.set("Fit XZY")
        editor.context_radius.set(80.0); editor.max_buildings.set(32); editor.max_vehicles.set(8); editor.max_decor.set(64)
        editor.perf_drag_preview.set(True); editor.perf_offscreen_cull.set(True); editor.perf_line_budget.set("25000")
    elif p == "Debug":
        editor.show_grid.set(True); editor.ground_mode.set("Grid")
        editor.show_node_overlay.set(True); editor.show_radii.set(True); editor.show_graph_ends.set(False)
        editor.show_building_levels.set(True)
        editor.show_buildings.set(True); editor.show_vehicles.set(True); editor.show_decorations.set(True)
        editor.show_visual_mesh.set(False); editor.show_cmodel_overlay.set(True)
        editor.building_scope.set("Full"); editor.decoration_scope.set("Full"); editor.visual_scope.set("Off"); editor.collision_scope.set("Full")
        editor.cmodel_color_mode.set("Addr Block"); editor.cmodel_detail_mode.set("Raw Wire"); editor._reset_block_visibility()
        editor.tunnel_draw_mode.set("Compare")
        editor.perf_drag_preview.set(False); editor.perf_offscreen_cull.set(False); editor.perf_line_budget.set("Unlimited")
    editor.rebuild_scene()
    editor.schedule_render()
    try:
        editor._update_preset_quick_buttons()
    except Exception:
        pass

