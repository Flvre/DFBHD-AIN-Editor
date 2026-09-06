"""Generator progress-window UI helpers.

The helpers deliberately do not import :mod:`app`.  ``editor`` is an explicit
state/callback adapter supplied by the composition root, which keeps the UI
module independent of the AINEditor class while preserving the existing
progress-window behavior and callback wiring.
"""

import re
import time
import tkinter as tk
from tkinter import ttk


def close_window(editor):
    """Cancel timers and destroy the current generator progress window."""
    job = getattr(editor, '_generator_progress_timer_job', None)
    if job is not None:
        try:
            editor.after_cancel(job)
        except Exception:
            pass
    editor._generator_progress_timer_job = None

    win = getattr(editor, '_generator_progress_window', None)
    if win is not None:
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass

    editor._generator_progress_window = None
    editor._generator_progress_bar = None
    editor._generator_progress_history_box = None
    editor._generator_progress_report_box = None
    editor._generator_progress_copy_button = None
    editor._generator_progress_close_button = None
    editor._generator_progress_cancel_button = None


def show_window(editor, cx, cy, radius):
    """Create and display the modal generator phase/timing monitor."""
    close_window(editor)
    try:
        light = bool(editor.light_panels.get())
    except Exception:
        light = False

    bg = '#f2f2f2' if light else '#252525'
    panel = '#ffffff' if light else '#181818'
    fg = '#161616' if light else '#f2f2f2'
    dim = '#555555' if light else '#aeb7c2'
    border = '#b7b7b7' if light else '#505050'

    win = tk.Toplevel(editor)
    win.title('Generating AIN')
    win.transient(editor)
    win.resizable(False, False)
    win.configure(bg=bg)
    win.protocol('WM_DELETE_WINDOW', editor._cancel_generator_process)
    editor._generator_progress_window = win
    editor._generator_progress_started_at = time.perf_counter()
    editor._generator_progress_finished_at = None
    editor._generator_progress_phase_started_at = editor._generator_progress_started_at
    editor._generator_progress_phase_name = None
    editor._generator_progress_phase_detail = ''
    editor._generator_progress_history = []

    outer = tk.Frame(win, bg=bg, padx=18, pady=16)
    outer.pack(fill='both', expand=True)
    tk.Label(outer, text='AIN generation in progress', bg=bg, fg=fg,
             font=('Segoe UI', 12, 'bold'), anchor='w').pack(fill='x')
    editor._generator_progress_seed_text = (
        f'Seed X={float(cx):.3f}, Y={float(cy):.3f}   |   '
        f'Radius {float(radius):.1f} m')
    tk.Label(outer, text=editor._generator_progress_seed_text,
             bg=bg, fg=dim, font=('Consolas', 9), anchor='w').pack(
                 fill='x', pady=(2, 12))

    editor._generator_progress_phase_var = tk.StringVar(
        value='Starting generator...')
    editor._generator_progress_detail_var = tk.StringVar(
        value='Preparing worker thread')
    editor._generator_progress_elapsed_var = tk.StringVar(
        value='Elapsed 0.0 s')
    tk.Label(outer, textvariable=editor._generator_progress_phase_var,
             bg=bg, fg=fg, font=('Segoe UI', 10, 'bold'), anchor='w').pack(
                 fill='x')
    tk.Label(outer, textvariable=editor._generator_progress_detail_var,
             bg=bg, fg=dim, font=('Segoe UI', 9), anchor='w', justify='left',
             wraplength=480).pack(fill='x', pady=(2, 8))

    style_name = 'AINGenerator.Horizontal.TProgressbar'
    try:
        style = ttk.Style(win)
        style.configure(style_name, troughcolor=panel, bordercolor=border,
                        background='#00b84a', lightcolor='#00b84a',
                        darkcolor='#00a03f')
    except Exception:
        pass
    bar = ttk.Progressbar(outer, orient='horizontal', mode='determinate',
                          maximum=100.0, length=500, style=style_name)
    bar.pack(fill='x', pady=(0, 6))
    editor._generator_progress_bar = bar
    tk.Label(outer, textvariable=editor._generator_progress_elapsed_var,
             bg=bg, fg=dim, font=('Consolas', 9), anchor='w').pack(fill='x')

    tk.Label(outer, text='Live details (selectable)', bg=bg, fg=fg,
             font=('Segoe UI', 9, 'bold'), anchor='w').pack(
                 fill='x', pady=(12, 4))
    report = tk.Text(outer, height=13, width=76, wrap='word', bg=panel, fg=fg,
                     insertbackground=fg, selectbackground='#2878b8',
                     selectforeground='white', highlightbackground=border,
                     highlightcolor=border, relief='solid', bd=1,
                     font=('Consolas', 8), padx=7, pady=6, cursor='xterm')
    report.pack(fill='both', expand=True)
    report.configure(state='disabled')
    report.bind('<Control-a>', editor._select_all_generator_progress_text)
    report.bind('<Control-A>', editor._select_all_generator_progress_text)
    editor._generator_progress_report_box = report

    buttons = tk.Frame(outer, bg=bg)
    buttons.pack(fill='x', pady=(9, 0))
    copy_btn = tk.Button(buttons, text='Copy details', width=14,
                         command=editor._copy_generator_progress_details)
    copy_btn.pack(side='left')
    close_btn = tk.Button(buttons, text='Close', width=10, state='disabled',
                          command=editor._close_generator_progress_window)
    close_btn.pack(side='right')
    cancel_btn = tk.Button(buttons, text='Cancel', width=10,
                           command=editor._cancel_generator_process)
    cancel_btn.pack(side='right', padx=(0, 6))
    editor._generator_progress_copy_button = copy_btn
    editor._generator_progress_close_button = close_btn
    editor._generator_progress_cancel_button = cancel_btn
    tk.Label(outer, text='Select text and press Ctrl+C, or use Copy details.',
             bg=bg, fg=dim, font=('Segoe UI', 8), anchor='w').pack(
                 fill='x', pady=(6, 0))

    win.update_idletasks()
    try:
        x = editor.winfo_rootx() + (
            editor.winfo_width() - win.winfo_width()) // 2
        y = editor.winfo_rooty() + (
            editor.winfo_height() - win.winfo_height()) // 2
        win.geometry(f'+{max(0, x)}+{max(0, y)}')
    except Exception:
        pass
    try:
        win.lift()
    except Exception:
        pass
    editor._update_generator_progress(
        0, 'Starting generator', 'Preparing worker thread')
    editor._tick_generator_progress_clock()


