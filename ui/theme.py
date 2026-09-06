"""Canvas, panel, top-bar, and menu theme orchestration."""

import ctypes
import os
import tkinter as tk

from config.editor_config import _load_editor_cfg
from config.ui_theme import (C_ACCENT, C_BG, C_BLUE, C_BORDER, C_CANVAS_BG,
    C_DIM, C_ENTITY, C_GREEN, C_GRID, C_LABEL, C_NEVER_EXCL, C_PANEL,
    C_PANEL2, C_TEXT, C_VEHICLE, C_YELLOW, UI_DARK_TO_LIGHT_COLOR_MAP,
    UI_THEME_DARK, UI_THEME_LIGHT, ZONE_COLORS)
from rendering.pil_renderer import _hex

def apply_canvas_theme(editor, light):
    """Swap the PIL canvas palette between dark (default) and light."""
    cfg = _load_editor_cfg()
    r = editor._pil_renderer
    if r is not None:
        if light:
            r.pil_bg      = (255, 255, 255)
            r.pil_grid    = (210, 210, 210)
            custom = cfg.get('entity_color_light')
            r.pil_entity  = _hex(custom) if custom else (70, 70, 70)
            r.pil_vehicle = (150, 85, 10)
            custom_foliage = cfg.get('foliage_color_light')
            r.pil_foliage = _hex(custom_foliage) if custom_foliage else (45, 120, 45)
            r.pil_origin  = (130, 130, 130)
            r.pil_node_id = (40, 40, 40)
        else:
            r.pil_bg      = _hex(C_CANVAS_BG)
            r.pil_grid    = _hex(C_GRID)
            custom = cfg.get('entity_color_dark')
            r.pil_entity  = _hex(custom) if custom else _hex(C_ENTITY)
            r.pil_vehicle = _hex(C_VEHICLE)
            custom_foliage = cfg.get('foliage_color_dark')
            r.pil_foliage = _hex(custom_foliage) if custom_foliage else (60, 180, 60)
            r.pil_origin  = (0x66, 0x66, 0x66)
            r.pil_node_id = (0xe0, 0xe0, 0xe0)
    try:
        editor.canvas.configure(bg=('#ffffff' if light else C_CANVAS_BG))
    except Exception:
        pass
    try:
        r = editor._pil_renderer
        if r is not None:
            r.invalidate_static_entity_layer()
            r._last_img = None
    except Exception:
        pass
    editor._zoom_preview_base = None
    editor.redraw()

def sync_3d_preset_button_theme(editor):
    """Re-apply custom 3D preset button colors after global light/dark retheme.

    The generic theme walker remaps standard Tk widget colors, but the 3D
    preset buttons use custom wrapper-frame hover borders and active states.
    Without this resync, their text colors can stay from the previous theme
    until the next hover event forces a refresh.
    """
    try:
        views = [getattr(editor, '_embedded_3d_view', None)]
        win = getattr(editor, '_wire3d_window', None)
        views.append(getattr(win, 'view', None) if win is not None else None)
        for view in views:
            if view is None:
                continue
            try:
                if hasattr(view, '_update_preset_quick_buttons'):
                    view._update_preset_quick_buttons()
            except Exception:
                pass
            try:
                if hasattr(view, '_update_side_section_styles'):
                    view._update_side_section_styles()
            except Exception:
                pass
    except Exception:
        pass

