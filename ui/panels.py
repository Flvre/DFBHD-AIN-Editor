"""Zone-panel construction helpers for the AIN editor.

The editor is supplied explicitly so this module does not import the
application class or own editor state.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from config.ui_theme import (C_ACCENT, C_BORDER, C_DIM, C_LABEL, C_PANEL,
    C_PANEL2, C_TEXT, UI_DARK_TO_LIGHT_COLOR_MAP)
from rendering.grid_spacing import MED_GRID_SPACING_CHOICES
import runtime_namespace as _ns

def build_zone_panel(editor, parent):
    """Docked right sidebar for Area Zone configuration and ownership."""
    panel = tk.Frame(parent, bg=C_PANEL, width=245, bd=0)
    panel.grid(row=0, column=2, sticky='ns')
    panel.grid_propagate(False)
    panel.pack_propagate(False)
    editor._zone_panel = panel

    header = tk.Frame(panel, bg=C_PANEL2)
    header.pack(fill='x')
    tk.Label(header, text="Area Zones", bg=C_PANEL2, fg=C_TEXT,
             font=('Consolas',11,'bold'), anchor='w').pack(side='left', padx=8, pady=6)
    tk.Button(header, text="Hide", command=editor._hide_zone_panel,
              bg=C_PANEL, fg=C_DIM, font=('Consolas',9),
              relief='flat', padx=6).pack(side='right', padx=6, pady=4)

    body = tk.Frame(panel, bg=C_PANEL)
    body.pack(fill='both', expand=True, padx=8, pady=8)

    editor._zone_name_var = tk.StringVar(value="Zone 1")
    editor._zone_color_var = tk.StringVar(value="#00ffaa")
    editor._zone_b12_var = tk.StringVar(value="0")
    editor._zone_b15_var = tk.StringVar(value="36")
    editor._zone_ignore_var = tk.BooleanVar(value=False)

    def row_label(txt):
        tk.Label(body, text=txt, bg=C_PANEL, fg=C_TEXT,
                 font=('Consolas',9,'bold'), anchor='w').pack(fill='x', pady=(6,1))

    row_label("Name")
    e_name = tk.Entry(body, textvariable=editor._zone_name_var,
                      bg=C_PANEL2, fg=C_TEXT, insertbackground=C_TEXT,
                      font=('Consolas',10), relief='flat')
    e_name.pack(fill='x')
    e_name.bind('<FocusOut>', lambda e: editor._apply_zone_panel_fields())
    e_name.bind('<Return>', lambda e: editor._apply_zone_panel_fields())

    editor._zone_add_btn = tk.Button(
        body, text="Add Zone", command=editor._add_new_zone,
        bg=C_ACCENT, fg='white', font=('Consolas',9,'bold'),
        relief='flat', padx=6)
    editor._zone_add_btn.pack(fill='x', pady=(4,3))

    row_label("Color")
    color_row = tk.Frame(body, bg=C_PANEL)
    color_row.pack(fill='x')
    editor._zone_color_preview = tk.Label(color_row, text="    ",
                                        bg=editor._zone_color_var.get(),
                                        relief='sunken', bd=1)
    editor._zone_color_preview.pack(side='left', padx=(0,5))
    e_color = tk.Entry(color_row, textvariable=editor._zone_color_var,
                       bg=C_PANEL2, fg=C_TEXT, insertbackground=C_TEXT,
                       font=('Consolas',9), width=10, relief='flat')
    e_color.pack(side='left', fill='x', expand=True)
    e_color.bind('<FocusOut>', lambda e: editor._apply_zone_panel_fields())
    e_color.bind('<Return>', lambda e: editor._apply_zone_panel_fields())
    tk.Button(color_row, text="Wheel", command=editor._choose_zone_color,
              bg=C_ACCENT, fg='white', font=('Consolas',9),
              relief='flat', padx=5).pack(side='left', padx=(5,0))

    row_label("Generation")

    opt = tk.Frame(body, bg=C_PANEL)
    opt.pack(fill='x', pady=(4,0))
    editor._zone_b12_label = tk.Label(opt, text="b12", bg=C_PANEL, fg=C_DIM, font=('Consolas',9))
    editor._zone_b12_label.grid(row=0, column=0, sticky='w')
    editor._zone_b12_entry = tk.Entry(opt, textvariable=editor._zone_b12_var, width=6,
                                    bg=C_PANEL2, fg=C_TEXT, insertbackground=C_TEXT,
                                    font=('Consolas',9), relief='flat')
    editor._zone_b12_entry.grid(row=0, column=1, padx=(4,12), sticky='w')
    editor._zone_b15_label = tk.Label(opt, text="b15", bg=C_PANEL, fg=C_DIM, font=('Consolas',9))
    editor._zone_b15_label.grid(row=0, column=2, sticky='w')
    editor._zone_b15_entry = tk.Entry(opt, textvariable=editor._zone_b15_var, width=6,
                                    bg=C_PANEL2, fg=C_TEXT, insertbackground=C_TEXT,
                                    font=('Consolas',9), relief='flat')
    editor._zone_b15_entry.grid(row=0, column=3, padx=(4,0), sticky='w')
    editor._zone_ignore_cb = tk.Checkbutton(
        body, text="Ignore collisions",
        variable=editor._zone_ignore_var,
        bg=C_PANEL, fg=C_TEXT, selectcolor=C_PANEL2,
        activebackground=C_PANEL, activeforeground=C_TEXT,
        font=('Consolas',9), anchor='w',
        command=editor._on_zone_ignore_toggle)
    editor._zone_ignore_cb.pack(fill='x', pady=(3,0))
    editor._sync_zone_generation_controls()

    tile_row = tk.Frame(body, bg=C_PANEL)
    tile_row.pack(fill='x', pady=(3,3))
    editor._tile_clear_btn = tk.Button(
        tile_row, text="Clear Tiles", command=editor._clear_tile_selection,
        bg=C_PANEL2, fg=C_DIM, font=('Consolas',9),
        relief='flat', padx=6)
    editor._tile_clear_btn.pack(side='left', fill='x', expand=True)

    btn_row = tk.Frame(body, bg=C_PANEL)
    btn_row.pack(fill='x', pady=(3,4))
    tk.Button(btn_row, text="Generate Nodes", command=editor.generate_selected_zone_nodes,
              bg='#006644', fg='white', font=('Consolas',9,'bold'),
              relief='flat', padx=6).pack(side='left', fill='x', expand=True, padx=(0,3))
    tk.Button(btn_row, text="Clear Nodes", command=editor.clear_selected_zone_nodes,
              bg='#664400', fg='white', font=('Consolas',9),
              relief='flat', padx=6).pack(side='left', fill='x', expand=True, padx=(3,0))

    tk.Frame(body, bg=C_BORDER, height=1).pack(fill='x', pady=(8,8))

    tk.Label(body, text="Zones / Owned Nodes", bg=C_PANEL, fg=C_TEXT,
             font=('Consolas',10,'bold'), anchor='w').pack(fill='x')
    editor._zone_listbox = tk.Listbox(body, height=10, bg='#202020', fg=C_TEXT,
                                    selectbackground=C_ACCENT,
                                    font=('Consolas',9), relief='flat',
                                    exportselection=False)
    editor._zone_listbox.pack(fill='both', expand=True, pady=(2,5))
    editor._zone_listbox.bind('<<ListboxSelect>>', editor._on_zone_list_select)
    editor._zone_listbox.bind('<ButtonRelease-1>', editor._on_zone_list_click)
    editor._zone_listbox.bind('<Delete>', editor._on_zone_list_delete_key)
    editor._zone_list_items = []

    tk.Button(body, text="Delete Selected Zone", command=editor._delete_selected_zone,
              bg='#663333', fg='white', font=('Consolas',9),
              relief='flat').pack(fill='x', pady=(2,2))
    tk.Button(body, text="Delete All Zones", command=editor._clear_all_zones,
              bg='#552222', fg='white', font=('Consolas',9),
              relief='flat').pack(fill='x', pady=(2,2))

    editor._refresh_zone_list()
    # The left inspector performs its first Developer-mode sync before this
    # right-hand panel exists, so apply the current semantic/raw labels once
    # the Area Zones controls have been created.
    editor._sync_dev_mode_inspector()


def help_panel_palette(editor):
    """Theme colours for the floating key-bind help panel."""
    try:
        light = bool(editor.light_panels.get())
    except Exception:
        light = False

    if light:
        return {
            'bg': '#ffffff',
            'fg': '#1c1c1c',
            # The quick-reference note is explanatory text, not disabled text.
            # Keep it comfortably readable in both themes.
            'note': '#3f3f3f',
            'section': '#444444',
            'key': '#005a9e',
            'border': '#b8b8b8',
            'divider': '#d0d0d0',
            'close_bg': '#ffffff',
            'close_fg': '#555555',
            'close_active_bg': '#e8e8e8',
            'close_active_fg': '#111111',
        }

    return {
        'bg': '#1e1e1e',
        'fg': '#e6e6e6',
        # Do not use C_DIM here: this sentence is information the user is
        # expected to read, so it must not look disabled/faded.
        'note': '#c8c8c8',
        'section': '#c0c0c0',
        'key': '#88ccff',
        'border': '#626262',
        'divider': '#555555',
        'close_bg': '#1e1e1e',
        'close_fg': '#b8b8b8',
        'close_active_bg': '#4b4b4b',
        'close_active_fg': '#f0f0f0',
    }


def sync_help_panel_theme(editor):
    """Apply the active light/dark theme to every persistent help-panel widget."""
    if not hasattr(editor, '_help_panel') or not editor._help_panel.winfo_exists():
        return
    pal = editor._help_panel_palette()

    def cfg(widget, **kwargs):
        try:
            if widget is not None and widget.winfo_exists():
                widget.configure(**kwargs)
        except Exception:
            pass

    cfg(editor._help_panel, bg=pal['bg'],
        highlightbackground=pal['border'], highlightcolor=pal['border'])
    cfg(getattr(editor, '_help_header_frame', None), bg=pal['bg'])
    cfg(getattr(editor, '_help_header_label', None), bg=pal['bg'], fg=pal['fg'])
    cfg(getattr(editor, '_help_close_btn', None),
        bg=pal['close_bg'], fg=pal['close_fg'],
        activebackground=pal['close_active_bg'],
        activeforeground=pal['close_active_fg'])
    cfg(getattr(editor, '_help_quick_reference_label', None),
        bg=pal['bg'], fg=pal['note'])
    cfg(getattr(editor, '_help_header_divider', None), bg=pal['divider'])
    cfg(getattr(editor, '_help_content_frame', None), bg=pal['bg'])

    # Content rows are rebuilt because their labels are intentionally
    # ephemeral. Persistent header/note widgets above are rethemed in place.
    try:
        editor._refresh_help_content()
    except Exception:
        pass


def build_help_panel(editor):
    """Build the floating help overlay panel."""
    pal = editor._help_panel_palette()
    editor._help_panel = tk.Frame(
        editor, bg=pal['bg'],
        relief='solid', bd=1,
        highlightbackground=pal['border'],
        highlightcolor=pal['border'],
        highlightthickness=1)

    # Header row. Keep references to persistent widgets so switching
    # light/dark mode can retheme the already-open panel correctly.
    hdr = editor._help_header_frame = tk.Frame(editor._help_panel, bg=pal['bg'])
    hdr.pack(fill='x', padx=6, pady=(6,2))
    editor._help_header_label = tk.Label(
        hdr, text='Keybinds', bg=pal['bg'], fg=pal['fg'],
        font=('Consolas', 10, 'bold'), anchor='w')
    editor._help_header_label.pack(side='left')
    editor._help_close_btn = tk.Button(
        hdr, text='✕', command=editor._toggle_help_panel,
        bg=pal['close_bg'], fg=pal['close_fg'], font=('Consolas', 9),
        relief='flat', padx=2, pady=0,
        activebackground=pal['close_active_bg'],
        activeforeground=pal['close_active_fg'])
    editor._help_close_btn.pack(side='right')

    editor._help_quick_reference_label = tk.Label(
        editor._help_panel,
        text='Quick reference only. See the guide for the full keybind list',
        bg=pal['bg'], fg=pal['note'], font=('Consolas', 9),
        anchor='w', justify='left', wraplength=314)
    editor._help_quick_reference_label.pack(fill='x', padx=6, pady=(1,5))

    editor._help_header_divider = tk.Frame(
        editor._help_panel, bg=pal['divider'], height=1)
    editor._help_header_divider.pack(fill='x', padx=6, pady=(0,4))

    # Content area — rebuilt on mode change
    editor._help_content_frame = tk.Frame(editor._help_panel, bg=pal['bg'])
    editor._help_content_frame.pack(fill='both', padx=6, pady=(0,6))

    editor._refresh_help_content()


def refresh_help_content(editor):
    """Rebuild help content for current mode."""
    if not hasattr(editor, '_help_content_frame'):
        return
    for w in editor._help_content_frame.winfo_children():
        w.destroy()

    f = editor._help_content_frame
    pal = editor._help_panel_palette()
    try:
        f.configure(bg=pal['bg'])
    except Exception:
        pass
    mode = editor.mode.get() if hasattr(editor, 'mode') else 'edit'
    sections = editor._HELP_CONTENT.get(mode, []) + ['---'] + editor._HELP_GLOBAL

    def _row(parent, key, val):
        if val is None:
            # Section header
            tk.Label(parent, text=key, bg=pal['bg'], fg=pal['section'],
                     font=('Consolas', 9, 'bold'), anchor='w').pack(
                     fill='x', pady=(4,1))
            tk.Frame(parent, bg=pal['divider'], height=1).pack(
                fill='x', pady=(0,3))
        else:
            row = tk.Frame(parent, bg=pal['bg'])
            row.pack(fill='x', pady=1)
            tk.Label(row, text=key, bg=pal['bg'], fg=pal['key'],
                     font=('Consolas', 9), width=18, anchor='w').pack(
                     side='left')
            tk.Label(row, text=val, bg=pal['bg'], fg=pal['fg'],
                     font=('Consolas', 9), anchor='w').pack(side='left')

    for item in sections:
        if item == '---':
            tk.Frame(f, bg=pal['divider'], height=1).pack(
                fill='x', pady=(6,2))
        else:
            k, v = item
            _row(f, k, v)


def toggle_help_panel(editor):
    """Show or hide the help overlay panel."""
    editor._help_visible = not editor._help_visible
    if editor._help_visible:
        if not hasattr(editor, '_help_panel') or not editor._help_panel.winfo_exists():
            editor._build_help_panel()
        else:
            editor._refresh_help_content()
        # Position below Help button, top-right of window
        editor._sync_topbar_theme()
        editor.update_idletasks()
        # Get button position relative to the root window
        bx = editor._topbar_help_btn.winfo_rootx() - editor.winfo_rootx()
        by = editor._topbar_help_btn.winfo_rooty() - editor.winfo_rooty()
        bw = editor._topbar_help_btn.winfo_width()
        bh = editor._topbar_help_btn.winfo_height()
        pw = 330
        x  = bx + bw - pw  # right-align with right edge of button
        y  = by + bh + 2   # just below the button
        # Place relative to editor (the root Tk window)
        editor._help_panel.place(in_=editor, x=x, y=y, width=pw)
        editor._help_panel.lift()
    else:
        if hasattr(editor, '_help_panel') and editor._help_panel.winfo_exists():
            editor._help_panel.place_forget()
        editor._sync_topbar_theme()


def build_left_panel(editor, parent):
    # ── Scrollable left panel container ──────────────
    outer = tk.Frame(parent, bg=C_PANEL, width=200, bd=0)
    outer.grid(row=0, column=0, sticky='ns')
    outer.pack_propagate(False)
    outer.grid_propagate(False)

    # Canvas + inner content frame for scrolling
    editor._side_canvas = tk.Canvas(outer, bg=C_PANEL, highlightthickness=0,
                                  width=200, bd=0)
    editor._side_canvas.pack(side='left', fill='both', expand=True)

    panel = tk.Frame(editor._side_canvas, bg=C_PANEL, bd=0)
    editor._side_canvas_window = editor._side_canvas.create_window(
        (0, 0), window=panel, anchor='nw')

    def _on_panel_configure(e):
        # Recalculate from the real packed content and visible viewport.
        # bbox('all') alone permits Tk to shift the canvas origin when the
        # content is shorter than the viewport, creating fake scrolling.
        editor.after_idle(editor._refresh_left_panel_scrollregion)

    def _on_side_canvas_configure(e):
        editor._side_canvas.itemconfig(
            editor._side_canvas_window, width=e.width)
        # A window resize can change whether scrolling is required even
        # when the inner frame itself did not receive a Configure event.
        editor.after_idle(editor._refresh_left_panel_scrollregion)

    panel.bind('<Configure>', _on_panel_configure)
    editor._side_canvas.bind('<Configure>', _on_side_canvas_configure)

    # Mouse wheel scroll — bind to canvas AND panel frame
    # so child widgets also trigger scroll
    def _on_panel_wheel(e):
        # Only scroll if mouse is over the left panel area
        wx = e.x_root
        try:
            lx = outer.winfo_rootx()
            lw = outer.winfo_width()
            if not (lx <= wx <= lx + lw):
                return
        except Exception:
            pass
        editor._side_canvas.yview_scroll(
            int(-1 * (e.delta / 120)) if e.delta else
            (-1 if e.num == 4 else 1), 'units')
        editor._update_scroll_arrow()
        return 'break'

    # Bind to all widgets in panel via bind_all with tag check
    outer.bind('<MouseWheel>', _on_panel_wheel)
    outer.bind('<Button-4>', _on_panel_wheel)
    outer.bind('<Button-5>', _on_panel_wheel)
    editor._side_canvas.bind('<MouseWheel>', _on_panel_wheel)
    editor._side_canvas.bind('<Button-4>', _on_panel_wheel)
    editor._side_canvas.bind('<Button-5>', _on_panel_wheel)
    # Store for binding child widgets
    editor._panel_wheel_cb = _on_panel_wheel
    # Bind content frame
    panel.bind('<MouseWheel>', _on_panel_wheel)
    panel.bind('<Button-4>',   _on_panel_wheel)
    panel.bind('<Button-5>',   _on_panel_wheel)
    editor._scroll_panel_frame = panel

    # Widget classes that handle their own scrolling
    # and must NOT get the panel scroll handler bound to them.
    _SCROLL_OWN = (tk.Listbox, tk.Text, tk.Canvas, ttk.Combobox)

    def _bind_scroll_to_widget(w):
        """Recursively bind mouse wheel to all panel children.
        Skips widgets that manage their own scrolling.
        """
        try:
            # Skip editor-scrolling widgets — they handle wheel internally
            # and use 'break' to stop propagation
            if isinstance(w, _SCROLL_OWN):
                return
            w.bind('<MouseWheel>', _on_panel_wheel)
            w.bind('<Button-4>',   _on_panel_wheel)
            w.bind('<Button-5>',   _on_panel_wheel)
            for child in w.winfo_children():
                _bind_scroll_to_widget(child)
        except Exception:
            pass
    editor._bind_scroll_to_widget = _bind_scroll_to_widget

    # Click on panel gives it arrow key focus
    def _grab_panel_focus(e):
        editor._panel_has_focus = True
    def _release_panel_focus(e):
        editor._panel_has_focus = False
    outer.bind_all('<ButtonPress-1>', lambda e: _grab_panel_focus(e)
                   if e.widget in (outer, editor._side_canvas, panel) or
                   str(e.widget).startswith(str(panel)) else
                   _release_panel_focus(e), add='+')

    editor._panel_has_focus = False

    def _on_key(e):
        if not getattr(editor, '_panel_has_focus', False):
            return
        if e.keysym == 'Down':
            editor._side_canvas.yview_scroll(1, 'units')
            editor._update_scroll_arrow()
            return 'break'
        elif e.keysym == 'Up':
            editor._side_canvas.yview_scroll(-1, 'units')
            editor._update_scroll_arrow()
            return 'break'
    outer.bind_all('<Key>', _on_key, add='+')

    # ── Animated scroll arrow indicator ──────────────
    editor._scroll_arrow_shown = False
    editor._scroll_arrow_dismissed = False
    editor._scroll_arrow_anim_id = None
    editor._scroll_arrow_offset = 0

    editor._scroll_arrow_btn = tk.Label(
        outer, text='▼', bg='#2a2a2a', fg='#ffdd00',
        font=('Consolas', 10, 'bold'), cursor='hand2',
        relief='solid', bd=2, padx=6, pady=3,
        highlightbackground='#ffdd00',
        highlightthickness=2)
    editor._scroll_arrow_btn.bind('<Button-1>', editor._dismiss_scroll_arrow)

    def _update_scroll_arrow(editor=editor):
        editor._update_scroll_arrow()
    editor._update_scroll_arrow_cb = _update_scroll_arrow

    # Store outer for later reference
    editor._left_panel_outer = outer

    # ── SHOW section — tree layout ──────────────────
    _F = 'Consolas'   # font family shorthand

    def _chk(parent, text, var, cmd=None, fg=C_TEXT, font_size=9):
        return tk.Checkbutton(parent, text=text, variable=var,
                              bg=C_PANEL, fg=fg, selectcolor=C_PANEL2,
                              activebackground=C_PANEL, activeforeground=fg,
                              font=(_F, font_size), anchor='w',
                              command=cmd or editor.redraw)

    def _tree_children(parent, items, last_indices=None):
        """Draw child rows with ├─ / └─ circuit-board connectors."""
        if last_indices is None:
            last_indices = {len(items)-1}
        for i, item in enumerate(items):
            is_last = (i == len(items)-1)
            row = tk.Frame(parent, bg=C_PANEL)
            row.pack(fill='x', padx=0, pady=0)
            # Vertical line column
            line_col = tk.Frame(row, bg=C_PANEL, width=14)
            line_col.pack(side='left', fill='y')
            line_col.pack_propagate(False)
            # Draw vertical + horizontal connector using canvas
            c = tk.Canvas(line_col, bg=C_PANEL, width=14, height=20,
                          highlightthickness=0)
            c.pack(fill='both', expand=True)
            # Vertical line (top half always, bottom half only if not last)
            c.create_line(7, 0, 7, 10, fill=C_BORDER, width=1)
            if not is_last:
                c.create_line(7, 10, 7, 20, fill=C_BORDER, width=1)
            # Horizontal connector
            c.create_line(7, 10, 14, 10, fill=C_BORDER, width=1)
            # The checkbox
            var, lbl, kwargs = item
            fg  = kwargs.get('fg', C_TEXT)
            cmd = kwargs.get('cmd', editor.redraw)
            cb  = _chk(row, lbl, var, cmd=cmd, fg=fg)
            cb.pack(side='left', fill='x', expand=True)
        
    # ── GRID STYLE ─────────────────────────────────
    # ── Grid controls — MED only, — / label / + ──────
    grid_box = tk.Frame(panel, bg=C_PANEL)
    grid_box.pack(fill='x', padx=8, pady=(4, 2))

    tk.Label(grid_box, text="Grid:", bg=C_PANEL, fg=C_LABEL,
             font=('Consolas',9), width=6, anchor='w').pack(side='left')

    # — button
    def _grid_step_down():
        editor._grid_step_down()

    def _grid_step_up():
        editor._grid_step_up()

    tk.Button(grid_box, text='−', command=_grid_step_down,
              bg=C_PANEL2, fg=C_TEXT,
              font=('Consolas', 8), relief='solid', bd=1, padx=6, pady=0,
              highlightbackground=C_BORDER, highlightthickness=1,
              activebackground=C_BORDER,
              activeforeground=C_TEXT).pack(side='left')

    # Spacing label — also a dropdown
    _med_om = tk.OptionMenu(grid_box, editor.grid_fixed_spacing_label,
                            *[label for label, value in MED_GRID_SPACING_CHOICES],
                            command=lambda *_: editor._on_grid_display_change())
    _med_om.configure(bg=C_PANEL2, fg=C_TEXT, activebackground=C_BORDER,
                      activeforeground=C_TEXT, relief='solid', bd=1,
                      font=('Consolas',8), highlightbackground=C_BORDER,
                      highlightthickness=1, width=7, anchor='center')
    _med_om["menu"].configure(bg=C_PANEL2, fg=C_TEXT,
                               activebackground=C_BORDER,
                               activeforeground=C_TEXT)
    _med_om.pack(side='left', padx=2)

    # + button
    tk.Button(grid_box, text='+', command=_grid_step_up,
              bg=C_PANEL2, fg=C_TEXT,
              font=('Consolas', 8), relief='solid', bd=1, padx=6, pady=0,
              highlightbackground=C_BORDER, highlightthickness=1,
              activebackground=C_BORDER,
              activeforeground=C_TEXT).pack(side='left')

    # Grid snap toggle
    snap_box = tk.Frame(panel, bg=C_PANEL)
    snap_box.pack(fill='x', padx=8, pady=(0,4))
    tk.Checkbutton(snap_box, text="Snap nodes to grid",
                   variable=editor.grid_snap_nodes,
                   bg=C_PANEL, fg=C_TEXT, selectcolor=C_PANEL2,
                   activebackground=C_PANEL, activeforeground=C_TEXT,
                   font=('Consolas',9), anchor='w').pack(side='left')

    # ── Grid / Terrain toggles ───────────────────────
    _chk(panel, 'Grid',    editor.show_grid).pack(fill='x', padx=8, pady=(2,0))
    terrain_row = tk.Frame(panel, bg=C_PANEL)
    terrain_row.pack(fill='x', padx=8)
    _chk(terrain_row, 'Terrain', editor.show_terrain).pack(side='left')
    editor._terrain_mode_buttons = {}
    for mode in ('C', 'H', 'D'):
        button = tk.Radiobutton(
            terrain_row, text=mode, variable=editor.terrain_display_mode,
            value=mode, indicatoron=False, width=2, padx=0, pady=0,
            bg=C_PANEL2, fg=C_TEXT, selectcolor=C_ACCENT,
            activebackground=C_ACCENT, activeforeground='#ffffff',
            relief='raised', offrelief='raised', borderwidth=1,
            font=(_F, 9, 'bold'),
            command=lambda selected=mode: editor._set_terrain_display_mode(selected)
        )
        button.pack(side='left', padx=(2 if mode == 'C' else 0, 0))
        editor._terrain_mode_buttons[mode] = button
    editor._sync_terrain_mode_button_colors()

    tk.Frame(panel, bg=C_BORDER, height=1).pack(fill='x', padx=8, pady=(4,2))

    # ── Nodes (parent) ──────────────────────────────
    nodes_row = tk.Frame(panel, bg=C_PANEL)
    nodes_row.pack(fill='x', padx=8, pady=(8,0))
    _chk(nodes_row, 'Nodes', editor.show_nodes).pack(side='left')
    # Deactivate all button
    _node_vars = [editor.show_nodes, editor.show_edges, editor.show_radius,
                  editor.show_zones, editor.show_ids, editor.show_breach_zones]

    def _any_node_active():
        return any(v.get() for v in _node_vars)

    def _update_deact_btn_style():
        light = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
        if _any_node_active():
            # At least one active — neutral button, "Deactivate all"
            if light:
                _deact_btn.configure(
                    text='Deactivate all',
                    fg='#000000',
                    bg='#eeeeee',
                    activebackground='#d8d8d8',
                    activeforeground='#000000',
                    highlightbackground='#b8b8b8',
                    highlightcolor='#b8b8b8',
                    highlightthickness=1,
                    relief='solid')
            else:
                _deact_btn.configure(
                    text='Deactivate all',
                    fg='#ffffff',
                    bg=C_PANEL2,
                    activebackground=C_BORDER,
                    activeforeground='#ffffff',
                    highlightbackground=C_BORDER,
                    highlightcolor=C_BORDER,
                    highlightthickness=1,
                    relief='solid')
        else:
            # All off — blue, "Activate all"
            if light:
                _deact_btn.configure(
                    text='Activate all',
                    fg='#003a66',
                    bg='#dbe9ff',
                    activebackground='#c9dcf4',
                    activeforeground='#003a66',
                    highlightbackground='#7aa7d9',
                    highlightcolor='#7aa7d9',
                    highlightthickness=1,
                    relief='solid')
            else:
                _deact_btn.configure(
                    text='Activate all',
                    fg='#88ccff',
                    bg='#1a2a3a',
                    activebackground='#23384f',
                    activeforeground='#88ccff',
                    highlightbackground='#0078d4',
                    highlightcolor='#0078d4',
                    highlightthickness=1,
                    relief='solid')

    def _deactivate_all_nodes():
        all_on = all(v.get() for v in _node_vars)
        new_val = not all_on
        for v in _node_vars:
            v.set(new_val)
        _update_deact_btn_style()
        editor.redraw()

    # Also update button style whenever individual toggles change
    def _on_node_var_change(*_):
        _update_deact_btn_style()
    for _v in _node_vars:
        _v.trace_add('write', _on_node_var_change)

    _deact_btn = tk.Button(nodes_row, text='Deactivate all',
                           command=_deactivate_all_nodes,
                           bg=C_PANEL2, fg='#ffffff',
                           font=(_F, 8), relief='solid', bd=1,
                           padx=6, pady=0,
                           highlightbackground=C_BORDER, highlightthickness=1,
                           activebackground=C_BORDER,
                           activeforeground=C_TEXT)
    _deact_btn.pack(side='right', padx=(4,0))
    editor._refresh_node_deactivate_button_style = _update_deact_btn_style
    _update_deact_btn_style()

    _tree_children(panel, [
        (editor.show_edges,       'Connections',    {}),
        (editor.show_radius,      'Radius',         {}),
        (editor.show_zones,       'Zone colours',   {}),
        (editor.show_ids,         'Node IDs',       {}),
        (editor.show_breach_zones,'Takedown zones',  {}),
    ])
    def _update_edge_zone_color_state(*_):
        if not (editor.show_breach_zones.get() or editor.show_zones.get()):
            editor.show_edge_zone_color.set(False)
    editor.show_breach_zones.trace_add('write', _update_edge_zone_color_state)
    editor.show_zones.trace_add('write', _update_edge_zone_color_state)

    # ── Entities (parent) ───────────────────────────
    tk.Frame(panel, bg=C_BORDER, height=1).pack(fill='x', padx=8, pady=(4,2))
    ent_row = tk.Frame(panel, bg=C_PANEL)
    ent_row.pack(fill='x', padx=8, pady=(0,0))

    def _on_entities_toggle():
        v = editor.show_entities.get()
        for var in [editor.show_buildings, editor.show_vehicles, editor.show_objects,
                    editor.show_decorations, editor.show_foliage]:
            var.set(v)
        if v:
            _on_foliage_toggle()
        else:
            _ns.set_val('SHOW_FOLIAGE', False)
            editor.redraw()

    _chk(ent_row, 'Entities', editor.show_entities,
         cmd=_on_entities_toggle).pack(side='left')

    def _on_foliage_toggle():
        enabled = bool(editor.show_foliage.get())
        _ns.set_val('SHOW_FOLIAGE', enabled)
        if enabled:
            if not getattr(editor, '_foliage_warned', False):
                editor._foliage_warned = True
                messagebox.showwarning(
                    'Performance Warning',
                    'Foliage rendering can significantly impact performance '
                    'on large maps.\n\nDeactivate if the editor becomes slow.',
                    parent=editor)
            # Explicit layer activation means load the complete unique
            # foliage type set now. Do not depend on viewport zoom, the
            # per-render request budget, or repeated redraws.
            load_token = int(getattr(editor, '_foliage_load_token', 0)) + 1
            editor._foliage_load_token = load_token
            editor.status('Foliage: loading model set...')

            def _foliage_ready():
                def _finish_on_ui():
                    if (load_token != getattr(editor, '_foliage_load_token', 0) or
                            not editor.show_foliage.get()):
                        return
                    editor.status('Foliage: model set loaded')
                    editor.redraw()
                editor.after(0, _finish_on_ui)

            _preload = _ns.get('preload_cmodels_for_entities', None)
            thread = _preload(
                editor.entities, editor.bms_path, log=editor.status,
                on_done=_foliage_ready, categories={'foliage'},
                max_types=0, worker_sleep=0.0,
                thread_name='FoliageCModelPreload') if _preload else None
            if thread is None:
                editor.redraw()
            return

        # Invalidate completion callbacks from an earlier activation.
        editor._foliage_load_token = int(
            getattr(editor, '_foliage_load_token', 0)) + 1
        editor.redraw()

    def _on_decorations_toggle():
        # Objects remain a separate View-menu layer, but the broader
        # left-panel Decorations control intentionally includes them.
        editor.show_objects.set(bool(editor.show_decorations.get()))
        editor.redraw()

    _tree_children(panel, [
        (editor.show_buildings,   'Buildings',   {}),
        (editor.show_vehicles,    'Vehicles',    {}),
        (editor.show_decorations, 'Decorations',
         {'cmd': _on_decorations_toggle}),
        (editor.show_foliage,     '⚠ Foliage',
         {'fg': '#ff4444', 'cmd': _on_foliage_toggle}),
    ])


    # ── Node metadata — Developer mode only ──────────
    # Keep the section constructed so it participates in theme updates, but
    # leave it unpacked in the normal public UI. _sync_dev_mode_inspector()
    # inserts/removes the whole block immediately above the Z filter.
    editor._node_metadata_section = tk.Frame(panel, bg=C_PANEL)
    tk.Frame(editor._node_metadata_section, bg=C_BORDER, height=1).pack(
        fill='x', padx=8, pady=(4,2))
    tk.Label(editor._node_metadata_section, text='Node metadata',
             bg=C_PANEL, fg=C_LABEL,
             font=(_F, 9, 'bold'), anchor='w').pack(
        fill='x', padx=8, pady=(2,0))
    _tree_children(editor._node_metadata_section, [
        (editor.show_b12_extended, 'Extended b12', {'cmd': editor._on_extended_b12_toggle}),
        (editor.show_b16_arrows,   'b16 arrows',   {}),
    ])

    # ── Z filter ──────────────────────────────────
    editor._z_filter_separator = tk.Frame(panel, bg=C_BORDER, height=1)
    editor._z_filter_separator.pack(fill='x', padx=4, pady=(6,2))
    zf_row = tk.Frame(panel, bg=C_PANEL)
    zf_row.pack(fill='x', padx=8, pady=(2,0))
    tk.Checkbutton(zf_row, text="Z filter", variable=editor.z_filter_on,
                   bg=C_PANEL, fg=C_TEXT, selectcolor=C_PANEL2,
                   activebackground=C_PANEL, activeforeground=C_TEXT,
                   font=('Consolas',8), anchor='w',
                   command=editor._on_zfilter_toggle).pack(side='left')
    editor._zf_frame = tk.Frame(panel, bg=C_PANEL)
    zf_min_row = tk.Frame(editor._zf_frame, bg=C_PANEL)
    zf_min_row.pack(fill='x', padx=8, pady=1)
    tk.Label(zf_min_row, text="Min Z:", bg=C_PANEL, fg=C_LABEL,
             font=('Consolas',9), width=6, anchor='w').pack(side='left')
    e_zmin = tk.Entry(zf_min_row, textvariable=editor.z_filter_min, width=8,
             bg=C_PANEL2, fg=C_TEXT, insertbackground=C_TEXT,
             font=('Consolas',8), relief='flat')
    e_zmin.pack(side='left')
    e_zmin.bind('<Return>', lambda e: (editor._rebuild_hit_tester(), editor.redraw()))
    e_zmin.bind('<FocusOut>', lambda e: (editor._rebuild_hit_tester(), editor.redraw()))
    tk.Label(zf_min_row, text="m", bg=C_PANEL, fg=C_LABEL,
             font=('Consolas',9)).pack(side='left', padx=2)

    zf_max_row = tk.Frame(editor._zf_frame, bg=C_PANEL)
    zf_max_row.pack(fill='x', padx=8, pady=1)
    tk.Label(zf_max_row, text="Max Z:", bg=C_PANEL, fg=C_LABEL,
             font=('Consolas',9), width=6, anchor='w').pack(side='left')
    e_zmax = tk.Entry(zf_max_row, textvariable=editor.z_filter_max, width=8,
             bg=C_PANEL2, fg=C_TEXT, insertbackground=C_TEXT,
             font=('Consolas',8), relief='flat')
    e_zmax.pack(side='left')
    e_zmax.bind('<Return>', lambda e: (editor._rebuild_hit_tester(), editor.redraw()))
    e_zmax.bind('<FocusOut>', lambda e: (editor._rebuild_hit_tester(), editor.redraw()))
    tk.Label(zf_max_row, text="m", bg=C_PANEL, fg=C_LABEL,
             font=('Consolas',9)).pack(side='left', padx=2)

    # ── separator ─────────────────────────────────
    # Stored so the delayed Min/Max Z frame can always be inserted directly
    # above it instead of being appended to the bottom of the panel.
    editor._post_z_filter_separator = tk.Frame(panel, bg=C_BORDER, height=1)
    editor._post_z_filter_separator.pack(fill='x', padx=4, pady=6)

    # ── INFO / TILER tabs ─────────────────────────
    tab_bar = tk.Frame(panel, bg=C_PANEL)
    tab_bar.pack(fill='x')
    # Start with neither lower tool drawer active.  The tab buttons remain
    # visible, but their content is only packed when the user opens it.
    editor._tab = tk.StringVar(value='')
    editor._active_tab = None
    # Keep the Bytes inspector implemented and callable for diagnostics, but
    # do not expose it as a normal editor tab.
    for t, lbl in [('info','Info'),('tiler','Tiler')]:
        tk.Radiobutton(tab_bar, text=lbl, variable=editor._tab, value=t,
                       bg=C_PANEL, fg=C_TEXT, selectcolor=C_PANEL2,
                       activebackground=C_PANEL,
                       font=('Consolas',8), indicatoron=0,
                       relief='flat', bd=1, padx=6,
                       command=editor._switch_tab).pack(side='left')

    # Match the section dividers used throughout the rest of the left panel.
    # This remains visible even while both lower drawers are collapsed.
    editor._tab_bottom_separator = tk.Frame(panel, bg=C_BORDER, height=1)
    editor._tab_bottom_separator.pack(fill='x', padx=4, pady=(2,0))

    # ── Tab content area ──────────────────────────
    # Construct it now so all existing inspector/tiler code can keep using
    # the same parent, but leave it unpacked until a tab is opened.
    editor._tab_frame = tk.Frame(panel, bg=C_PANEL2, relief='sunken', bd=1)

    editor._build_info_tab()
    editor._build_tiler_tab()
    editor._build_bytes_tab()
    editor._switch_tab(force=True)


def apply_panel_theme(editor, light, root=None):
    """Best-effort light/dark retheme of the Tk panels and menus: walk the widget tree and
    remap the app's known dark colours to light equivalents (and back). Render canvases are
    skipped (2D follows the 'Light mode: canvas' toggle; the 3D view stays dark). Pass a
    `root` widget to re-theme just that subtree -- used to catch dynamically rebuilt widgets
    (e.g. the 3D zone-filter list) so they match after they're recreated."""
    dark2light = UI_DARK_TO_LIGHT_COLOR_MAP
    cmap = dict(dark2light) if light else {v: k for k, v in dark2light.items()}
    _light_set = set(dark2light.values())
    opts = ('background', 'foreground', 'activebackground', 'activeforeground',
            'selectcolor', 'selectbackground', 'selectforeground', 'highlightbackground',
            'highlightcolor', 'insertbackground', 'troughcolor',
            'disabledforeground', 'readonlybackground')
    def _remap(w):
        for opt in opts:
            try:
                cur = str(w.cget(opt)).strip().lower()
            except Exception:
                continue
            if cur in cmap:
                try:
                    w.configure(**{opt: cmap[cur]})
                except Exception:
                    pass
        # Light mode: white text left on a now-light background is invisible. Darken it
        # only when the background is one of our light colours (so coloured accent buttons
        # like Generate/Takedown keep their white text). On restore it becomes light grey.
        if light:
            try:
                bgnow = str(w.cget('background')).strip().lower()
            except Exception:
                bgnow = ''
            if bgnow in _light_set:
                for fopt in ('foreground', 'activeforeground', 'insertbackground'):
                    try:
                        if str(w.cget(fopt)).strip().lower() in ('#ffffff', '#fff', 'white'):
                            w.configure(**{fopt: '#1c1c1c'})
                    except Exception:
                        pass
    # Render canvases keep their own background: the 2D map follows the 'Light mode:
    # canvas' toggle, and the 3D infrastructure view stays dark on purpose.
    skip = set()
    for _c in (getattr(editor, 'canvas', None),):
        if _c is not None:
            skip.add(_c)
    _v3d = getattr(editor, '_embedded_3d_view', None)
    if _v3d is not None:
        _c3 = getattr(_v3d, 'canvas', None)
        if _c3 is not None:
            skip.add(_c3)

    def _walk(w):
        if w in skip:
            return
        _remap(w)
        try:
            for child in w.winfo_children():
                _walk(child)
        except Exception:
            pass
    if root is not None:
        # Re-theme just a freshly (re)built subtree (dynamic widgets) using current state.
        try:
            _walk(root)
        except Exception:
            pass
        try:
            editor._sync_3d_preset_button_theme()
        except Exception:
            pass
        return
    editor._panel_light = bool(light)
    try:
        _remap(editor)
        for child in editor.winfo_children():
            _walk(child)
    except Exception:
        pass
    try:
        mb = editor.nametowidget(editor['menu']) if editor['menu'] else None
        if mb is not None:
            _remap(mb)
            for child in mb.winfo_children():
                _walk(child)
    except Exception:
        pass
    try:
        # Menus use a dedicated semantic policy instead of color remapping.
        # This also fixes cascades that Tk does not expose as child widgets.
        editor._sync_all_menu_themes()
    except Exception:
        pass
    try:
        editor._sync_topbar_theme()
    except Exception:
        pass
    try:
        if hasattr(editor, '_refresh_node_deactivate_button_style'):
            editor._refresh_node_deactivate_button_style()
    except Exception:
        pass
    try:
        editor._redraw_fps_counter()
    except Exception:
        pass
    try:
        editor._sync_lock_options_panel()
    except Exception:
        pass
    try:
        editor._sync_zone_generation_controls()
    except Exception:
        pass
    try:
        editor._sync_byte_flag_highlights()
    except Exception:
        pass
    try:
        editor._sync_node_help_window_theme()
    except Exception:
        pass
    try:
        editor._sync_3d_preset_button_theme()
    except Exception:
        pass
    try:
        editor._sync_debug_bar_theme()
    except Exception:
        pass
    try:
        editor._sync_adaptive_radius_theme()
    except Exception:
        pass
    try:
        editor._sync_takedown_label_theme()
    except Exception:
        pass
    try:
        views = [getattr(editor, '_embedded_3d_view', None)]
        win = getattr(editor, '_wire3d_window', None)
        views.append(getattr(win, 'view', None) if win is not None else None)
        for view in views:
            if view is not None and hasattr(view, '_update_section_toolbar'):
                view._update_section_toolbar()
    except Exception:
        pass
    try:
        # This panel has dynamic selected/active styling which must be
        # recalculated after the generic widget-tree colour remap.
        editor._update_paint_tool_ui()
    except Exception:
        pass
    try:
        # Menu entries are not ordinary child widgets on every Tk build, so
        # explicitly restore the intended indicator contrast after a theme
        # switch instead of relying on the generic colour remap above.
        editor._sync_view_menu_checkmark_colors()
    except Exception:
        pass
    try:
        editor._sync_terrain_mode_button_colors()
    except Exception:
        pass