def compose_report(editor):
    """Return the current selectable progress report text."""
    now = (getattr(editor, '_generator_progress_finished_at', None)
           or time.perf_counter())
    total = max(0.0, now - (
        getattr(editor, '_generator_progress_started_at', now) or now))
    phase_time = max(0.0, now - (
        getattr(editor, '_generator_progress_phase_started_at', now) or now))
    percent = float(getattr(editor, '_generator_progress_percent', 0.0) or 0.0)
    phase = str(getattr(editor, '_generator_progress_phase_name', '') or '')
    try:
        detail = editor._generator_progress_detail_var.get()
    except Exception:
        detail = ''
    lines = [
        'AIN GENERATION PROGRESS',
        editor._generator_progress_seed_text,
        '',
        f'Progress:           {percent:.1f}%',
        f'Total elapsed:      {total:.2f} s',
        f'Current phase time: {phase_time:.2f} s',
        f'Current phase:      {phase}',
        f'Details:            {detail}',
        '',
        'COMPLETED PHASES',
    ]
    lines.extend(editor._generator_progress_history or ['(none yet)'])
    return '\n'.join(lines)


def refresh_report(editor, force=False):
    """Refresh the selectable report without disturbing an active selection."""
    report_text = compose_report(editor)
    editor._generator_progress_report_text = report_text
    box = getattr(editor, '_generator_progress_report_box', None)
    if box is None:
        return
    now = time.perf_counter()
    last_draw = float(getattr(
        editor, '_generator_progress_report_last_draw', 0.0) or 0.0)
    if not force and now - last_draw < 0.75:
        return
    if report_text == getattr(
            editor, '_generator_progress_report_drawn_text', None):
        return
    try:
        if box.tag_ranges('sel'):
            return
        box.configure(state='normal')
        box.delete('1.0', 'end')
        box.insert('1.0', report_text)
        box.configure(state='disabled')
        editor._generator_progress_report_drawn_text = report_text
        editor._generator_progress_report_last_draw = now
    except Exception:
        try:
            box.configure(state='disabled')
        except Exception:
            pass


