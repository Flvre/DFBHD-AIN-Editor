"""Small standalone Tk dialogs used by the AIN editor.

The dialog receives the editor state/callback adapter explicitly and does not
import the application class.
"""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from tkinter import colorchooser

from config.editor_config import _load_editor_cfg, _save_editor_cfg
from config.editor_config import _configured_additional_pff_dirs, _save_additional_pff_dirs
from config.ui_theme import (C_ACCENT, C_BORDER, C_DIM, C_ENTITY, C_GREEN,
    C_PANEL, C_PANEL2, C_TEXT)

def open_entity_color_dialog(editor):
    """Open the left-side color dialog for entity and foliage outlines."""
    from tkinter import colorchooser

    cfg = _load_editor_cfg()
    dark_col  = cfg.get('entity_color_dark',  C_ENTITY)
    light_col = cfg.get('entity_color_light', '#464646')
    foliage_dark_col = cfg.get('foliage_color_dark', '#3cb43c')
    foliage_light_col = cfg.get('foliage_color_light', '#2d782d')

    try:
        pal = editor._ui_theme()
    except Exception:
        pal = {
            'popup_bg': C_PANEL, 'text': C_TEXT, 'text_dim': C_DIM,
            'selection_bg': C_ACCENT, 'button_bg': C_PANEL2,
            'button_fg': C_TEXT, 'button_active_bg': C_BORDER,
            'button_active_fg': C_TEXT,
        }
    bg = pal.get('popup_bg', C_PANEL)
    fg = pal.get('text', C_TEXT)
    dim = pal.get('text_dim', C_DIM)
    accent = pal.get('selection_bg', C_ACCENT)

    win = tk.Toplevel(editor)
    win.withdraw()
    win.title("Entity Colors")
    win.resizable(False, False)
    win.configure(bg=bg)
    win.transient(editor)

    dark_var  = [dark_col]
    light_var = [light_col]
    foliage_dark_var = [foliage_dark_col]
    foliage_light_var = [foliage_light_col]

    def _make_row(parent, label_text, current_color, color_var):
        row = tk.Frame(parent, bg=bg)
        row.pack(fill='x', padx=10, pady=4)
        tk.Label(row, text=label_text, bg=bg, fg=fg,
                 font=('Consolas', 9), width=19, anchor='w').pack(side='left')
        swatch = tk.Label(row, text='  ', bg=current_color, width=4,
                          relief='solid', bd=1)
        swatch.pack(side='left', padx=(4, 8))
        hex_label = tk.Label(row, text=current_color, bg=bg, fg=dim,
                             font=('Consolas', 8), width=8, anchor='w')
        hex_label.pack(side='left')

        def _pick():
            result = colorchooser.askcolor(
                color=color_var[0], title=f"Pick {label_text}",
                parent=win)
            if result and result[1]:
                color_var[0] = result[1]
                swatch.configure(bg=result[1])
                hex_label.configure(text=result[1])
                _apply_live()

        tk.Button(row, text="Pick", command=_pick,
                  bg=accent, fg='white', font=('Consolas', 8),
                  relief='flat', padx=6).pack(side='right')
        return swatch, hex_label

    tk.Label(win, text="Entity and Foliage Outline Colors", bg=bg, fg=accent,
             font=('Consolas', 10, 'bold')).pack(pady=(10, 4))

    dark_swatch, dark_hex = _make_row(win, "Dark canvas:", dark_col, dark_var)
    light_swatch, light_hex = _make_row(win, "Light canvas:", light_col, light_var)
    foliage_dark_swatch, foliage_dark_hex = _make_row(
        win, "Foliage (dark):", foliage_dark_col, foliage_dark_var)
    foliage_light_swatch, foliage_light_hex = _make_row(
        win, "Foliage (light):", foliage_light_col, foliage_light_var)

    def _apply_live():
        cfg_save = _load_editor_cfg()
        cfg_save['entity_color_dark']  = dark_var[0]
        cfg_save['entity_color_light'] = light_var[0]
        cfg_save['foliage_color_dark'] = foliage_dark_var[0]
        cfg_save['foliage_color_light'] = foliage_light_var[0]
        _save_editor_cfg(cfg_save)
        editor._apply_entity_custom_colors()

    def _reset():
        dark_var[0]  = C_ENTITY
        light_var[0] = '#464646'
        dark_swatch.configure(bg=dark_var[0])
        dark_hex.configure(text=dark_var[0])
        light_swatch.configure(bg=light_var[0])
        light_hex.configure(text=light_var[0])
        foliage_dark_var[0] = '#3cb43c'
        foliage_light_var[0] = '#2d782d'
        foliage_dark_swatch.configure(bg=foliage_dark_var[0])
        foliage_dark_hex.configure(text=foliage_dark_var[0])
        foliage_light_swatch.configure(bg=foliage_light_var[0])
        foliage_light_hex.configure(text=foliage_light_var[0])
        _apply_live()

    btn_row = tk.Frame(win, bg=bg)
    btn_row.pack(fill='x', padx=10, pady=(8, 10))
    tk.Button(btn_row, text="Reset defaults", command=_reset,
              bg='#664400', fg='white', font=('Consolas', 8),
              relief='flat', padx=8).pack(side='left')
    def _close():
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    tk.Button(btn_row, text="Close", command=_close,
              bg=pal.get('button_bg', C_PANEL2),
              fg=pal.get('button_fg', C_TEXT), font=('Consolas', 8),
              relief='flat', padx=8).pack(side='right')

    win.protocol('WM_DELETE_WINDOW', _close)
    try:
        editor.update_idletasks()
        win.update_idletasks()
        width = max(380, win.winfo_reqwidth())
        height = win.winfo_reqheight()
        px, py = editor.winfo_rootx(), editor.winfo_rooty()
        x = px + 18
        y = py + 18
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = max(0, min(x, max(0, sw - width)))
        y = max(0, min(y, max(0, sh - height)))
        win.geometry(f'{width}x{height}+{x}+{y}')
        win.deiconify()
        win.grab_set()
        win.lift(editor.winfo_toplevel())
        win.focus_force()
    except Exception:
        try:
            win.deiconify()
            win.grab_set()
        except Exception:
            pass