def on_light_panels_toggle(editor):
    try:
        editor._apply_panel_theme(bool(editor.light_panels.get()))
    except Exception:
        pass

    try:
        editor._sync_all_menu_themes()
        editor._sync_view_menu_checkmark_colors()
    except Exception:
        pass
    try:
        editor._sync_node_help_window_theme()
    except Exception:
        pass
    try:
        editor._refresh_byte_flags_help_popup()
    except Exception:
        pass

    # The AIN Pass chooser is drawn on a Canvas, so the normal widget-tree
    # theme remap cannot recolor it. If it is open while light/dark panels
    # are toggled, rebuild the in-editor overlay so it redraws with the new
    # palette immediately instead of waiting for close/reopen.
    try:
        ov = getattr(editor, '_ain_pass_overlay', None)
        if ov is not None and ov.winfo_exists():
            editor._hide_ain_pass_overlay()
            if bool(editor.dev_mode.get()):
                editor._show_ain_pass_overlay()
    except Exception:
        pass

    # The Cleanup HUD also uses a custom palette. Rebuild it so a live
    # panels/UI theme toggle immediately updates the HUD without requiring
    # the user to leave and re-enter AIN Pass mode.
    try:
        hud = getattr(editor, '_cleanup_hud', None)
        if hud is not None and hud.winfo_exists():
            editor._show_cleanup_hud()
    except Exception:
        pass

    editor._save_ui_preferences()

def ui_theme(editor, light=None):
    """Return the active semantic UI palette."""
    if light is None:
        try:
            light = bool(editor.light_panels.get())
        except Exception:
            light = False
    return UI_THEME_LIGHT if bool(light) else UI_THEME_DARK

def menu_theme(editor, light=None):
    """Return one consistent menu palette for the current platform/theme.

    Light mode on Windows uses Tk's semantic Windows system colours.  This
    lets parent menus and cascaded submenus ask Windows for the same menu,
    text, highlight, and disabled colours instead of approximating them with
    hand-picked RGB values.  Other platforms use the central fallback palette.
    Dark mode remains editor-controlled because classic Tk/Win32 system menu
    colours do not reliably participate in app dark mode.
    """
    if light is None:
        try:
            light = bool(editor.light_panels.get())
        except Exception:
            light = False
    if bool(light) and os.name == 'nt':
        return {
            'bg': 'SystemMenu',
            'fg': 'SystemMenuText',
            'active_bg': 'SystemHighlight',
            'active_fg': 'SystemHighlightText',
            'disabled_fg': 'SystemGrayText',
            'select': 'SystemMenuText',
        }
    pal = editor._ui_theme(light)
    return {
        'bg': pal['menu_bg'],
        'fg': pal['menu_fg'],
        'active_bg': pal['menu_active_bg'],
        'active_fg': pal['menu_active_fg'],
        'disabled_fg': pal['menu_disabled_fg'],
        'select': pal['menu_fg'],
    }

def windows_system_menu_rgb_theme():
    """Resolve classic Win32 menu colours as RGB fallback values.

    Tk's symbolic SystemMenu names are preferred because they preserve the
    semantic link to Windows.  GetSysColor is only the fallback for Tk
    builds that reject those symbolic names.
    """
    if os.name != 'nt':
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32

        def _hex(index):
            value = int(user32.GetSysColor(int(index)))
            r = value & 0xff
            g = (value >> 8) & 0xff
            b = (value >> 16) & 0xff
            return f'#{r:02x}{g:02x}{b:02x}'

        return {
            'bg': _hex(4),             # COLOR_MENU
            'fg': _hex(7),             # COLOR_MENUTEXT
            'active_bg': _hex(13),     # COLOR_HIGHLIGHT
            'active_fg': _hex(14),     # COLOR_HIGHLIGHTTEXT
            'disabled_fg': _hex(17),   # COLOR_GRAYTEXT
            'select': _hex(7),
        }
    except Exception:
        return None