def select_all(editor, event=None):
    """Select all text in the progress report widget."""
    box = getattr(editor, '_generator_progress_report_box', None)
    if box is not None:
        try:
            box.tag_add('sel', '1.0', 'end-1c')
            box.mark_set('insert', '1.0')
            box.see('1.0')
            box.focus_set()
        except Exception:
            pass
    return 'break'


def copy_details(editor):
    """Copy the current progress report to the clipboard."""
    refresh_report(editor, force=True)
    text_value = str(getattr(editor, '_generator_progress_report_text', '') or '')
    try:
        editor.clipboard_clear()
        editor.clipboard_append(text_value)
        editor.update_idletasks()
        button = getattr(editor, '_generator_progress_copy_button', None)
        if button is not None:
            button.configure(text='Copied')

            def _restore_copy_label():
                if getattr(editor, '_generator_progress_copy_button', None) is button:
                    try:
                        button.configure(text='Copy details')
                    except Exception:
                        pass

            editor.after(1200, _restore_copy_label)
    except Exception:
        editor.bell()


def update(editor, percent, phase, detail=''):
    """Update progress state, widgets, and the selectable report."""
    now = time.perf_counter()
    old_phase = getattr(editor, '_generator_progress_phase_name', None)
    old_detail = str(
        getattr(editor, '_generator_progress_phase_detail', '') or '')
    if old_phase and phase != old_phase:
        started = getattr(
            editor, '_generator_progress_phase_started_at', now) or now
        duration = max(0.0, now - started)
        line = f'{old_phase:<34} {duration:7.2f} s'
        editor._generator_progress_history.append(line)
        if old_detail:
            editor._generator_progress_history.append(f'  {old_detail}')
        editor._generator_progress_phase_started_at = now
    elif not old_phase:
        editor._generator_progress_phase_started_at = now
    editor._generator_progress_phase_name = str(phase)
    editor._generator_progress_phase_detail = str(detail or '')
    editor._generator_progress_percent = max(
        0.0, min(100.0, float(percent)))
    try:
        editor._generator_progress_phase_var.set(str(phase))
        editor._generator_progress_detail_var.set(str(detail or ''))
        editor._generator_progress_bar['value'] = editor._generator_progress_percent
    except Exception:
        pass
    refresh_report(editor)


def tick(editor):
    """Update elapsed-time text and schedule the next clock tick."""
    win = getattr(editor, '_generator_progress_window', None)
    if win is None:
        return
    now = (getattr(editor, '_generator_progress_finished_at', None)
           or time.perf_counter())
    total = max(0.0, now - (
        getattr(editor, '_generator_progress_started_at', now) or now))
    phase = max(0.0, now - (
        getattr(editor, '_generator_progress_phase_started_at', now) or now))
    percent = float(getattr(editor, '_generator_progress_percent', 0.0) or 0.0)
    try:
        editor._generator_progress_elapsed_var.set(
            f'Progress {percent:5.1f}%   |   Elapsed {total:7.1f} s   |   '
            f'Current phase {phase:7.1f} s')
    except Exception:
        pass
    refresh_report(editor)
    editor._generator_progress_timer_job = editor.after(100, lambda: tick(editor))