def show_node_help(editor):
    """Show the Node Inspector help floating panel."""
    if getattr(editor, '_node_help_win', None) is not None:
        try:
            if editor._node_help_win.winfo_exists():
                editor._set_owned_popup_window(editor._node_help_win)
                editor._sync_node_help_window_theme()
                editor._node_help_win.lift(editor.winfo_toplevel())
                return
        except Exception:
            pass

    pal = editor._node_help_popup_palette()
    win = tk.Toplevel(editor)
    try:
        win.withdraw()
    except Exception:
        pass
    win.title("Node Inspector — Help")
    win.resizable(True, True)
    win.minsize(400, 300)
    win.configure(bg=pal['bg'])
    editor._node_help_win = win
    editor._set_owned_popup_window(win)

    def _close_node_help():
        try:
            win.destroy()
        except Exception:
            pass
        editor._node_help_win = None

    win.protocol('WM_DELETE_WINDOW', _close_node_help)
    win.bind('<Escape>', lambda e: (_close_node_help(), 'break'))

    # Resize grip bottom-right
    tk.ttk.Sizegrip(win).pack(side='right', anchor='se')

    # Scrollable text area
    frame = tk.Frame(win, bg=pal['bg'])
    frame.pack(fill='both', expand=True, padx=(8,0), pady=8)
    editor._node_help_body = frame
    sb = tk.Scrollbar(frame)
    sb.pack(side='right', fill='y')
    editor._node_help_scrollbar = sb
    txt = tk.Text(frame, yscrollcommand=sb.set, wrap='word',
                  bg=pal['text_bg'], fg=pal['fg'], font=('Consolas',10),
                  relief='flat', padx=8, pady=8, width=60, height=30,
                  cursor='arrow', state='normal')
    txt.pack(side='left', fill='both', expand=True)
    sb.config(command=txt.yview)
    editor._node_help_text = txt

    # Define tags
    txt.tag_config('green',  foreground=pal['green'], font=('Consolas',10,'bold'))
    txt.tag_config('yellow', foreground=pal['yellow'], font=('Consolas',10,'bold'))
    txt.tag_config('ph',     foreground=pal['ph'], font=('Consolas',10,'italic'))
    txt.tag_config('dim',    foreground=pal['dim'], font=('Consolas',9))
    txt.tag_config('normal', foreground=pal['fg'], font=('Consolas',10))

    def w(text, tag='normal'):
        txt.insert('end', text, tag)

    editor._populate_node_help_text()
    editor._sync_node_help_window_theme()

    # Center before showing so Windows does not flash it at top-left first.
    try:
        editor._center_popup_over_editor(win, 500, 520)
    except Exception:
        pass
    try:
        win.deiconify()
    except Exception:
        pass
    try:
        win.lift(editor.winfo_toplevel())
    except Exception:
        pass


