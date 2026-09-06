"""Main AIN editor keyboard/mouse binding helpers.

This module owns registration only; callbacks and state remain supplied by the
composition root through the explicit ``editor`` parameter.
"""

import tkinter as tk
from tkinter import ttk


def bind_events(editor):
        c = editor.canvas
        def _rebuild_selected_neighbors():
            if editor.selected_nodes:
                editor.rebuild_neighbors()

        c.bind('<Motion>',        editor._on_mouse_move)
        c.bind('<ButtonPress-1>', editor._on_lclick)
        c.bind('<B1-Motion>',     editor._on_ldrag)
        c.bind('<ButtonRelease-1>', editor._on_lrelease)
        c.bind('<ButtonPress-2>', editor._on_mclick)
        c.bind('<B2-Motion>',     editor._on_mdrag)
        c.bind('<ButtonRelease-2>', editor._on_mrelease)

        # Extra pan controls for mice/trackpads without a reliable middle button.
        c.bind('<Alt-ButtonPress-1>', editor._on_mclick)
        c.bind('<Alt-B1-Motion>',     editor._on_mdrag)
        c.bind('<Alt-ButtonRelease-1>', editor._on_mrelease)
        c.bind('<space>', lambda e: editor.canvas.focus_set())

        c.bind('<ButtonPress-3>',   editor._on_rclick)
        c.bind('<B3-Motion>',       editor._on_rdrag)
        c.bind('<ButtonRelease-3>', editor._on_rrelease)
        c.bind('<Control-ButtonPress-3>',   editor._on_edge_brush_start)
        c.bind('<Control-B3-Motion>',        editor._on_edge_brush_drag)
        c.bind('<Control-ButtonRelease-3>',  editor._on_edge_brush_release)
        c.bind('<Shift-ButtonPress-3>',   editor._on_cut_start)
        c.bind('<Shift-B3-Motion>',        editor._on_cut_drag)
        c.bind('<Shift-ButtonRelease-3>',  editor._on_cut_release)
        c.bind('<MouseWheel>',    editor._on_scroll)
        c.bind('<Button-4>',      editor._on_scroll)  # Linux scroll up
        c.bind('<Button-5>',      editor._on_scroll)  # Linux scroll down
        c.bind('<Configure>',     lambda e: editor.redraw())

        # Pre-v1 MED-familiar shortcuts.  Bind these to the 2D canvas itself
        # instead of the toplevel so typing in Entry/Text/Combobox widgets can
        # never activate them.  The 3D viewport owns its equivalent F/G/U/zoom
        # bindings independently.
        c.bind('<KeyPress-u>', lambda e: (editor._clear_node_selection_shortcut(), 'break')[1])
        c.bind('<KeyPress-U>', lambda e: (editor._clear_node_selection_shortcut(), 'break')[1])
        c.bind('<KeyPress-F1>', lambda e: (editor._set_terrain_display_mode('C'), 'break')[1])
        c.bind('<KeyPress-F2>', lambda e: (editor._set_terrain_display_mode('H'), 'break')[1])
        c.bind('<KeyPress-F3>', lambda e: (editor._set_terrain_display_mode('D'), 'break')[1])
        c.bind('<KeyPress-f>', lambda e: (editor._fit_all_nodes_shortcut(), 'break')[1])
        c.bind('<KeyPress-F>', lambda e: (editor._fit_all_nodes_shortcut(), 'break')[1])
        c.bind('<KeyPress-g>', lambda e: (editor._toggle_2d_grid_shortcut(), 'break')[1])
        c.bind('<KeyPress-G>', lambda e: (editor._toggle_2d_grid_shortcut(), 'break')[1])
        def _bare_key(e):
            return not (e.state & 0x000C)
        c.bind('<KeyPress-r>', lambda e: (editor._toggle_draw_radius_brush(), 'break')[1] if _bare_key(e) and editor.mode.get() == 'draw' else None)
        c.bind('<KeyPress-R>', lambda e: (editor._toggle_draw_radius_brush(), 'break')[1] if _bare_key(e) and editor.mode.get() == 'draw' else None)
        c.bind('<Control-r>', lambda e: (_rebuild_selected_neighbors(), 'break')[1]
               if getattr(editor, '_view_mode', '2d') == '2d' else None)
        c.bind('<Control-R>', lambda e: (_rebuild_selected_neighbors(), 'break')[1]
               if getattr(editor, '_view_mode', '2d') == '2d' else None)
        c.bind('<KeyPress-e>', lambda e: (editor.mode.set('edit'), editor._on_mode_change(), 'break')[2] if _bare_key(e) else None)
        c.bind('<KeyPress-E>', lambda e: (editor.mode.set('edit'), editor._on_mode_change(), 'break')[2] if _bare_key(e) else None)
        c.bind('<KeyPress-d>', lambda e: (editor.mode.set('draw'), editor._on_mode_change(), 'break')[2] if _bare_key(e) else None)
        c.bind('<KeyPress-D>', lambda e: (editor.mode.set('draw'), editor._on_mode_change(), 'break')[2] if _bare_key(e) else None)
        c.bind('<KeyPress-c>', lambda e: (editor.mode.set('paint'), editor._on_mode_change(), 'break')[2] if _bare_key(e) else None)
        c.bind('<KeyPress-C>', lambda e: (editor.mode.set('paint'), editor._on_mode_change(), 'break')[2] if _bare_key(e) else None)
        c.bind('<KeyPress-z>', lambda e: (editor.mode.set('zone'), editor._on_mode_change(), 'break')[2] if _bare_key(e) else None)
        c.bind('<KeyPress-Z>', lambda e: (editor.mode.set('zone'), editor._on_mode_change(), 'break')[2] if _bare_key(e) else None)
        # Tk reports Page Up / Page Down as the Prior / Next keysyms.
        c.bind('<KeyPress-Prior>', lambda e: (editor._zoom_center(1.2), 'break')[1])
        c.bind('<KeyPress-Next>',  lambda e: (editor._zoom_center(1/1.2), 'break')[1])
        # Optional alias for the existing Shift+Arrow 0.1 m fine nudge.  Returning
        # break is important because the editor also has broader toplevel Arrow
        # bindings for the normal 1 m nudge.
        c.bind('<Control-Left>',  lambda e: (editor._nudge_node(-0.1, 0), 'break')[1])
        c.bind('<Control-Right>', lambda e: (editor._nudge_node( 0.1, 0), 'break')[1])
        c.bind('<Control-Up>',    lambda e: (editor._nudge_node( 0, 0.1), 'break')[1])
        c.bind('<Control-Down>',  lambda e: (editor._nudge_node( 0,-0.1), 'break')[1])

        # Canvas bindings are the primary path, but a focused non-canvas
        # control (for example a mode button) can otherwise consume the 2D
        # shortcuts.  Register a guarded toplevel fallback as well.  The
        # fallback is disabled in 3D mode and while typing in text widgets.
        def _two_d_shortcut_allowed(event):
            if getattr(editor, '_view_mode', '2d') != '2d':
                return False
            try:
                focused = editor.focus_get()
            except Exception:
                focused = None
            if isinstance(focused, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
                return False
            try:
                # Some Windows keyboard layouts report ordinary letter presses
                # with Mod1/Alt (0x0008) still present.  Ctrl is the only
                # modifier that blocks these documented 2D shortcuts.
                return not bool(event.state & 0x0004)
            except Exception:
                return True

        def _bind_two_d_key(sequence, callback):
            def _handler(event):
                if not _two_d_shortcut_allowed(event):
                    return None
                callback()
                return 'break'
            editor.bind(sequence, _handler)

        def _bind_two_d_ctrl_key(sequence, callback):
            """Bind a Ctrl shortcut while retaining 2D/text-focus guards."""
            def _handler(event):
                if getattr(editor, '_view_mode', '2d') != '2d':
                    return None
                try:
                    focused = editor.focus_get()
                except Exception:
                    focused = None
                if isinstance(focused, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
                    return None
                callback()
                return 'break'
            editor.bind(sequence, _handler)

        def _set_mode(mode):
            editor.mode.set(mode)
            editor._on_mode_change()

        for _key, _mode in (
                ('e', 'edit'), ('E', 'edit'),
                ('d', 'draw'), ('D', 'draw'),
                ('c', 'paint'), ('C', 'paint'),
                ('z', 'zone'), ('Z', 'zone')):
            _bind_two_d_key(
                f'<KeyPress-{_key}>',
                lambda m=_mode: _set_mode(m))
        _bind_two_d_key('<KeyPress-f>', editor._fit_all_nodes_shortcut)
        _bind_two_d_key('<KeyPress-F>', editor._fit_all_nodes_shortcut)
        _bind_two_d_key('<KeyPress-g>', editor._toggle_2d_grid_shortcut)
        _bind_two_d_key('<KeyPress-G>', editor._toggle_2d_grid_shortcut)
        _bind_two_d_key('<KeyPress-u>', editor._clear_node_selection_shortcut)
        _bind_two_d_key('<KeyPress-U>', editor._clear_node_selection_shortcut)
        _bind_two_d_key('<KeyPress-F1>', lambda: editor._set_terrain_display_mode('C'))
        _bind_two_d_key('<KeyPress-F2>', lambda: editor._set_terrain_display_mode('H'))
        _bind_two_d_key('<KeyPress-F3>', lambda: editor._set_terrain_display_mode('D'))
        _bind_two_d_key('<KeyPress-Prior>', lambda: editor._zoom_center(1.2))
        _bind_two_d_key('<KeyPress-Next>', lambda: editor._zoom_center(1/1.2))
        _bind_two_d_ctrl_key('<Control-Left>', lambda: editor._nudge_node(-0.1, 0))
        _bind_two_d_ctrl_key('<Control-Right>', lambda: editor._nudge_node(0.1, 0))
        _bind_two_d_ctrl_key('<Control-Up>', lambda: editor._nudge_node(0, 0.1))
        _bind_two_d_ctrl_key('<Control-Down>', lambda: editor._nudge_node(0, -0.1))

        def _toggle_radius_if_draw():
            if editor.mode.get() == 'draw':
                editor._toggle_draw_radius_brush()
        _bind_two_d_key('<KeyPress-r>', _toggle_radius_if_draw)
        _bind_two_d_key('<KeyPress-R>', _toggle_radius_if_draw)
        _bind_two_d_ctrl_key('<Control-r>', _rebuild_selected_neighbors)
        _bind_two_d_ctrl_key('<Control-R>', _rebuild_selected_neighbors)

        editor.bind('<Delete>',     lambda e: editor._delete_selected_zone()
                              if editor.mode.get() == 'zone'
                              else editor._delete_selected_node())
        editor.bind('<Escape>',     editor._on_escape_key)
        editor.bind('<Control-z>',  lambda e: editor.undo())
        editor.bind('<Control-c>',  editor._on_copy_shortcut)
        editor.bind('<Control-C>',  editor._on_copy_shortcut)
        editor.bind('<Control-v>',  editor._on_paste_shortcut)
        editor.bind('<Control-V>',  editor._on_paste_shortcut)
        editor.bind('<Control-Z>',  lambda e: editor.undo())
        def _help_key(e):
            # Only fire if no text entry widget has focus
            fw = editor.focus_get()
            if isinstance(fw, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
                return  # let the key type normally
            editor._toggle_help_panel()
        editor.bind('<h>', _help_key)
        editor.bind('<H>', _help_key)
        editor.bind('<Control-y>',  lambda e: editor.redo())
        editor.bind('<Control-Y>',  lambda e: editor.redo())
        editor.bind('<F9>',         lambda e: editor._toggle_debug_bar() if editor.dev_mode.get() else None)
        editor.bind('<F8>',         lambda e: editor._open_zone_layer_manager())
        editor.bind('<Left>',       lambda e: editor._nudge_node(-1, 0))
        editor.bind('<Right>',      lambda e: editor._nudge_node( 1, 0))
        editor.bind('<Up>',         lambda e: editor._nudge_node( 0, 1))
        editor.bind('<Down>',       lambda e: editor._nudge_node( 0,-1))
        editor.bind('<Shift-Left>',  lambda e: editor._nudge_node(-0.1, 0))
        editor.bind('<Shift-Right>', lambda e: editor._nudge_node( 0.1, 0))
        editor.bind('<Shift-Up>',    lambda e: editor._nudge_node( 0, 0.1))
        editor.bind('<Shift-Down>',  lambda e: editor._nudge_node( 0,-0.1))
        _NUMPAD_KEYCODES = {96,97,98,99,100,101,102,103,104,105}
        def _numpad_only(fn):
            def _guard(e):
                if getattr(editor, '_view_mode', '2d') != '2d':
                    return
                if e.keycode not in _NUMPAD_KEYCODES:
                    return
                if isinstance(editor.focus_get(), (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
                    return
                fn()
            return _guard
        # Both Shift+= and Windows keypad + report the plus keysym.
        editor.bind('<plus>',   lambda e: editor._grid_step_up())
        editor.bind('<minus>',  lambda e: editor._grid_step_down())
        editor.bind('<KeyPress-9>',  _numpad_only(lambda: editor._zoom_center(1.2)))
        editor.bind('<KeyPress-3>',  _numpad_only(lambda: editor._zoom_center(1/1.2)))
        editor.bind('<KeyPress-8>',  _numpad_only(lambda: editor._pan_grid(0, 1)))
        editor.bind('<KeyPress-2>',  _numpad_only(lambda: editor._pan_grid(0, -1)))
        editor.bind('<KeyPress-4>',  _numpad_only(lambda: editor._pan_grid(-1, 0)))
        editor.bind('<KeyPress-6>',  _numpad_only(lambda: editor._pan_grid(1, 0)))
        # Shift+keypad directions move the active node, rather than panning.
        editor.bind('<Shift-KeyPress-8>', _numpad_only(lambda: editor._nudge_node(0, 0.1)))
        editor.bind('<Shift-KeyPress-2>', _numpad_only(lambda: editor._nudge_node(0, -0.1)))
        editor.bind('<Shift-KeyPress-4>', _numpad_only(lambda: editor._nudge_node(-0.1, 0)))
        editor.bind('<Shift-KeyPress-6>', _numpad_only(lambda: editor._nudge_node(0.1, 0)))
