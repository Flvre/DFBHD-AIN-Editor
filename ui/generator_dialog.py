"""Generator-options dialog construction and validation.

This module is independent of :mod:`app`; the editor is supplied explicitly
as the state/callback adapter for the existing dialog behavior.
"""

import tkinter as tk
import math
from tkinter import messagebox

from config.ui_theme import C_ACCENT, C_BORDER, C_PANEL, C_TEXT, UI_THEME_DARK
from format.ain_format import MAX_NEIGHBORS
from generator.support import GENERATOR_REACHABLE_MAX_STEP
import runtime_namespace as _ns


def _load_config():
    return _ns.get('_load_config', lambda: {})()


def _save_config(config):
    return _ns.get('_save_config', lambda _config: None)(config)


def ask_generator_options(editor, preselect_zone=False, fixed_seed_coordinates=None):
    """Ask for generation radius / zone selection and generator options.

    Top-bar Generate always starts on Seed only.
    Area Zone generation explicitly preselects the selected zone.
    Coordinate generation locks the target to Seed only and displays the
    supplied X/Y while Z remains resolved by the normal generator layer
    logic.
    """
    coordinate_mode = fixed_seed_coordinates is not None
    coordinate_x = coordinate_y = None
    if coordinate_mode:
        coordinate_x = float(fixed_seed_coordinates[0])
        coordinate_y = float(fixed_seed_coordinates[1])
    light = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
    if light:
        dlg_bg = '#f4f4f4'
        fg = '#1c1c1c'
        dim_fg = '#555555'
        entry_bg = '#ffffff'
        entry_fg = '#111111'
        select_bg = '#e8e8e8'
        border = '#c6c6c6'
        cancel_bg = '#eeeeee'
        cancel_fg = '#222222'
        go_bg = '#c9e8c9'
        go_fg = '#005500'
        disabled_fg = '#999999'
    else:
        dlg_bg = '#2b2b2b'
        fg = 'white'
        dim_fg = '#bfc7d2'
        entry_bg = '#161616'
        entry_fg = 'white'
        select_bg = '#161616'
        border = C_BORDER
        cancel_bg = '#3a3a3a'
        cancel_fg = C_TEXT
        go_bg = '#005500'
        go_fg = 'white'
        disabled_fg = '#666666'

    dialog = tk.Toplevel(editor)
    dialog.title('Generate AIN')
    dialog.transient(editor)
    dialog.resizable(False, False)
    dialog.configure(bg=dlg_bg)
    result = {'value': None}

    zones_with_tiles = []
    if not coordinate_mode:
        for anum in sorted(editor.zones.keys()):
            meta = editor._ensure_zone_meta(anum)
            tiles = meta.get('tiles')
            if tiles:
                name = meta.get('name') or f'Zone {anum}'
                zones_with_tiles.append((anum, name, len(tiles)))

    zone_choices = [
        'Seed only (fixed coordinates)' if coordinate_mode
        else 'Seed only (click to place)'
    ]
    zone_map = [None]
    for anum, name, nt in zones_with_tiles:
        zone_choices.append(f'{name}  [{nt} tiles]')
        zone_map.append(anum)

    preselect = 0
    if preselect_zone and zones_with_tiles and editor._zone_selected is not None:
        for i, anum in enumerate(zone_map):
            if anum == editor._zone_selected:
                preselect = i
                break

    zone_var = tk.StringVar(value=zone_choices[preselect])

    radius_label = tk.Label(
        dialog,
        text=('Radius around coordinate seed:' if coordinate_mode
              else 'Radius around clicked seed:'),
        bg=dlg_bg,
        fg=fg,
        anchor='w',
        font=('Consolas', 9),
    )
    radius_label.grid(row=0, column=0, padx=12, pady=(12, 4), sticky='w')
    radius_var = tk.StringVar(
        value=str(float(getattr(editor, '_seed_generate_radius', 32.0) or 32.0)))
    radius_entry = tk.Entry(
        dialog,
        textvariable=radius_var,
        width=12,
        bg=entry_bg,
        fg=entry_fg,
        insertbackground=entry_fg,
        highlightbackground=border,
        highlightcolor=border,
        relief='solid',
        bd=1,
        font=('Consolas', 9),
    )
    radius_entry.grid(row=0, column=1, padx=12, pady=(12, 4), sticky='e')

    if zones_with_tiles or coordinate_mode:
        tk.Label(
            dialog,
            text=('Seed / zone:' if coordinate_mode else 'Generate zone:'),
            bg=dlg_bg, fg=(disabled_fg if coordinate_mode else fg),
            anchor='w', font=('Consolas', 9),
        ).grid(row=1, column=0, padx=12, pady=(4, 4), sticky='w')
        zone_menu = tk.OptionMenu(dialog, zone_var, *zone_choices)
        zone_menu.configure(
            bg=entry_bg, fg=entry_fg, activebackground=entry_bg,
            activeforeground=entry_fg, highlightbackground=border,
            font=('Consolas', 9), relief='solid', bd=1)
        zone_menu['menu'].configure(
            bg=entry_bg, fg=entry_fg, activebackground=go_bg,
            activeforeground=go_fg, font=('Consolas', 9))
        zone_menu.grid(row=1, column=1, padx=12, pady=(4, 4), sticky='ew')
        if coordinate_mode:
            try:
                zone_menu.configure(state='disabled', disabledforeground=disabled_fg)
            except Exception:
                zone_menu.configure(state='disabled')
            tk.Label(
                dialog,
                text=(f'Coordinate seed: ON   X={coordinate_x:.3f}   Y={coordinate_y:.3f}\n'
                      'Z coordinate: automatic (resolved from map/support)'),
                bg=dlg_bg, fg=dim_fg, justify='left', anchor='w',
                font=('Consolas', 8),
            ).grid(row=2, column=0, columnspan=2, padx=12,
                   pady=(2, 5), sticky='w')
            opt_row_start = 3
        else:
            opt_row_start = 2
    else:
        opt_row_start = 1

    def sync_zone_selection(*_):
        sel = zone_var.get()
        idx = zone_choices.index(sel) if sel in zone_choices else 0
        is_zone = (idx > 0)
        if is_zone:
            radius_entry.configure(state='disabled', fg=disabled_fg)
            radius_label.configure(fg=disabled_fg)
        else:
            radius_entry.configure(state='normal', fg=entry_fg)
            radius_label.configure(fg=fg)

    zone_var.trace_add('write', sync_zone_selection)
    sync_zone_selection()

    z_filter_var = tk.BooleanVar(
        value=bool(getattr(editor, '_seed_generate_z_filter', True)))
    z_filter_check = tk.Checkbutton(
        dialog,
        text='Automatic generator Z filter (Interior)',
        variable=z_filter_var,
        bg=dlg_bg,
        fg=fg,
        selectcolor=select_bg,
        activebackground=dlg_bg,
        activeforeground=fg,
        font=('Consolas', 9),
    )
    z_filter_check.grid(
        row=opt_row_start, column=0, columnspan=2,
        padx=12, pady=(7, 0), sticky='w')
    tk.Label(
        dialog,
        text=f'Follows connected slopes/stairs up to {GENERATOR_REACHABLE_MAX_STEP:.2f} m and ignores\n'
             'disconnected raised surfaces such as tables and chairs.',
        bg=dlg_bg,
        fg=dim_fg,
        justify='left',
        font=('Consolas', 8),
    ).grid(row=opt_row_start + 1, column=0, columnspan=2, padx=30, pady=(2, 6), sticky='w')

    rooftops_var = tk.BooleanVar(
        value=bool(getattr(editor, '_seed_generate_rooftops', False)))
    rooftops_check = tk.Checkbutton(
        dialog,
        text='Generate on elevated platforms / rooftops',
        variable=rooftops_var,
        bg=dlg_bg,
        fg=fg,
        selectcolor=select_bg,
        activebackground=dlg_bg,
        activeforeground=fg,
        font=('Consolas', 9),
    )
    rooftops_check.grid(
        row=opt_row_start + 2, column=0, columnspan=2,
        padx=12, pady=(3, 3), sticky='w')

    highest_rooftops_var = tk.BooleanVar(
        value=bool(getattr(
            editor, '_seed_generate_highest_rooftops', False)))
    highest_rooftops_check = tk.Checkbutton(
        dialog,
        text='Limit added platforms to highest broad surfaces',
        variable=highest_rooftops_var,
        bg=dlg_bg,
        fg=fg,
        selectcolor=select_bg,
        activebackground=dlg_bg,
        activeforeground=fg,
        font=('Consolas', 9),
    )
    highest_rooftops_check.grid(
        row=opt_row_start + 3, column=0, columnspan=2,
        padx=30, pady=(0, 3), sticky='w')

    ladders_var = tk.BooleanVar(
        value=bool(getattr(editor, '_seed_generate_ladders', False)))
    ladders_check = tk.Checkbutton(
        dialog,
        text='Include ladders',
        variable=ladders_var,
        bg=dlg_bg,
        fg=fg,
        selectcolor=select_bg,
        activebackground=dlg_bg,
        activeforeground=fg,
        font=('Consolas', 9),
    )
    ladders_check.grid(
        row=opt_row_start + 4, column=0, columnspan=2,
        padx=30, pady=(0, 6), sticky='w')

    def sync_ladder_option():
        if bool(z_filter_var.get()) and bool(rooftops_var.get()):
            ladders_check.configure(state='normal')
        else:
            ladders_var.set(False)
            ladders_check.configure(state='disabled')

    def sync_rooftop_option():
        if bool(z_filter_var.get()):
            rooftops_check.configure(state='normal')
        else:
            rooftops_var.set(False)
            rooftops_check.configure(state='disabled')
        if bool(z_filter_var.get()) and bool(rooftops_var.get()):
            highest_rooftops_check.configure(state='normal')
        else:
            highest_rooftops_var.set(False)
            highest_rooftops_check.configure(state='disabled')
        sync_ladder_option()

    z_filter_check.configure(command=sync_rooftop_option)
    rooftops_check.configure(command=sync_rooftop_option)
    sync_rooftop_option()

    ignore_destroyable_var = tk.BooleanVar(
        value=bool(getattr(editor, '_seed_ignore_destroyable', True)))
    tk.Checkbutton(
        dialog,
        text='Ignore destroyable objects',
        variable=ignore_destroyable_var,
        bg=dlg_bg,
        fg=fg,
        selectcolor=select_bg,
        activebackground=dlg_bg,
        activeforeground=fg,
        font=('Consolas', 9),
    ).grid(row=opt_row_start + 5, column=0, columnspan=2,
           padx=12, pady=(0, 6), sticky='w')

    ignore_vehicles_var = tk.BooleanVar(
        value=bool(getattr(editor, '_seed_ignore_vehicles', False)))
    tk.Checkbutton(
        dialog,
        text='Ignore vehicles',
        variable=ignore_vehicles_var,
        bg=dlg_bg,
        fg=fg,
        selectcolor=select_bg,
        activebackground=dlg_bg,
        activeforeground=fg,
        font=('Consolas', 9),
    ).grid(row=opt_row_start + 6, column=0, columnspan=2,
           padx=12, pady=(0, 6), sticky='w')

    quantized_var = tk.BooleanVar(
        value=bool(getattr(editor, '_seed_generate_quantized', False)))
    tk.Checkbutton(
        dialog,
        text='Quantized placement (Experimental)',
        variable=quantized_var,
        bg=dlg_bg,
        fg=fg,
        selectcolor=select_bg,
        activebackground=dlg_bg,
        activeforeground=fg,
        font=('Consolas', 9),
    ).grid(row=opt_row_start + 7, column=0, columnspan=2, padx=12, pady=(0, 10), sticky='w')

    buttons = tk.Frame(dialog, bg=dlg_bg)
    buttons.grid(row=opt_row_start + 8, column=0, columnspan=2, padx=12, pady=(0, 12), sticky='e')

    def accept():
        sel = zone_var.get()
        zone_idx = zone_choices.index(sel) if sel in zone_choices else 0
        selected_zone = zone_map[zone_idx] if zone_idx > 0 else None

        if selected_zone is None:
            try:
                radius = float(radius_var.get())
            except Exception:
                messagebox.showerror(
                    'Generate AIN', 'Radius must be a number.', parent=dialog)
                return
            if radius < 8.0 or radius > 80.0:
                messagebox.showerror(
                    'Generate AIN', 'Radius must be between 8 and 80 meters.',
                    parent=dialog)
                return
        else:
            radius = 32.0

        if bool(quantized_var.get()):
            cfg = _load_config()
            if not cfg.get('quantized_warning_dismissed'):
                warn_win = tk.Toplevel(dialog)
                warn_win.title('Quantized Mode')
                warn_win.transient(dialog)
                warn_win.resizable(False, False)
                warn_win.configure(bg=dlg_bg)
                warn_win.grab_set()
                wf = tk.Frame(warn_win, bg=dlg_bg, padx=18, pady=14)
                wf.pack(fill='both', expand=True)
                tk.Label(wf,
                         text='Quantized placement is experimental.',
                         bg=dlg_bg, fg=fg,
                         font=('Segoe UI', 10, 'bold'),
                         anchor='w').pack(fill='x')
                tk.Label(wf,
                         text=('The quantized method may not generate '
                               'nodes properly at building doorways.\n'
                               'AI pathfinding through entrances may be '
                               'incomplete on maps with interior areas.'),
                         bg=dlg_bg, fg=fg,
                         font=('Segoe UI', 9),
                         anchor='w', justify='left',
                         wraplength=380).pack(fill='x', pady=(6, 10))
                never_var = tk.BooleanVar(value=False)
                tk.Checkbutton(wf, text='Never show again',
                               variable=never_var,
                               bg=dlg_bg, fg=fg,
                               selectcolor=select_bg,
                               activebackground=dlg_bg,
                               activeforeground=fg,
                               font=('Consolas', 9)).pack(anchor='w')
                warn_result = {'proceed': False}
                def _warn_ok():
                    if never_var.get():
                        c = _load_config()
                        c['quantized_warning_dismissed'] = True
                        _save_config(c)
                    warn_result['proceed'] = True
                    warn_win.destroy()
                wb = tk.Frame(wf, bg=dlg_bg)
                wb.pack(fill='x', pady=(10, 0))
                tk.Button(wb, text='Cancel', command=warn_win.destroy,
                          bg=cancel_bg, fg=cancel_fg,
                          activebackground=border,
                          activeforeground=cancel_fg,
                          relief='flat', font=('Consolas', 9),
                          padx=10, pady=2).pack(side='right', padx=(6, 0))
                tk.Button(wb, text='Continue', command=_warn_ok,
                          bg=go_bg, fg=go_fg,
                          activebackground=go_bg,
                          activeforeground=go_fg,
                          relief='flat', font=('Consolas', 9, 'bold'),
                          padx=10, pady=2).pack(side='right')
                warn_win.protocol('WM_DELETE_WINDOW', warn_win.destroy)
                warn_win.bind('<Return>', lambda _e: _warn_ok())
                warn_win.bind('<Escape>', lambda _e: warn_win.destroy())
                warn_win.update_idletasks()
                try:
                    wx = dialog.winfo_rootx() + (dialog.winfo_width() - warn_win.winfo_width()) // 2
                    wy = dialog.winfo_rooty() + (dialog.winfo_height() - warn_win.winfo_height()) // 2
                    warn_win.geometry(f'+{max(0, wx)}+{max(0, wy)}')
                except Exception:
                    pass
                dialog.wait_window(warn_win)
                if not warn_result['proceed']:
                    return

        if selected_zone is not None and bool(z_filter_var.get()):
            cfg = _load_config()
            if not cfg.get('zone_zfilter_warning_dismissed'):
                warn_win = tk.Toplevel(dialog)
                warn_win.title('Area Zone + Z Filter')
                warn_win.transient(dialog)
                warn_win.resizable(False, False)
                warn_win.configure(bg=dlg_bg)
                warn_win.grab_set()
                wf = tk.Frame(warn_win, bg=dlg_bg, padx=18, pady=14)
                wf.pack(fill='both', expand=True)
                tk.Label(wf,
                         text='Area zone with Z filter active',
                         bg=dlg_bg, fg=fg,
                         font=('Segoe UI', 10, 'bold'),
                         anchor='w').pack(fill='x')
                tk.Label(wf,
                         text=('Area zones may fail to generate nodes '
                               'inside certain rooms or floors due to '
                               'how tile boundaries interact with the '
                               'Z filter.\n\n'
                               'If there are buildings with navigable '
                               'interiors in this zone, a normal circle '
                               'seed with the Z filter on is recommended '
                               'instead.'),
                         bg=dlg_bg, fg=fg,
                         font=('Segoe UI', 9),
                         anchor='w', justify='left',
                         wraplength=380).pack(fill='x', pady=(6, 10))
                never_var2 = tk.BooleanVar(value=False)
                tk.Checkbutton(wf, text='Never show again',
                               variable=never_var2,
                               bg=dlg_bg, fg=fg,
                               selectcolor=select_bg,
                               activebackground=dlg_bg,
                               activeforeground=fg,
                               font=('Consolas', 9)).pack(anchor='w')
                warn_result2 = {'proceed': False}
                def _warn_ok2():
                    if never_var2.get():
                        c = _load_config()
                        c['zone_zfilter_warning_dismissed'] = True
                        _save_config(c)
                    warn_result2['proceed'] = True
                    warn_win.destroy()
                wb = tk.Frame(wf, bg=dlg_bg)
                wb.pack(fill='x', pady=(10, 0))
                tk.Button(wb, text='Cancel', command=warn_win.destroy,
                          bg=cancel_bg, fg=cancel_fg,
                          activebackground=border,
                          activeforeground=cancel_fg,
                          relief='flat', font=('Consolas', 9),
                          padx=10, pady=2).pack(side='right', padx=(6, 0))
                tk.Button(wb, text='Continue', command=_warn_ok2,
                          bg=go_bg, fg=go_fg,
                          activebackground=go_bg,
                          activeforeground=go_fg,
                          relief='flat', font=('Consolas', 9, 'bold'),
                          padx=10, pady=2).pack(side='right')
                warn_win.protocol('WM_DELETE_WINDOW', warn_win.destroy)
                warn_win.bind('<Return>', lambda _e: _warn_ok2())
                warn_win.bind('<Escape>', lambda _e: warn_win.destroy())
                warn_win.update_idletasks()
                try:
                    wx = dialog.winfo_rootx() + (dialog.winfo_width() - warn_win.winfo_width()) // 2
                    wy = dialog.winfo_rooty() + (dialog.winfo_height() - warn_win.winfo_height()) // 2
                    warn_win.geometry(f'+{max(0, wx)}+{max(0, wy)}')
                except Exception:
                    pass
                dialog.wait_window(warn_win)
                if not warn_result2['proceed']:
                    return

        result['value'] = (
            radius,
            bool(z_filter_var.get()),
            bool(z_filter_var.get()) and bool(rooftops_var.get()),
            bool(quantized_var.get()),
            selected_zone,
            (bool(z_filter_var.get())
             and bool(rooftops_var.get())
             and bool(ladders_var.get())),
            (bool(z_filter_var.get())
             and bool(rooftops_var.get())
             and bool(highest_rooftops_var.get())),
            bool(ignore_destroyable_var.get()),
            bool(ignore_vehicles_var.get()),
        )
        dialog.destroy()

    tk.Button(buttons, text='Cancel', command=dialog.destroy,
              bg=cancel_bg, fg=cancel_fg, activebackground=border,
              activeforeground=cancel_fg, relief='flat',
              font=('Consolas', 9), padx=10, pady=2).pack(
        side='right', padx=(6, 0))
    tk.Button(buttons, text='Generate', command=accept,
              bg=go_bg, fg=go_fg, activebackground=go_bg,
              activeforeground=go_fg, relief='flat',
              font=('Consolas', 9, 'bold'), padx=10, pady=2).pack(side='right')
    dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
    dialog.bind('<Return>', lambda _event: accept())
    dialog.bind('<Escape>', lambda _event: dialog.destroy())
    if preselect == 0:
        radius_entry.focus_set()
        radius_entry.selection_range(0, 'end')

    try:
        dialog.update_idletasks()
        dw = dialog.winfo_width()
        dh = dialog.winfo_height()
        px = editor.winfo_rootx()
        py = editor.winfo_rooty()
        pw = editor.winfo_width()
        ph = editor.winfo_height()
        x = px + (pw - dw) // 2
        y = py + (ph - dh) // 2
        dialog.geometry(f'+{max(x, 0)}+{max(y, 0)}')
    except Exception:
        pass

    # Do not grab the application here.  Keeping the dialog transient/owned
    # is enough; without the grab, the main editor can still be minimized
    # from its title bar and the dialog follows it.
    editor.wait_window(dialog)
    return result['value']