def finish(editor, detail='Generated graph applied to the editor'):
    """Mark generation complete and enable the close button."""
    update(editor, 100, 'Generation complete', detail)
    editor._generator_progress_finished_at = time.perf_counter()
    editor._generator_progress_phase_started_at = editor._generator_progress_finished_at
    win = getattr(editor, '_generator_progress_window', None)
    try:
        win.grab_release()
    except Exception:
        pass
    try:
        editor._generator_progress_close_button.configure(state='normal')
        editor._generator_progress_cancel_button.configure(state='disabled')
        win.protocol('WM_DELETE_WINDOW', editor._close_generator_progress_window)
        win.lift()
    except Exception:
        pass
    refresh_report(editor, force=True)



def show_scan_hud(editor):
    try:
        old = getattr(editor, '_scan_hud', None)
        if old is not None and old.winfo_exists():
            old.destroy()
    except Exception:
        pass

    parent = getattr(editor, '_canvas_frame', None) or editor
    light_panels = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
    bg = '#f7f7f7' if light_panels else '#050505'
    fg = '#111111' if light_panels else '#f0f0f0'
    dim = '#555555' if light_panels else '#888888'
    border = '#808080' if light_panels else '#404040'
    title_bg = '#ccd3ff' if light_panels else '#202a55'
    title_fg = '#111111' if light_panels else '#dfe8ff'
    blue_bg = '#c9dcf4' if light_panels else '#1f6fb2'
    blue_fg = '#003366' if light_panels else '#ffffff'
    red_bg = '#ffe1e1' if light_panels else '#400000'
    red_fg = '#cc0000' if light_panels else '#ff6666'
    cb_bg = '#ffffff' if light_panels else '#111111'
    cb_fg = fg

    hud = tk.Frame(parent, bg=bg, bd=0, highlightthickness=1, highlightbackground=border)
    editor._scan_hud = hud
    hud.place(relx=0.5, rely=0.43, anchor='center', width=390)
    hud.lift()

    title = tk.Frame(hud, bg=title_bg)
    title.pack(fill='x')
    tk.Label(title, text='Scan Applier', bg=title_bg, fg=title_fg,
             font=('Consolas', 10, 'bold'), anchor='w').pack(side='left', padx=6, pady=3)
    tk.Button(title, text='X', command=editor._exit_ain_pass_mode,
              bg=red_bg, fg=red_fg, activebackground=red_bg, activeforeground=red_fg,
              font=('Consolas', 10, 'bold'), relief='flat', padx=6, pady=0).pack(side='right', padx=3, pady=2)

    body = tk.Frame(hud, bg=bg)
    body.pack(fill='both', expand=True, padx=12, pady=10)

    # Step 1: choose the local scan center after Apply Scan.
    tk.Label(body, text='1. Select scan origin', bg=bg, fg=fg,
             font=('Consolas', 9, 'bold'), anchor='w').pack(fill='x')
    tk.Label(body, text='   Click Apply Scan, then move the disc.', bg=bg, fg=dim,
             font=('Consolas', 9), anchor='w').pack(fill='x')
    tk.Label(body, text='   LMB anywhere = scan center.', bg=bg, fg=dim,
             font=('Consolas', 9), anchor='w').pack(fill='x')

    tk.Frame(body, bg=border, height=1).pack(fill='x', pady=(6, 6))

    # Step 2: Scan radius
    tk.Label(body, text='2. Scan radius (meters)', bg=bg, fg=fg,
             font=('Consolas', 9, 'bold'), anchor='w').pack(fill='x')
    row_r = tk.Frame(body, bg=bg)
    row_r.pack(fill='x', pady=(2, 0))
    tk.Label(row_r, text='Radius:', bg=bg, fg=dim, font=('Consolas', 9)).pack(side='left')
    if not hasattr(editor, '_scan_radius_var'):
        editor._scan_radius_var = tk.StringVar(value='50')
    tk.Entry(row_r, textvariable=editor._scan_radius_var, width=6,
             bg=(('#ffffff' if light_panels else '#111111')), fg=fg,
             insertbackground=fg, relief='solid', bd=1,
             font=('Consolas', 9)).pack(side='left', padx=4)
    tk.Label(row_r, text='m', bg=bg, fg=dim,
             font=('Consolas', 9)).pack(side='left')

    # Application density: percentage of eligible B12 nodes selected.
    if not hasattr(editor, '_scan_percentage_var'):
        editor._scan_percentage_var = tk.IntVar(value=50)
    pct_row = tk.Frame(body, bg=bg)
    pct_row.pack(fill='x', pady=(5, 0))
    tk.Label(pct_row, text='Application %:', bg=bg, fg=dim,
             font=('Consolas', 9)).pack(side='left')
    pct_value = tk.Label(pct_row, text=f'{int(editor._scan_percentage_var.get())}%',
                         bg=bg, fg=fg, font=('Consolas', 9), width=5, anchor='e')
    pct_value.pack(side='right')

    def _scan_pct_changed(value):
        q = max(0, min(100, int(round(float(value) / 5.0) * 5)))
        if int(editor._scan_percentage_var.get()) != q:
            editor._scan_percentage_var.set(q)
        pct_value.configure(text=f'{q}%')

    tk.Scale(body, from_=0, to=100, orient='horizontal',
             variable=editor._scan_percentage_var, command=_scan_pct_changed,
             resolution=5, showvalue=False, bg=bg, fg=fg,
             troughcolor=('#d0d0d0' if light_panels else '#303030'),
             highlightthickness=0, bd=0, length=245).pack(fill='x', pady=(0, 3))

    tk.Frame(body, bg=border, height=1).pack(fill='x', pady=(6, 6))

    # Channels
    tk.Label(body, text='3. Channels', bg=bg, fg=fg,
             font=('Consolas', 9, 'bold'), anchor='w').pack(fill='x')
    tk.Label(body, text='0x32 distance field — always runs', bg=bg, fg=dim,
             font=('Consolas', 9), anchor='w').pack(fill='x')

    cb1 = tk.Checkbutton(body, text='b12 context', variable=editor._scan_b12_var,
                         bg=bg, fg=cb_fg, selectcolor=cb_bg, activebackground=bg,
                         activeforeground=cb_fg, font=('Consolas', 9), anchor='w')
    cb1.pack(fill='x', pady=(2, 0))
    row_b12 = tk.Frame(body, bg=bg)
    row_b12.pack(fill='x', padx=(18, 0), pady=(1, 1))
    tk.Label(row_b12, text='Requested flags:', bg=bg, fg=dim,
             font=('Consolas', 9)).pack(side='left')
    editor._scan_b12_entry = tk.Entry(
        row_b12, textvariable=editor._scan_b12_value_var, width=5,
        bg=(('#ffffff' if light_panels else '#111111')), fg=fg,
        insertbackground=fg, relief='solid', bd=1,
        font=('Consolas', 9))
    editor._scan_b12_entry.pack(side='left', padx=4)

    editor._scan_b12_preview_label = tk.Label(
        row_b12, text='', bg=bg, fg=dim,
        font=('Consolas', 9), anchor='w')
    editor._scan_b12_preview_label.pack(side='left', padx=(6, 0))

    tk.Checkbutton(body, text='  Exact byte (disable per-node B12 bits)',
                   variable=editor._scan_b12_exact_var,
                   bg=bg, fg=cb_fg, selectcolor=cb_bg, activebackground=bg,
                   activeforeground=cb_fg, font=('Consolas', 9), anchor='w').pack(fill='x')

    add_row = tk.Frame(body, bg=bg)
    add_row.pack(fill='x', padx=(18, 0), pady=(3, 1))
    tk.Label(
        add_row, text='Additional values:', bg=bg, fg=dim,
        font=('Consolas', 9)
    ).pack(side='left')
    editor._scan_b12_additional_entry = tk.Entry(
        add_row, textvariable=editor._scan_b12_additional_values_var,
        width=20,
        bg=(('#ffffff' if light_panels else '#111111')), fg=fg,
        insertbackground=fg, relief='solid', bd=1,
        font=('Consolas', 9))
    editor._scan_b12_additional_entry.pack(side='left', padx=(6, 6), fill='x', expand=True)
    tk.Checkbutton(
        body, text='  Additional values are exact bytes',
        variable=editor._scan_b12_additional_exact_var,
        bg=bg, fg=cb_fg, selectcolor=cb_bg, activebackground=bg,
        activeforeground=cb_fg, font=('Consolas', 9), anchor='w'
    ).pack(fill='x')

    editor._scan_b12_additional_preview_label = tk.Label(
        body, text='', bg=bg, fg=dim,
        font=('Consolas', 9), anchor='w', justify='left',
        wraplength=345)
    editor._scan_b12_additional_preview_label.pack(
        fill='x', padx=(36, 0), pady=(0, 1))

    tk.Label(body,
             text='  separate values with commas: 12,24 or 12 , 24',
             bg=bg, fg=dim, font=('Consolas', 9), anchor='w').pack(fill='x')
    tk.Label(body,
             text='  normal: requested flags + per-node precision / bit1',
             bg=bg, fg=dim, font=('Consolas', 9), anchor='w').pack(fill='x')

    def _scan_preview_outputs(value):
        value = int(value)
        # bit0 is always per-node. bit1 is per-node only when the requested
        # byte does not already require it.
        base = value & 0xFE
        values = {base, base | 0x01}
        if (value & 0x02) == 0 and (value & 0x0C):
            values.add(base | 0x02)
            values.add(base | 0x03)
        return sorted(v for v in values if 0 <= v <= 255)

    def _scan_parse_additional_values(raw):
        raw = str(raw)
        if not raw.strip():
            return [], None

        # Commas delimit values. Whitespace around a value is fine, but
        # whitespace INSIDE a numeric token is deliberately rejected:
        # "12 , 24" is valid; "1 2, 2  4" is not.
        tokens = raw.split(',')
        values = []
        for token in tokens:
            stripped = token.strip()
            if not stripped:
                return None, token
            if re.fullmatch(r'(?:0[xX][0-9A-Fa-f]+|[0-9]+)', stripped) is None:
                return None, stripped
            try:
                value = int(stripped, 0)
            except Exception:
                return None, stripped
            if not (0 <= value <= 255):
                return None, stripped
            values.append(value)
        return values, None

    def _scan_refresh_b12_preview(*_ignored):
        normal_entry_bg = '#ffffff' if light_panels else '#111111'
        invalid_entry_bg = '#ffd4d4' if light_panels else '#5a1717'

        try:
            primary = int(str(editor._scan_b12_value_var.get()).strip(), 0)
            primary_valid = 0 <= primary <= 255
        except Exception:
            primary = None
            primary_valid = False
        try:
            editor._scan_b12_entry.configure(
                bg=normal_entry_bg if primary_valid else invalid_entry_bg)
        except Exception:
            pass

        primary_text = ''
        if primary_valid and not bool(editor._scan_b12_exact_var.get()):
            primary_text = '→ ' + ', '.join(
                str(v) for v in _scan_preview_outputs(primary))
        editor._scan_b12_preview_label.configure(text=primary_text)

        additional, bad_token = _scan_parse_additional_values(
            editor._scan_b12_additional_values_var.get())
        additional_valid = additional is not None
        try:
            editor._scan_b12_additional_entry.configure(
                bg=normal_entry_bg if additional_valid else invalid_entry_bg)
        except Exception:
            pass

        add_text = ''
        if additional_valid and additional:
            if bool(editor._scan_b12_additional_exact_var.get()):
                add_text = 'Additional outputs: ' + ', '.join(
                    str(v) for v in additional)
            else:
                chunks = []
                for value in additional:
                    family = ', '.join(
                        str(v) for v in _scan_preview_outputs(value))
                    chunks.append(f'{value} → {family}')
                add_text = 'Additional: ' + '   |   '.join(chunks)

            # Campaign-like weighting: requests in the 0x80+ range share
            # a small ~3% allocation instead of competing equally with
            # ordinary B12 families.
            _preview_requests = [primary] if primary_valid else []
            _preview_requests.extend(additional)
            if any(v >= 128 for v in _preview_requests):
                add_text += '   |   128+ = strong-context overlay'
        elif not additional_valid:
            add_text = 'Unrecognized additional B12 value'
        editor._scan_b12_additional_preview_label.configure(text=add_text)

    for _scan_var in (
        editor._scan_b12_value_var,
        editor._scan_b12_exact_var,
        editor._scan_b12_additional_values_var,
        editor._scan_b12_additional_exact_var,
    ):
        try:
            _scan_var.trace_add('write', _scan_refresh_b12_preview)
        except Exception:
            pass
    _scan_refresh_b12_preview()

    # Existing 0/1 nodes are protected by default. These optional parity overrides
    # force matching existing even/odd B12 values to the requested byte exactly
    # (for example, Odd enabled allows an existing B12=1 to become exactly 12).
    parity_row = tk.Frame(body, bg=bg)
    parity_row.pack(fill='x', padx=(18, 0), pady=(2, 0))
    tk.Label(parity_row, text='Force parity to requested byte:', bg=bg, fg=dim,
             font=('Consolas', 9)).pack(anchor='w')
    parity_opts = tk.Frame(body, bg=bg)
    parity_opts.pack(fill='x', padx=(30, 0), pady=(0, 1))
    tk.Checkbutton(parity_opts, text='Even values', variable=editor._scan_b12_replace_even_var,
                   bg=bg, fg=cb_fg, selectcolor=cb_bg, activebackground=bg,
                   activeforeground=cb_fg, font=('Consolas', 9)).pack(side='left')
    tk.Checkbutton(parity_opts, text='Odd values', variable=editor._scan_b12_replace_odd_var,
                   bg=bg, fg=cb_fg, selectcolor=cb_bg, activebackground=bg,
                   activeforeground=cb_fg, font=('Consolas', 9)).pack(side='left', padx=(14, 0))
    tk.Label(body, text='  off by default; forces matching parity to the requested byte',
             bg=bg, fg=dim, font=('Consolas', 9), anchor='w').pack(fill='x')

    cb2 = tk.Checkbutton(body, text='b16 direction', variable=editor._scan_b16_var,
                         bg=bg, fg=cb_fg, selectcolor=cb_bg, activebackground=bg,
                         activeforeground=cb_fg, font=('Consolas', 9), anchor='w')
    cb2.pack(fill='x', pady=(2, 0))
    tk.Label(body, text='  largest-gap direction (campaign formula)', bg=bg, fg=dim,
             font=('Consolas', 9), anchor='w').pack(fill='x')

    tk.Frame(body, bg=border, height=1).pack(fill='x', pady=(6, 6))

    tk.Button(body, text='Apply Scan', command=editor._scan_apply,
              bg=blue_bg, fg=blue_fg, activebackground=blue_bg, activeforeground=blue_fg,
              font=('Consolas', 10, 'bold'), relief='flat', padx=8, pady=3).pack(fill='x')

    n_eligible = sum(1 for nd in editor.nodes if len(nd.neighbors) <= 15) if editor.nodes else 0
    n_have = sum(1 for nd in editor.nodes if getattr(nd, 'scan_0x32', None) is not None) if editor.nodes else 0
    tk.Label(body, text=f'{n_eligible} eligible, {n_have} scanned',
             bg=bg, fg=dim, font=('Consolas', 7), anchor='w').pack(fill='x', pady=(4, 0))