def apply_menu_theme(editor, menu, light=None):
    """Apply the central menu policy to one Tk Menu.

    All menu surfaces use identical border/active geometry.  If a platform
    rejects a Windows semantic colour name, fall back atomically to the
    RGB palette rather than leaving a half-themed menu.
    """
    if menu is None:
        return
    pal = editor._menu_theme(light)
    try:
        is_light = bool(editor.light_panels.get()) if light is None else bool(light)
    except Exception:
        is_light = bool(light)
    try:
        editor._windows_menu_visual_policy.set_dark_mode(not is_light, pal.get('bg'))
    except Exception:
        pass
    opts = dict(
        bg=pal['bg'], fg=pal['fg'],
        activebackground=pal['active_bg'],
        activeforeground=pal['active_fg'],
        disabledforeground=pal['disabled_fg'],
        selectcolor=pal['select'],
        relief='flat', bd=0, activeborderwidth=0,
    )
    try:
        menu.configure(**opts)
    except Exception:
        pal = editor._windows_system_menu_rgb_theme() if is_light else None
        if pal is None:
            fallback = editor._ui_theme(light)
            pal = {
                'bg': fallback['menu_bg'],
                'fg': fallback['menu_fg'],
                'active_bg': fallback['menu_active_bg'],
                'active_fg': fallback['menu_active_fg'],
                'disabled_fg': fallback['menu_disabled_fg'],
                'select': fallback['menu_fg'],
            }
        try:
            menu.configure(
                bg=pal['bg'], fg=pal['fg'],
                activebackground=pal['active_bg'],
                activeforeground=pal['active_fg'],
                disabledforeground=pal['disabled_fg'],
                selectcolor=pal['select'],
                relief='flat', bd=0, activeborderwidth=0)
        except Exception:
            return

    # Menu entries are not a normal Tk child tree on every platform.  Apply
    # the same palette entry-by-entry so a rebuilt cascade cannot drift from
    # its parent even when Tk ignores inherited widget options.
    try:
        end = menu.index('end')
        if end is not None:
            for i in range(int(end) + 1):
                if menu.type(i) == 'separator':
                    continue
                try:
                    menu.entryconfigure(
                        i,
                        background=pal['bg'], foreground=pal['fg'],
                        activebackground=pal['active_bg'],
                        activeforeground=pal['active_fg'])
                except Exception:
                    pass
    except Exception:
        pass

def sync_all_menu_themes(editor):
    """Re-theme every persistent menu and discard dead registry entries."""
    alive = []
    for menu in list(getattr(editor, '_ui_menus', ())):
        try:
            if not bool(menu.winfo_exists()):
                continue
        except Exception:
            continue
        editor._apply_menu_theme(menu)
        alive.append(menu)
    editor._ui_menus = alive

def sync_view_grid_submenu_theme(editor):
    """Compatibility wrapper: MED spacing now uses the shared menu theme."""
    editor._apply_menu_theme(getattr(editor, '_view_grid_submenu', None))

def sync_node_help_window_theme(editor):
    """Apply the active panel theme to the Node Inspector help popup if it is open."""
    win = getattr(editor, '_node_help_win', None)
    if win is None:
        return
    try:
        if not win.winfo_exists():
            return
    except Exception:
        return

    pal = editor._node_help_popup_palette()
    try:
        win.configure(bg=pal['bg'])
    except Exception:
        pass
    for name, opts in (
        ('_node_help_body', dict(bg=pal['bg'])),
    ):
        try:
            getattr(editor, name).configure(**opts)
        except Exception:
            pass
    try:
        editor._node_help_scrollbar.configure(bg=pal['button_bg'], troughcolor=pal['bg'],
                                            activebackground=pal['button_active_bg'])
    except Exception:
        pass
    txt = getattr(editor, '_node_help_text', None)
    if txt is not None:
        try:
            txt.configure(bg=pal['text_bg'], fg=pal['fg'], insertbackground=pal['fg'])
            txt.tag_config('green',  foreground=pal['green'],  font=('Consolas',9,'bold'))
            txt.tag_config('yellow', foreground=pal['yellow'], font=('Consolas',9,'bold'))
            txt.tag_config('ph',     foreground=pal['ph'],     font=('Consolas',9,'italic'))
            txt.tag_config('dim',    foreground=pal['dim'],    font=('Consolas',8))
            txt.tag_config('normal', foreground=pal['fg'],     font=('Consolas',9))
        except Exception:
            pass

