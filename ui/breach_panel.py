"""Takedown/breach-zone management panel for the AIN editor."""

import tkinter as tk

from config.ui_theme import C_ACCENT, C_BORDER, C_PANEL, C_PANEL2

def sync_breach_panel_mode(editor):
    """Switch Takedown Zone help between creator wording and raw Developer details."""
    try:
        dev = bool(editor.dev_mode.get())
    except Exception:
        dev = False

    if dev:
        summary = (
            "Manual = player triggers (0x02)   Auto = AI clears automatically (0x01)   "
            "Both = 0x03"
        )
        requirements = (
            "Manual: player triggers takedown prompt (b17 nodes)\n"
            "Auto:   AI clears room automatically on entry\n"
            "Both:   both systems active on this zone\n\n"
            "Nodes need: b14=zone (action room)  b17=zone (trigger)\n"
            "            b13=1 dead (no runstate)  b13=4 unknown (TS 1.5.0: 0x004890F0)\n"
            "            b13=6 assault  b13=7 unknown (TS 1.5.0: 0x0041AD24 / 0x004890F0)\n"
            "            b13=8 grenade target  b13=9 marker"
        )
    else:
        summary = (
            "Manual = player-triggered takedown   Auto = AI clears automatically   "
            "Both = both systems active"
        )
        requirements = (
            "Manual: player triggers takedown prompt (Takedown (var2) nodes)\n"
            "Auto:   AI clears room automatically on entry\n"
            "Both:   both systems active on this zone\n\n"
            "Nodes need:\n"
            "  Takedown Zone = zone (action room)\n"
            "  Takedown (var2) = zone (trigger)\n"
            "  Takedown (var1): 6 assault / 8 grenade target / 9 marker"
        )

    try:
        label = getattr(editor, '_breach_mode_summary_label', None)
        if label is not None and label.winfo_exists():
            label.configure(text=summary)
    except Exception:
        pass
    try:
        label = getattr(editor, '_breach_requirements_label', None)
        if label is not None and label.winfo_exists():
            label.configure(text=requirements)
    except Exception:
        pass
    try:
        refresh = getattr(editor, '_breach_refresh_list', None)
        if callable(refresh):
            refresh()
    except Exception:
        pass