def open_additional_pff_folders_dialog(editor):
    """Configure external folders whose PFF archives extend the game stack."""
    win = tk.Toplevel(editor)
    try:
        win.withdraw()
    except Exception:
        pass
    win.title("Additional PFF Folders")
    win.geometry("760x390")
    try:
        editor._set_owned_popup_window(win)
    except Exception:
        pass

    title_label = tk.Label(
        win, text="Additional PFF Folders",
        font=('Consolas', 11, 'bold'), anchor='w')
    title_label.pack(fill='x', padx=12, pady=(12, 3))

    desc_label = tk.Label(
        win,
        text=("The DFBHD game/EXE folder remains the base resource path. "
              "Folders listed here add external .pff archives without copying them "
              "into the game directory. Later folders have higher override priority."),
        font=('Consolas', 10),
        justify='left', anchor='w', wraplength=730)
    desc_label.pack(fill='x', padx=12, pady=(0, 8))

    list_frame = tk.Frame(win)
    list_frame.pack(fill='both', expand=True, padx=12, pady=(0, 8))
    sb = tk.Scrollbar(list_frame)
    sb.pack(side='right', fill='y')
    lb = tk.Listbox(
        list_frame,
        font=('Consolas', 10),
        yscrollcommand=sb.set, activestyle='none',
        relief='solid', bd=1)
    lb.pack(side='left', fill='both', expand=True)
    sb.config(command=lb.yview)

    working = [str(p) for p in _configured_additional_pff_dirs()]

    def refresh(select=None):
        lb.delete(0, 'end')
        for i, value in enumerate(working, 1):
            p = Path(value)
            status = 'OK' if p.is_dir() else 'MISSING'
            try:
                pff_count = len(list(p.glob('*.pff'))) if p.is_dir() else 0
                pff_sub = p / 'pff'
                if pff_sub.is_dir():
                    pff_count += len(list(pff_sub.glob('*.pff')))
            except Exception:
                pff_count = 0
            lb.insert('end', f"{i:02d}. [{status}] {value}   ({pff_count} PFF)")
        if select is not None and working:
            select = max(0, min(int(select), len(working) - 1))
            lb.selection_set(select)
            lb.see(select)

    def selected_index():
        sel = lb.curselection()
        return int(sel[0]) if sel else None

    def add_folder():
        folder = filedialog.askdirectory(
            parent=win, title="Select folder containing additional PFF files")
        if not folder:
            return
        try:
            key = str(Path(folder).resolve()).lower()
        except Exception:
            key = str(Path(folder)).lower()
        for existing in working:
            try:
                ek = str(Path(existing).resolve()).lower()
            except Exception:
                ek = str(Path(existing)).lower()
            if ek == key:
                return
        working.append(folder)
        refresh(len(working) - 1)

    def remove_folder():
        idx = selected_index()
        if idx is None:
            return
        del working[idx]
        refresh(min(idx, len(working) - 1) if working else None)

    def move(delta):
        idx = selected_index()
        if idx is None:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(working):
            return
        working[idx], working[new_idx] = working[new_idx], working[idx]
        refresh(new_idx)

    def apply_changes(close=False):
        saved = _save_additional_pff_dirs(working)
        working[:] = saved
        editor._invalidate_pff_resource_context(reparse_bms=True)
        editor.status(f"Additional PFF folders updated: {len(saved)} configured")
        refresh()
        if close:
            win.destroy()

    refresh()

    buttons = tk.Frame(win)
    buttons.pack(fill='x', padx=12, pady=(0, 10))

    btn_add = tk.Button(
        buttons, text="Add Folder...", command=add_folder,
        relief='flat', font=('Consolas', 10), padx=9, pady=3)
    btn_add.pack(side='left', padx=(0, 5))

    btn_remove = tk.Button(
        buttons, text="Remove", command=remove_folder,
        relief='flat', font=('Consolas', 10), padx=9, pady=3)
    btn_remove.pack(side='left', padx=(0, 5))

    btn_up = tk.Button(
        buttons, text="Move Up", command=lambda: move(-1),
        relief='flat', font=('Consolas', 10), padx=9, pady=3)
    btn_up.pack(side='left', padx=(0, 5))

    btn_down = tk.Button(
        buttons, text="Move Down", command=lambda: move(1),
        relief='flat', font=('Consolas', 10), padx=9, pady=3)
    btn_down.pack(side='left', padx=(0, 5))

    btn_apply = tk.Button(
        buttons, text="Apply", command=lambda: apply_changes(False),
        relief='flat', font=('Consolas', 10), padx=10, pady=3)
    btn_apply.pack(side='right', padx=(5, 0))

    btn_apply_close = tk.Button(
        buttons, text="Apply & Close", command=lambda: apply_changes(True),
        relief='flat', font=('Consolas', 10), padx=10, pady=3)
    btn_apply_close.pack(side='right')

    theme_widgets = {
        'containers': [win, list_frame, buttons],
        'labels': [title_label, desc_label],
        'buttons': [btn_add, btn_remove, btn_up, btn_down, btn_apply, btn_apply_close],
    }

    def apply_theme(*_args):
        """Re-theme this open popup immediately when editor mode changes."""
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return

        light = bool(editor.light_panels.get())
        bg = '#f7f7f7' if light else '#303030'
        fg = '#111111' if light else '#f2f2f2'
        dim = '#555555' if light else '#d0d0d0'
        list_bg = '#ffffff' if light else '#101010'
        list_fg = '#111111' if light else '#f2f2f2'
        btn_bg = '#e9e9e9' if light else '#444444'
        btn_fg = '#111111' if light else '#f2f2f2'
        active_bg = '#d9e7f7' if light else '#315f88'
        active_fg = '#111111' if light else '#ffffff'

        for w in theme_widgets['containers']:
            try:
                w.configure(bg=bg)
            except Exception:
                pass

        try:
            title_label.configure(bg=bg, fg=fg)
            desc_label.configure(bg=bg, fg=dim)
        except Exception:
            pass

        try:
            lb.configure(
                bg=list_bg, fg=list_fg,
                selectbackground=active_bg,
                selectforeground=active_fg)
        except Exception:
            pass

        for b in theme_widgets['buttons']:
            try:
                b.configure(
                    bg=btn_bg, fg=btn_fg,
                    activebackground=active_bg,
                    activeforeground=active_fg)
            except Exception:
                pass

    apply_theme()

    try:
        _theme_trace_id = editor.light_panels.trace_add('write', apply_theme)
    except Exception:
        _theme_trace_id = None

    def _close():
        if _theme_trace_id is not None:
            try:
                editor.light_panels.trace_remove('write', _theme_trace_id)
            except Exception:
                pass
        try:
            win.destroy()
        except Exception:
            pass

    try:
        win.protocol("WM_DELETE_WINDOW", _close)
    except Exception:
        pass

    btn_apply_close.configure(
        command=lambda: (apply_changes(False), _close()))

    try:
        editor._center_popup_over_editor(win, 760, 390)
    except Exception:
        try:
            win.update_idletasks()
            w, h = win.winfo_width(), win.winfo_height()
            x = editor.winfo_rootx() + max(0, (editor.winfo_width() - w) // 2)
            y = editor.winfo_rooty() + max(0, (editor.winfo_height() - h) // 2)
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    try:
        win.deiconify()
        win.lift(editor.winfo_toplevel())
    except Exception:
        pass


def show_cleanup_hud(editor):
    """Small in-editor HUD for Cleanup mode.  It lives over the canvas."""
    try:
        old = getattr(editor, '_cleanup_hud', None)
        if old is not None and old.winfo_exists():
            old.destroy()
    except Exception:
        pass

    parent = getattr(editor, '_canvas_frame', None) or editor
    # The AIN Pass HUD is UI chrome, so it follows the panels/UI theme
    # rather than the canvas theme.  This keeps it light when the editor
    # panels are light even if the map canvas itself remains dark.
    light_panels = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
    bg = '#f7f7f7' if light_panels else '#050505'
    fg = '#111111' if light_panels else '#f0f0f0'
    dim = '#555555' if light_panels else '#bdbdbd'
    border = '#808080' if light_panels else '#404040'
    title_bg = '#ccd3ff' if light_panels else '#202a55'
    title_fg = '#111111' if light_panels else '#dfe8ff'
    btn_bg = '#e5e5e5' if light_panels else '#202020'
    btn_fg = '#111111' if light_panels else '#f0f0f0'
    blue_bg = '#c9dcf4' if light_panels else '#1f6fb2'
    blue_fg = '#003366' if light_panels else '#ffffff'
    red_bg = '#ffe1e1' if light_panels else '#400000'
    red_fg = '#cc0000' if light_panels else '#ff6666'

    hud = tk.Frame(parent, bg=bg, bd=0, highlightthickness=1, highlightbackground=border)
    editor._cleanup_hud = hud
    hud.place(x=0, y=0, width=230)
    hud.lift()

    title = tk.Frame(hud, bg=title_bg)
    title.pack(fill='x')
    tk.Label(title, text='Cleanup', bg=title_bg, fg=title_fg,
             font=('Consolas', 10, 'bold'), anchor='w').pack(side='left', padx=6, pady=3)
    tk.Button(title, text='X', command=editor._exit_ain_pass_mode,
              bg=red_bg, fg=red_fg, activebackground=red_bg, activeforeground=red_fg,
              font=('Consolas', 10, 'bold'), relief='flat', padx=6, pady=0).pack(side='right', padx=3, pady=2)

    body = tk.Frame(hud, bg=bg)
    body.pack(fill='both', expand=True, padx=7, pady=7)
    tk.Label(body, text='1. Select region\n   Shift + LMB', bg=bg, fg=fg,
             font=('Consolas', 9, 'bold'), justify='left', anchor='w').pack(fill='x')

    tk.Frame(body, bg=border, height=1).pack(fill='x', pady=(6, 5))
    tk.Label(body, text='Auto clean selected region', bg=bg, fg=fg,
             font=('Consolas', 9, 'bold'), anchor='w').pack(fill='x')

    row1 = tk.Frame(body, bg=bg)
    row1.pack(fill='x', pady=(3, 1))
    tk.Label(row1, text='Delete islands <=', bg=bg, fg=dim, font=('Consolas', 9)).pack(side='left')
    tk.Entry(row1, textvariable=editor._cleanup_ratio_var, width=4,
             bg=('#ffffff' if light_panels else '#111111'), fg=fg,
             insertbackground=fg, relief='solid', bd=1,
             font=('Consolas', 9)).pack(side='left', padx=3)
    tk.Label(row1, text='% of protected graph', bg=bg, fg=dim, font=('Consolas', 9)).pack(side='left')

    row2 = tk.Frame(body, bg=bg)
    row2.pack(fill='x', pady=(1, 4))
    tk.Label(row2, text='Tiny islands <=', bg=bg, fg=dim, font=('Consolas', 9)).pack(side='left')
    tk.Entry(row2, textvariable=editor._cleanup_tiny_var, width=4,
             bg=('#ffffff' if light_panels else '#111111'), fg=fg,
             insertbackground=fg, relief='solid', bd=1,
             font=('Consolas', 9)).pack(side='left', padx=3)
    tk.Label(row2, text='nodes', bg=bg, fg=dim, font=('Consolas', 9)).pack(side='left')

    tk.Button(body, text='Clean graph', command=editor._cleanup_clean_selected_region,
              bg=blue_bg, fg=blue_fg, activebackground=blue_bg, activeforeground=blue_fg,
              font=('Consolas', 9, 'bold'), relief='flat', padx=8, pady=2).pack(fill='x', pady=(2, 6))

    tk.Frame(body, bg=border, height=1).pack(fill='x', pady=(2, 5))
    tk.Label(body, text='Picked clean', bg=bg, fg=fg,
             font=('Consolas', 9, 'bold'), anchor='w').pack(fill='x')
    tk.Label(body, text='Select nodes from 2 components.', bg=bg, fg=dim,
             font=('Consolas', 9), anchor='w').pack(fill='x', pady=(2, 4))
    tk.Button(body, text='Delete smaller picked', command=editor._cleanup_delete_smaller_picked_component,
              bg=btn_bg, fg=btn_fg, activebackground=border, activeforeground=fg,
              font=('Consolas', 8, 'bold'), relief='solid', bd=1, padx=8, pady=2).pack(fill='x')

    tk.Label(body, text='Ctrl+Z restores deleted nodes.', bg=bg, fg=dim,
             font=('Consolas', 9), anchor='w').pack(fill='x', pady=(7, 0))


def open_zone_layer_manager(editor):
    """Open the Zone Layer Manager popup (F8 / View menu)."""
    if hasattr(editor, '_zone_layer_win') and editor._zone_layer_win and                 editor._zone_layer_win.winfo_exists():
        editor._zone_layer_win.lift()
        return

    win = tk.Toplevel(editor)
    win.title("Zone Layers")

    # Follow the editor panel theme. These local colours were referenced
    # below but never initialized, which caused the popup to crash as soon
    # as it was opened.
    light = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
    if light:
        bg = '#f3f3f3'
        panel2 = '#ffffff'
        fg = '#000000'
        dim = '#404040'
        border = '#c7c7c7'
        select_bg = '#2d7dd2'
    else:
        bg = C_PANEL
        panel2 = C_PANEL2
        fg = C_TEXT
        dim = C_DIM
        border = C_BORDER
        select_bg = C_ACCENT

    win.configure(bg=bg)
    win.geometry("370x520")
    win.resizable(True, True)
    editor._zone_layer_win = win

    # ── Header ────────────────────────────────────────────────────────
    tk.Label(win, text="Zone Layer Manager",
             bg=bg, fg='#44aaff', font=('Consolas',10,'bold'),
             anchor='w').pack(fill='x', padx=10, pady=(10,2))
    tk.Label(win,
             text="Active = fully interactive   Locked = visible but non-interactive   Hidden = invisible",
             bg=bg, fg=dim, font=('Consolas',8),
             justify='left', anchor='w').pack(fill='x', padx=12, pady=(0,6))

    tk.Frame(win, bg=border, height=1).pack(fill='x', padx=10, pady=3)

    # ── Quick action buttons ───────────────────────────────────────────
    btn_row = tk.Frame(win, bg=bg)
    btn_row.pack(fill='x', padx=10, pady=4)

    def _btn(parent, text, cmd, fg='#aaaaaa'):
        tk.Button(parent, text=text, command=cmd,
                  bg=panel2, fg=fg, font=('Consolas',8),
                  relief='flat', padx=6, pady=2).pack(side='left', padx=2)

    def _activate_all():
        editor._zone_layer_state.clear()
        editor._locked_zone_ids.clear()
        editor._hidden_zone_ids.clear()
        _refresh()
        editor.redraw()
        editor.status("All zones activated")

    def _lock_all():
        for zid in _all_zone_ids():
            editor._zone_layer_state[zid] = 'locked'
        editor._locked_zone_ids = set(_all_zone_ids())
        editor._hidden_zone_ids.clear()
        _refresh()
        editor.redraw()

    def _hide_all():
        for zid in _all_zone_ids():
            editor._zone_layer_state[zid] = 'hidden'
        editor._hidden_zone_ids = set(_all_zone_ids())
        editor._locked_zone_ids.clear()
        _refresh()
        editor.redraw()

    def _activate_selected_only():
        sel = listbox.curselection()
        if not sel:
            editor.status("Select zones to activate exclusively")
            return
        active_ids = set()
        for i in sel:
            zid = _zone_id_from_row(i)
            if zid is not None:
                active_ids.add(zid)
        for zid in _all_zone_ids():
            if zid in active_ids:
                editor._zone_layer_state.pop(zid, None)
                editor._locked_zone_ids.discard(zid)
                editor._hidden_zone_ids.discard(zid)
            else:
                editor._zone_layer_state[zid] = 'locked'
                editor._locked_zone_ids.add(zid)
                editor._hidden_zone_ids.discard(zid)
        _refresh()
        editor.redraw()

    _btn(btn_row, "Activate All",     _activate_all,          '#44ff88')
    _btn(btn_row, "Lock All",         _lock_all,              '#ffaa44')
    _btn(btn_row, "Hide All",         _hide_all,              '#ff4444')
    _btn(btn_row, "Active Only (sel)",_activate_selected_only,'#44aaff')

    tk.Frame(win, bg=border, height=1).pack(fill='x', padx=10, pady=6)

    # ── Zone list ─────────────────────────────────────────────────────
    tk.Label(win, text="Zones  (click row to jump to zone, Ctrl+click = multi-select):",
             bg=bg, fg=fg, font=('Consolas',8),
             anchor='w').pack(fill='x', padx=10, pady=(0,2))

    list_outer = tk.Frame(win, bg=panel2, relief='sunken', bd=1)
    list_outer.pack(fill='both', expand=True, padx=10, pady=(0,4))

    sb = tk.Scrollbar(list_outer)
    sb.pack(side='right', fill='y')
    listbox = tk.Listbox(list_outer, bg=panel2, fg=fg,
                         selectbackground=select_bg, selectforeground='white',
                         font=('Consolas',9), bd=0, highlightthickness=0,
                         selectmode='extended', yscrollcommand=sb.set)
    listbox.pack(fill='both', expand=True, padx=2, pady=2)
    sb.config(command=listbox.yview)

    # ── State buttons for selected rows ───────────────────────────────
    state_row = tk.Frame(win, bg=bg)
    state_row.pack(fill='x', padx=10, pady=(0,6))

    tk.Label(state_row, text="Set selected to:",
             bg=bg, fg=dim, font=('Consolas',8)).pack(side='left', padx=(0,6))

    def _set_selected_state(state):
        sel = listbox.curselection()
        if not sel:
            return
        for i in sel:
            zid = _zone_id_from_row(i)
            if zid is None:
                continue
            if state == 'active':
                editor._zone_layer_state.pop(zid, None)
                editor._locked_zone_ids.discard(zid)
                editor._hidden_zone_ids.discard(zid)
            elif state == 'locked':
                editor._zone_layer_state[zid] = 'locked'
                editor._locked_zone_ids.add(zid)
                editor._hidden_zone_ids.discard(zid)
            elif state == 'hidden':
                editor._zone_layer_state[zid] = 'hidden'
                editor._hidden_zone_ids.add(zid)
                editor._locked_zone_ids.discard(zid)
        _refresh()
        editor.redraw()
        # Update status bar indicator
        _update_status_indicator()

    for label, state, fg in [
        ("Active", 'active', '#44ff88'),
        ("Locked", 'locked', '#ffaa44'),
        ("Hidden", 'hidden', '#ff4444'),
    ]:
        tk.Button(state_row, text=label,
                  command=lambda s=state: _set_selected_state(s),
                  bg=panel2, fg=fg, font=('Consolas',8),
                  relief='flat', padx=8, pady=2).pack(side='left', padx=2)

    # ── Internal helpers ───────────────────────────────────────────────
    _row_zone_ids = []  # parallel list: row index -> b14 zone id

    def _all_zone_ids():
        seen = set()
        for n in editor.nodes:
            seen.add(n.b14)
        return sorted(seen)

    def _zone_id_from_row(row_idx):
        if 0 <= row_idx < len(_row_zone_ids):
            return _row_zone_ids[row_idx]
        return None

    def _state_label(zid):
        s = editor._zone_layer_state.get(zid, 'active')
        if s == 'locked': return 'Locked'
        if s == 'hidden': return 'Hidden'
        return 'Active'

    def _state_color(zid):
        s = editor._zone_layer_state.get(zid, 'active')
        if s == 'locked': return '#ffaa44'
        if s == 'hidden': return '#ff4444'
        return '#44ff88'

    def _node_count(zid):
        return sum(1 for n in editor.nodes if n.b14 == zid)

    def _refresh():
        listbox.delete(0, 'end')
        _row_zone_ids.clear()
        for zid in _all_zone_ids():
            count = _node_count(zid)
            slbl  = _state_label(zid)
            line  = f"  Zone {zid:3d}   {count:5d} nodes   [{slbl}]"
            listbox.insert('end', line)
            listbox.itemconfig('end', fg=_state_color(zid))
            _row_zone_ids.append(zid)

    def _update_status_indicator():
        n_locked = len(editor._locked_zone_ids)
        n_hidden = len(editor._hidden_zone_ids)
        if n_locked or n_hidden:
            editor.status(f"Layers active — {n_locked} locked, {n_hidden} hidden")

    # Jump camera to zone centroid on double-click
    def _on_double_click(event):
        sel = listbox.curselection()
        if not sel:
            return
        zid = _zone_id_from_row(sel[0])
        if zid is None:
            return
        zone_nodes = [n for n in editor.nodes if n.b14 == zid]
        if not zone_nodes:
            return
        cx = sum(n.x for n in zone_nodes) / len(zone_nodes)
        cy = sum(n.y for n in zone_nodes) / len(zone_nodes)
        editor.vp_offset_x = cx
        editor.vp_offset_y = cy
        editor.redraw()
        editor.status(f"Jumped to Zone {zid} centroid ({cx:.1f}, {cy:.1f})")

    listbox.bind('<Double-Button-1>', _on_double_click)

    _refresh()
    _update_status_indicator()
    win.protocol("WM_DELETE_WINDOW", win.destroy)