def sync_topbar_theme(editor):
    light = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
    if light:
        bar_bg = '#ededed'
        sep_bg = '#c2c2c2'
        neutral_bg = '#fcfcfc'
        neutral_fg = '#1c1c1c'
        dim_fg = '#5f5f5f'
        active_bg = '#c9def4'
        active_fg = '#003a66'
        generate_bg, generate_fg = '#bfe3bf', '#005500'
        ain_pass_bg, ain_pass_fg = '#005a9e', 'white'
        export_bg, export_fg = '#c9dcf4', '#003366'
        breach_bg, breach_fg = '#f4caca', '#8a0000'
        help_bg, help_fg = ('#d8d8d8', '#111111') if getattr(editor, '_help_visible', False) else ('#fcfcfc', '#1c1c1c')
        z_bg, z_fg, z_hl = '#fff0c2', '#7a5200', '#d09a00'
        paint_canvas_bg = neutral_bg
    else:
        bar_bg = C_PANEL
        sep_bg = C_BORDER
        neutral_bg = C_PANEL2
        neutral_fg = C_TEXT
        dim_fg = C_DIM
        active_bg = C_ACCENT
        active_fg = 'white'
        generate_bg, generate_fg = '#006000', 'white'
        ain_pass_bg, ain_pass_fg = '#00437a', 'white'
        export_bg, export_fg = '#2f7fc0', 'white'
        breach_bg, breach_fg = '#550000', '#ff6666'
        help_bg, help_fg = (C_BORDER, 'white') if getattr(editor, '_help_visible', False) else ('#3a3a3a', '#e8e8e8')
        z_bg, z_fg, z_hl = '#5a3b00', '#ffd84a', '#9a6a00'
        paint_canvas_bg = C_PANEL

    def cfg(w, **kw):
        try:
            if w is not None:
                w.configure(**kw)
        except Exception:
            pass

    cfg(getattr(editor, '_topbar_frame', None), bg=bar_bg)
    for sep in getattr(editor, '_topbar_separators', []) or []:
        cfg(sep, bg=sep_bg)

    cfg(getattr(editor, '_generate_btn', None), bg=generate_bg, fg=generate_fg,
        activebackground=generate_bg, activeforeground=generate_fg)
    cfg(getattr(editor, '_ain_pass_btn', None), bg=ain_pass_bg, fg=ain_pass_fg,
        activebackground=ain_pass_bg, activeforeground=ain_pass_fg)
    cfg(getattr(editor, '_export_btn', None), bg=export_bg, fg=export_fg,
        activebackground=export_bg, activeforeground=export_fg)
    cfg(getattr(editor, '_breach_panel_btn', None), bg=breach_bg, fg=breach_fg,
        activebackground=breach_bg, activeforeground=breach_fg)

    in_3d = getattr(editor, '_view_mode', '2d') == '3d'
    view_bg = active_bg if in_3d else neutral_bg
    view_fg = active_fg if in_3d else neutral_fg
    cfg(getattr(editor, '_view3d_toggle_btn', None), bg=view_bg, fg=view_fg,
        activebackground=active_bg, activeforeground=active_fg)

    zone_active = (str(getattr(editor, 'mode', tk.StringVar(value='edit')).get()) == 'zone')
    if light:
        zone_bg = '#c9def4' if zone_active else '#e0e0e0'
        zone_hover_bg = '#bfd4eb'
    else:
        zone_bg = active_bg if zone_active else '#505050'
        zone_hover_bg = active_bg
    zone_fg = active_fg if zone_active else neutral_fg
    cfg(getattr(editor, '_zone_panel_toggle_btn', None), bg=zone_bg, fg=zone_fg,
        activebackground=zone_hover_bg, activeforeground=active_fg)

    cfg(getattr(editor, '_grid_diag_btn', None), bg=neutral_bg, fg=dim_fg,
        activebackground=active_bg, activeforeground=active_fg)
    cfg(getattr(editor, '_topbar_help_btn', None), bg=help_bg, fg=help_fg,
        activebackground=active_bg, activeforeground=active_fg,
        highlightbackground=sep_bg, highlightcolor=sep_bg)
    cfg(getattr(editor, '_coord_label', None), bg=bar_bg, fg=C_GREEN)
    cfg(getattr(editor, '_zoom_label', None), bg=bar_bg, fg=dim_fg)
    cfg(getattr(editor, '_zfilter_top_indicator', None), bg=z_bg, fg=z_fg,
        highlightbackground=z_hl, highlightcolor=z_hl)
    editor._sync_help_panel_theme()

    for btn_ref in getattr(editor, '_mode_btns', {}).values():
        try:
            if isinstance(btn_ref, tuple):
                frame, canvas = btn_ref
                cfg(frame, bg=paint_canvas_bg)
                cfg(canvas, bg=paint_canvas_bg)
            else:
                cfg(btn_ref, bg=neutral_bg, activebackground=active_bg,
                    activeforeground=active_fg)
        except Exception:
            pass
    try:
        if hasattr(editor, '_update_mode_btns'):
            editor._update_mode_btns()
    except Exception:
        pass