def open_breach_panel(editor):
    """Open the Takedown Zones management panel."""
    if hasattr(editor, '_breach_win') and editor._breach_win and editor._breach_win.winfo_exists():
        editor._breach_win.lift()
        return

    win = tk.Toplevel(editor)
    # Keep the popup hidden until it has been sized and centered, so it
    # does not briefly flash at Tk's default top-left position.
    try:
        win.withdraw()
    except Exception:
        pass
    win.title("Takedown Zones")
    win.resizable(False, False)
    try:
        editor._set_owned_popup_window(win)
    except Exception:
        pass
    editor._breach_win = win

    import struct as _struct

    light = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
    if light:
        bg = '#f3f3f3'
        panel_bg = '#ffffff'
        panel2 = '#ffffff'
        fg = '#000000'
        dim = '#303030'
        border = '#c7c7c7'
        title_fg = '#c8382b'
        remove_bg = '#f4d6d6'
        remove_fg = '#7e1111'
        add_bg = '#dcefdc'
        add_fg = '#0d5f1a'
        button_bg = '#efefef'
        button_fg = '#000000'
        entry_bg = '#ffffff'
        select_bg = '#2d7dd2'
        menu_bg = '#ffffff'
        menu_fg = '#000000'
        list_relief = 'solid'
        list_bd = 1
        info_fg = '#404040'
        small_font = ('Consolas', 9)
        body_font = ('Consolas', 10)
        label_font = ('Consolas', 10, 'bold')
        list_font = ('Consolas', 10)
        title_font = ('Consolas', 11, 'bold')
        legend_manual = '#ff3333'
        legend_auto = '#c87400'
        legend_both = '#b89700'
    else:
        bg = C_PANEL
        panel_bg = C_PANEL2
        panel2 = C_PANEL2
        fg = '#f0f0f0'
        dim = '#d6d6d6'
        border = C_BORDER
        title_fg = '#ff6666'
        remove_bg = '#550000'
        remove_fg = '#ffd6d6'
        add_bg = '#003300'
        add_fg = '#66ff88'
        button_bg = C_PANEL2
        button_fg = '#f0f0f0'
        entry_bg = '#101010'
        select_bg = C_ACCENT
        menu_bg = C_PANEL2
        menu_fg = '#f0f0f0'
        list_relief = 'sunken'
        list_bd = 1
        info_fg = '#d0d0d0'
        small_font = ('Consolas', 8)
        body_font = ('Consolas', 9)
        label_font = ('Consolas', 9, 'bold')
        list_font = ('Consolas', 10)
        title_font = ('Consolas', 11, 'bold')
        legend_manual = '#ff4444'
        legend_auto = '#ff9900'
        legend_both = '#ffd200'

    win.configure(bg=bg)

    # ── Header ────────────────────────────────────────────────────────
    tk.Label(
        win,
        text="Takedown Zone Manager",
        bg=bg,
        fg=title_fg,
        font=title_font,
        anchor='w'
    ).pack(fill='x', padx=12, pady=(12, 3))

    editor._breach_mode_summary_label = tk.Label(
        win,
        text="",
        bg=bg,
        fg=info_fg,
        font=small_font,
        justify='left',
        anchor='w',
        wraplength=414
    )
    editor._breach_mode_summary_label.pack(fill='x', padx=12, pady=(0, 6))

    # ── Legend ────────────────────────────────────────────────────────
    leg = tk.Frame(win, bg=bg)
    leg.pack(fill='x', padx=12, pady=(0, 4))
    for label, color in [("■ Manual", legend_manual), ("■ Auto", legend_auto), ("■ Both", legend_both)]:
        tk.Label(leg, text=label, bg=bg, fg=color, font=label_font).pack(side='left', padx=(0, 14))

    tk.Frame(win, bg=border, height=1).pack(fill='x', padx=10, pady=4)

    # ── Zone list ─────────────────────────────────────────────────────
    tk.Label(
        win,
        text="Active zones:",
        bg=bg,
        fg=fg,
        font=label_font,
        anchor='w'
    ).pack(fill='x', padx=12, pady=(4, 3))

    list_frame = tk.Frame(
        win,
        bg=panel_bg,
        relief=list_relief,
        bd=list_bd,
        highlightthickness=1,
        highlightbackground=border
    )
    list_frame.pack(fill='x', padx=10, pady=(0, 5))

    editor._breach_listbox = tk.Listbox(
        list_frame,
        bg=panel_bg,
        fg=fg,
        selectbackground=select_bg,
        selectforeground='white',
        font=list_font,
        height=8,
        bd=0,
        highlightthickness=0
    )
    editor._breach_listbox.pack(fill='x', padx=5, pady=5)

    def _get_zone_flags(zid):
        if not editor._ain_area_states or zid >= len(editor._ain_area_states):
            return 0
        entry = editor._ain_area_states[zid]
        if len(entry) >= 4:
            return _struct.unpack_from('<I', entry)[0] & 0xFF
        return 0

    def _flag_label(flags):
        f01 = bool(flags & 0x01)
        f02 = bool(flags & 0x02)
        if f01 and f02:
            return "Both"
        if f02:
            return "Manual"
        if f01:
            return "Auto"
        return "?"

    def _refresh_list():
        editor._breach_listbox.delete(0, 'end')
        dev = bool(getattr(editor, 'dev_mode', tk.BooleanVar(value=False)).get())
        all_ids = sorted(editor._breach_zone_ids | editor._assault_zone_ids | editor._both_zone_ids)
        for zid in all_ids:
            flags = _get_zone_flags(zid)
            lbl = _flag_label(flags)
            if dev:
                text = f"  Zone {zid:3d}   [{lbl}]   (0x{flags:02X})"
            else:
                text = f"  Zone {zid:3d}   [{lbl}]"
            editor._breach_listbox.insert('end', text)

    editor._breach_refresh_list = _refresh_list
    _refresh_list()

    # ── Remove ────────────────────────────────────────────────────────
    def _remove_selected():
        sel = editor._breach_listbox.curselection()
        if not sel:
            return
        text = editor._breach_listbox.get(sel[0])
        try:
            zid = int(text.strip().split()[1])
        except Exception:
            return
        editor._breach_zone_ids.discard(zid)
        editor._assault_zone_ids.discard(zid)
        editor._both_zone_ids.discard(zid)
        if not editor._ain_area_states:
            editor._ain_area_states = [b'\x00' * 0xC] * 256
        while len(editor._ain_area_states) <= zid:
            editor._ain_area_states.append(b'\x00' * 0xC)
        editor._ain_area_states[zid] = b'\x00' * 0xC
        _refresh_list()
        editor.redraw()

    tk.Button(
        list_frame,
        text="Remove selected",
        command=_remove_selected,
        bg=remove_bg,
        fg=remove_fg,
        font=body_font,
        relief='flat',
        padx=10,
        pady=3
    ).pack(pady=(0, 5))

    tk.Frame(win, bg=border, height=1).pack(fill='x', padx=10, pady=6)

    # ── Add zone ──────────────────────────────────────────────────────
    tk.Label(
        win,
        text="Add zone:",
        bg=bg,
        fg=fg,
        font=label_font,
        anchor='w'
    ).pack(fill='x', padx=12, pady=(0, 4))

    add_row = tk.Frame(win, bg=bg)
    add_row.pack(fill='x', padx=12, pady=(0, 6))

    tk.Label(add_row, text="Zone ID:", bg=bg, fg=dim, font=body_font).pack(side='left')

    zone_id_var = tk.StringVar(value="5")
    tk.Entry(
        add_row,
        textvariable=zone_id_var,
        width=5,
        bg=entry_bg,
        fg=fg,
        font=list_font,
        insertbackground=fg,
        relief='flat'
    ).pack(side='left', padx=6)

    tk.Label(add_row, text="Type:", bg=bg, fg=dim, font=body_font).pack(side='left', padx=(12, 4))

    zone_type_var = tk.StringVar(value="Manual")
    type_menu = tk.OptionMenu(add_row, zone_type_var, "Manual", "Auto", "Both")
    type_menu.configure(
        bg=button_bg,
        fg=button_fg,
        font=body_font,
        activebackground=panel2,
        activeforeground=button_fg,
        highlightthickness=0,
        relief='flat',
        bd=0
    )
    type_menu['menu'].configure(bg=menu_bg, fg=menu_fg, font=body_font)
    type_menu.pack(side='left', padx=6)

    def _add_zone():
        try:
            zid = int(zone_id_var.get().strip())
            if not (0 <= zid <= 255):
                raise ValueError
        except ValueError:
            editor.status("Zone ID must be 0-255")
            return

        ztype = zone_type_var.get()
        flags = {'Manual': 0x02, 'Auto': 0x01, 'Both': 0x03}.get(ztype, 0x02)

        if not editor._ain_area_states:
            editor._ain_area_states = [b'\x00' * 0xC] * 256
        while len(editor._ain_area_states) <= zid:
            editor._ain_area_states.append(b'\x00' * 0xC)
        entry = bytearray(editor._ain_area_states[zid])
        if len(entry) < 0xC:
            entry = bytearray(entry) + bytearray(0xC - len(entry))
        _struct.pack_into('<I', entry, 0, flags)
        editor._ain_area_states[zid] = bytes(entry)

        editor._breach_zone_ids.discard(zid)
        editor._assault_zone_ids.discard(zid)
        editor._both_zone_ids.discard(zid)
        if flags & 0x02:
            editor._breach_zone_ids.add(zid)
        if flags & 0x01:
            editor._assault_zone_ids.add(zid)
        if (flags & 0x03) == 0x03:
            editor._both_zone_ids.add(zid)

        _refresh_list()
        if bool(getattr(editor, 'dev_mode', tk.BooleanVar(value=False)).get()):
            editor.status(f"Zone {zid} set to {ztype} (0x{flags:02X}) — will be written on Export AIN")
        else:
            editor.status(f"Zone {zid} set to {ztype} — will be written on Export AIN")
        editor.redraw()

    tk.Button(
        add_row,
        text="Add",
        command=_add_zone,
        bg=add_bg,
        fg=add_fg,
        font=body_font,
        relief='flat',
        padx=10,
        pady=3
    ).pack(side='left', padx=6)

    tk.Frame(win, bg=border, height=1).pack(fill='x', padx=10, pady=7)

    # ── Info ──────────────────────────────────────────────────────────
    editor._breach_requirements_label = tk.Label(
        win,
        text="",
        bg=bg,
        fg=info_fg,
        font=small_font,
        justify='left',
        anchor='w',
        wraplength=414
    )
    editor._breach_requirements_label.pack(fill='x', padx=12, pady=(0, 10))

    editor._sync_breach_panel_mode()

    def _close_breach_win():
        try:
            win.destroy()
        finally:
            editor._breach_win = None
            editor._breach_mode_summary_label = None
            editor._breach_requirements_label = None
            editor._breach_refresh_list = None

    win.protocol("WM_DELETE_WINDOW", _close_breach_win)

    # Center over the main editor before showing the popup.
    try:
        editor._center_popup_over_editor(win, width=440)
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