def show_previous_generator_seeds(editor):
    """Show saved/current seed history and allow a seed/options retry."""
    win = getattr(editor, '_previous_generator_seeds_window', None)
    try:
        if win is not None and win.winfo_exists():
            editor._refresh_previous_generator_seeds_window()
            win.deiconify()
            win.lift()
            win.focus_force()
            return
    except Exception:
        pass

    win = tk.Toplevel(editor)
    editor._previous_generator_seeds_window = win
    win.title('Previous Generator Seeds')
    win.geometry('900x440')
    win.minsize(620, 300)
    try:
        win.transient(editor)
    except Exception:
        pass

    light = bool(editor.light_panels.get())
    bg = '#f3f3f3' if light else C_PANEL
    fg = '#111111' if light else C_TEXT
    list_bg = '#ffffff' if light else '#1e1e1e'
    select_bg = '#cfe3f8' if light else C_ACCENT
    select_fg = '#111111' if light else 'white'
    win.configure(bg=bg)

    tk.Label(
        win,
        text=('Seed history is saved with project JSON. Select a run and use '
              'Retry Seed to submit the same X/Y/radius and generator options again.'),
        bg=bg, fg=fg, anchor='w', justify='left',
        font=('Consolas', 9)).pack(fill='x', padx=10, pady=(10, 6))

    frame = tk.Frame(win, bg=bg)
    frame.pack(fill='both', expand=True, padx=10, pady=(0, 8))
    box = tk.Listbox(
        frame, bg=list_bg, fg=fg, selectbackground=select_bg,
        selectforeground=select_fg, activestyle='none', exportselection=False,
        font=('Consolas', 9), relief='sunken', bd=1)
    ybar = tk.Scrollbar(frame, orient='vertical', command=box.yview)
    xbar = tk.Scrollbar(frame, orient='horizontal', command=box.xview)
    box.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    box.grid(row=0, column=0, sticky='nsew')
    ybar.grid(row=0, column=1, sticky='ns')
    xbar.grid(row=1, column=0, sticky='ew')
    editor._previous_generator_seeds_listbox = box
    box.bind('<<ListboxSelect>>', editor._update_previous_generator_seed_retry_state)
    box.bind('<Double-Button-1>', lambda _e: editor._retry_previous_generator_seed())

    buttons = tk.Frame(win, bg=bg)
    buttons.pack(fill='x', padx=10, pady=(0, 10))

    retry = tk.Button(buttons, text='Retry Seed', command=editor._retry_previous_generator_seed,
                      font=('Consolas', 9), state='disabled')
    retry.pack(side='left')
    editor._previous_generator_seeds_retry_button = retry

    def copy_all():
        payload = editor._format_previous_generator_seeds()
        try:
            editor.clipboard_clear()
            editor.clipboard_append(payload)
            editor.update_idletasks()
            editor.status(
                f'Copied {len(getattr(editor, "_previous_generator_seeds", []))} generator seed(s).')
        except Exception:
            pass

    def clear_all():
        editor._previous_generator_seeds = []
        editor._refresh_previous_generator_seeds_window()
        editor.status('Previous Generator Seeds: history cleared; save the project to keep this change.')

    copy_selected = tk.Button(
        buttons, text='Copy Selected',
        command=editor._copy_selected_previous_generator_seed,
        font=('Consolas', 9), state='disabled')
    copy_selected.pack(side='left', padx=(6, 0))
    editor._previous_generator_seeds_copy_button = copy_selected

    tk.Button(buttons, text='Copy All', command=copy_all,
              font=('Consolas', 9)).pack(side='left', padx=(6, 0))
    tk.Button(buttons, text='Clear', command=clear_all,
              font=('Consolas', 9)).pack(side='left', padx=(6, 0))
    tk.Button(buttons, text='Close', command=win.destroy,
              font=('Consolas', 9)).pack(side='right')

    def on_close():
        try:
            win.destroy()
        finally:
            editor._previous_generator_seeds_window = None
            editor._previous_generator_seeds_listbox = None
            editor._previous_generator_seeds_retry_button = None
            editor._previous_generator_seeds_copy_button = None
    win.protocol('WM_DELETE_WINDOW', on_close)

    editor._refresh_previous_generator_seeds_window(select_last=True)

    # Center over the editor after Tk has resolved the requested size.  The
    # old diagnostic opened at the window manager's default top-left origin.
    try:
        win.update_idletasks()
        ww = win.winfo_width()
        wh = win.winfo_height()
        px = editor.winfo_rootx() + max(0, (editor.winfo_width() - ww) // 2)
        py = editor.winfo_rooty() + max(0, (editor.winfo_height() - wh) // 2)
        win.geometry(f'+{px}+{py}')
    except Exception:
        pass


def ask_generate_coordinates(editor):
    """Ask for an exact X/Y seed in a small theme-aware centered popup."""
    pal = editor._ui_theme()
    dialog = tk.Toplevel(editor)
    try:
        dialog.withdraw()
    except Exception:
        pass
    dialog.title('Generate with coordinates')
    dialog.resizable(False, False)
    dialog.configure(bg=pal['window_bg'])
    editor._set_owned_popup_window(dialog)

    result = {'value': None}
    previous = getattr(editor, '_last_coordinate_generator_seed', None)
    x_var = tk.StringVar(value=(
        f'{float(previous[0]):.3f}' if previous is not None else ''))
    y_var = tk.StringVar(value=(
        f'{float(previous[1]):.3f}' if previous is not None else ''))
    error_var = tk.StringVar(value='')

    outer = tk.Frame(dialog, bg=pal['window_bg'])
    outer.pack(fill='both', expand=True, padx=8, pady=6)
    outer.columnconfigure(1, weight=1)

    tk.Label(
        outer, text='Generator seed coordinates',
        bg=pal['window_bg'], fg=pal['text'],
        font=('Consolas', 10, 'bold'), anchor='w',
    ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))

    entry_opts = dict(
        bg=pal['control_bg'], fg=pal['text'],
        insertbackground=pal['text'],
        selectbackground=pal['selection_bg'],
        selectforeground=pal['selection_fg'],
        relief='flat', highlightthickness=1,
        highlightbackground=pal['border'],
        highlightcolor=pal['selection_bg'],
        font=('Consolas', 9), width=16,
    )

    tk.Label(outer, text='X:', bg=pal['window_bg'], fg=pal['text'],
             font=('Consolas', 9)).grid(
        row=1, column=0, sticky='w', padx=(0, 7), pady=1)
    x_entry = tk.Entry(outer, textvariable=x_var, **entry_opts)
    x_entry.grid(row=1, column=1, sticky='ew', pady=1)

    tk.Label(outer, text='Y:', bg=pal['window_bg'], fg=pal['text'],
             font=('Consolas', 9)).grid(
        row=2, column=0, sticky='w', padx=(0, 7), pady=1)
    y_entry = tk.Entry(outer, textvariable=y_var, **entry_opts)
    y_entry.grid(row=2, column=1, sticky='ew', pady=1)

    error_label = tk.Label(
        outer, textvariable=error_var,
        bg=pal['window_bg'], fg='#ff5555',
        font=('Consolas', 8), anchor='w', justify='left')
    error_label.grid(row=3, column=0, columnspan=2, sticky='w', pady=(3, 0))
    error_label.grid_remove()

    buttons = tk.Frame(outer, bg=pal['window_bg'])
    buttons.grid(row=4, column=0, columnspan=2, sticky='e', pady=(5, 0))

    def cancel():
        dialog.destroy()

    def accept():
        try:
            x = float(x_var.get().strip().replace(',', '.'))
            y = float(y_var.get().strip().replace(',', '.'))
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError
        except Exception:
            error_var.set('Enter valid numeric X and Y coordinates.')
            try:
                error_label.grid()
                dialog.update_idletasks()
                editor._center_popup_over_editor(dialog)
                dialog.bell()
            except Exception:
                pass
            return
        result['value'] = (float(x), float(y))
        editor._last_coordinate_generator_seed = result['value']
        dialog.destroy()

    tk.Button(
        buttons, text='Cancel', command=cancel,
        bg=pal['button_bg'], fg=pal['button_fg'],
        activebackground=pal['button_active_bg'],
        activeforeground=pal['button_active_fg'],
        relief='flat', font=('Consolas', 9), padx=10, pady=2
    ).pack(side='right', padx=(6, 0))
    tk.Button(
        buttons, text='OK', command=accept,
        bg=('#007000' if pal is UI_THEME_DARK else '#0b7a28'), fg='#ffffff',
        activebackground=('#008000' if pal is UI_THEME_DARK else '#096a22'),
        activeforeground='#ffffff',
        relief='flat', font=('Consolas', 9, 'bold'), padx=10, pady=2
    ).pack(side='right')

    dialog.protocol('WM_DELETE_WINDOW', cancel)
    dialog.bind('<Return>', lambda _event: accept())
    dialog.bind('<Escape>', lambda _event: cancel())
    try:
        dialog.update_idletasks()
        editor._center_popup_over_editor(
            dialog,
            width=max(1, dialog.winfo_reqwidth()),
            height=max(1, dialog.winfo_reqheight()))
    except Exception:
        editor._center_popup_over_editor(dialog)
    try:
        dialog.deiconify()
    except Exception:
        pass
    try:
        x_entry.focus_set()
        x_entry.selection_range(0, 'end')
    except Exception:
        pass
    editor.wait_window(dialog)
    return result['value']


def ask_link_by_distance_options(editor, selected_count):
    """Ask for the limits used by the selection-only distance linker."""
    pal = editor._ui_theme()
    dialog = tk.Toplevel(editor)
    try:
        dialog.withdraw()
    except Exception:
        pass
    dialog.title('Link by Distance')
    dialog.resizable(False, False)
    dialog.configure(bg=pal['window_bg'])
    editor._set_owned_popup_window(dialog)

    result = {'value': None}
    distance_var = tk.StringVar(
        value=f"{float(getattr(editor, '_link_by_distance_max', 12.0)):g}")
    neighbors_var = tk.StringVar(
        value=str(int(getattr(editor, '_link_by_distance_neighbors', 8))))

    outer = tk.Frame(dialog, bg=pal['window_bg'])
    outer.pack(fill='both', expand=True, padx=14, pady=12)

    tk.Label(
        outer,
        text=f'Link {int(selected_count)} selected nodes',
        bg=pal['window_bg'], fg=pal['text'],
        font=('Consolas', 10, 'bold'), anchor='w',
    ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))
    tk.Label(
        outer,
        text=('Links inside the selection are rebuilt. Connections to unselected nodes\n'
              'are preserved. New links must pass collision and vertical-layer checks.'),
        bg=pal['window_bg'], fg=pal['text_dim'], justify='left',
        font=('Consolas', 8), anchor='w',
    ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 12))

    entry_opts = dict(
        bg=pal['control_bg'], fg=pal['text'],
        insertbackground=pal['text'],
        selectbackground=pal['selection_bg'],
        selectforeground=pal['selection_fg'],
        relief='flat', highlightthickness=1,
        highlightbackground=pal['border'],
        highlightcolor=pal['selection_bg'],
        font=('Consolas', 9), width=12,
    )

    tk.Label(
        outer, text='Maximum distance (m):',
        bg=pal['window_bg'], fg=pal['text'],
        font=('Consolas', 9), anchor='w',
    ).grid(row=2, column=0, sticky='w', padx=(0, 14), pady=4)
    distance_entry = tk.Entry(outer, textvariable=distance_var, **entry_opts)
    distance_entry.grid(row=2, column=1, sticky='e', pady=4)

    tk.Label(
        outer, text='Maximum neighbours per node:',
        bg=pal['window_bg'], fg=pal['text'],
        font=('Consolas', 9), anchor='w',
    ).grid(row=3, column=0, sticky='w', padx=(0, 14), pady=4)
    neighbors_entry = tk.Entry(outer, textvariable=neighbors_var, **entry_opts)
    neighbors_entry.grid(row=3, column=1, sticky='e', pady=4)

    tk.Label(
        outer,
        text=f'Neighbour limit includes existing links. NAI2 maximum: {MAX_NEIGHBORS}.',
        bg=pal['window_bg'], fg=pal['text_dim'], justify='left',
        font=('Consolas', 8), anchor='w',
    ).grid(row=4, column=0, columnspan=2, sticky='w', pady=(7, 10))

    buttons = tk.Frame(outer, bg=pal['window_bg'])
    buttons.grid(row=5, column=0, columnspan=2, sticky='e')

    def accept():
        try:
            max_distance = float(distance_var.get())
        except Exception:
            messagebox.showerror(
                'Link by Distance', 'Maximum distance must be a number.',
                parent=dialog)
            distance_entry.focus_set()
            distance_entry.selection_range(0, 'end')
            return
        if not math.isfinite(max_distance) or not (0.05 <= max_distance <= 4096.0):
            messagebox.showerror(
                'Link by Distance',
                'Maximum distance must be between 0.05 and 4096 metres.',
                parent=dialog)
            distance_entry.focus_set()
            distance_entry.selection_range(0, 'end')
            return
        try:
            max_neighbors = int(neighbors_var.get())
        except Exception:
            messagebox.showerror(
                'Link by Distance', 'Maximum neighbours must be a whole number.',
                parent=dialog)
            neighbors_entry.focus_set()
            neighbors_entry.selection_range(0, 'end')
            return
        if not (1 <= max_neighbors <= MAX_NEIGHBORS):
            messagebox.showerror(
                'Link by Distance',
                f'Maximum neighbours must be between 1 and {MAX_NEIGHBORS}.',
                parent=dialog)
            neighbors_entry.focus_set()
            neighbors_entry.selection_range(0, 'end')
            return

        editor._link_by_distance_max = float(max_distance)
        editor._link_by_distance_neighbors = int(max_neighbors)
        result['value'] = (float(max_distance), int(max_neighbors))
        dialog.destroy()

    tk.Button(
        buttons, text='Cancel', command=dialog.destroy,
        bg=pal['button_bg'], fg=pal['button_fg'],
        activebackground=pal['button_active_bg'],
        activeforeground=pal['button_active_fg'],
        relief='flat', font=('Consolas', 9), padx=10, pady=2,
    ).pack(side='right', padx=(6, 0))
    tk.Button(
        buttons, text='Link', command=accept,
        bg='#007000' if pal is UI_THEME_DARK else '#0b7a28', fg='#ffffff',
        activebackground='#008000' if pal is UI_THEME_DARK else '#096a22',
        activeforeground='#ffffff', relief='flat',
        font=('Consolas', 9, 'bold'), padx=10, pady=2,
    ).pack(side='right')

    dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
    dialog.bind('<Return>', lambda _event: accept())
    dialog.bind('<Escape>', lambda _event: dialog.destroy())
    dialog.update_idletasks()
    editor._center_popup_over_editor(dialog)
    try:
        dialog.deiconify()
        dialog.lift(editor)
    except Exception:
        pass
    distance_entry.focus_set()
    distance_entry.selection_range(0, 'end')
    dialog.grab_set()
    editor.wait_window(dialog)
    return result['value']