def sync_adaptive_radius_theme(editor):
    """Keep the Adaptive radius Inspector labels comfortably readable."""
    light = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
    fg = '#1c1c1c' if light else '#c4c4c4'
    active_fg = '#003a66' if light else '#f0f0f0'
    for name in ('_adaptive_r_btn', '_place_nodes_on_entities_btn', '_adaptive_large_r_btn', '_adaptive_xlarge_r_btn'):
        btn = getattr(editor, name, None)
        if btn is None:
            continue
        try:
            btn.configure(fg=fg, activeforeground=active_fg)
        except Exception:
            pass

def sync_takedown_label_theme(editor):
    """Keep takedown-related inspector labels consistent with other fields."""
    pal = editor._ui_theme()
    fg = pal['text']
    for widget in (
        getattr(editor, '_inspector_field_labels', {}).get('b13'),
        getattr(editor, '_e_b14_label', None),
        getattr(editor, '_inspector_field_labels', {}).get('b17'),
    ):
        if widget is None:
            continue
        try:
            widget.configure(fg=fg)
        except Exception:
            pass

def sync_debug_bar_theme(editor):
    """Keep the debug bar neon in dark mode and readable in light mode."""
    light = bool(getattr(editor, 'light_panels', tk.BooleanVar(value=False)).get())
    bg = '#fff1e8' if light else '#1a0a00'
    select_bg = '#ffe2e2' if light else '#330000'

    bar = getattr(editor, '_debug_bar_frame', None)
    if bar is not None:
        try:
            bar.configure(bg=bg)
        except Exception:
            pass

    title = getattr(editor, '_debug_bar_title', None)
    if title is not None:
        try:
            title.configure(bg=bg, fg=('#a84d00' if light else '#ff8800'))
        except Exception:
            pass

    for btn in getattr(editor, '_debug_bar_color_widgets', ()):
        fg = (getattr(btn, '_debug_light_fg', '#222222') if light
              else getattr(btn, '_debug_dark_fg', '#eeeeee'))
        try:
            btn.configure(bg=bg, fg=fg, selectcolor=select_bg,
                          activebackground=bg, activeforeground=fg)
        except Exception:
            pass

    legend = getattr(editor, '_debug_bar_legend', None)
    if legend is not None:
        try:
            legend.configure(bg=bg, fg=('#555555' if light else '#555555'))
        except Exception:
            pass

    close = getattr(editor, '_debug_bar_close', None)
    if close is not None:
        try:
            close.configure(bg=bg, fg=('#b52a2a' if light else '#ff4444'),
                            activebackground=bg,
                            activeforeground=('#8d1d1d' if light else '#ff7777'))
        except Exception:
            pass
