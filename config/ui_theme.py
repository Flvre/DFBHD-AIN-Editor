"""UI color constants and theme tokens for the AIN Editor."""

# MED-inspired colours
C_BG        = '#000000'
C_PANEL     = '#3c3c3c'
C_PANEL2    = '#454545'
C_BORDER    = '#555555'
C_TEXT      = '#e0e0e0'
C_DIM       = '#888888'
C_LABEL     = '#ababab'
C_GREEN     = '#00c060'
C_YELLOW    = '#ffd700'
C_ORANGE    = '#ff8c00'
C_BLUE      = '#4488ff'
C_RED       = '#ff4444'
C_ACCENT    = '#0078d4'

# ── Central UI theme tokens ──────────────────────────────────────────────────
UI_THEME_DARK = {
    'window_bg': C_PANEL,
    'panel_bg': C_PANEL,
    'panel_alt_bg': C_PANEL2,
    'control_bg': C_PANEL2,
    'text': C_TEXT,
    'text_dim': C_DIM,
    'label': C_LABEL,
    'border': C_BORDER,
    'selection_bg': C_ACCENT,
    'selection_fg': '#ffffff',
    'menu_bg': C_PANEL,
    'menu_fg': C_TEXT,
    'menu_active_bg': C_ACCENT,
    'menu_active_fg': '#ffffff',
    'menu_disabled_fg': '#888888',
    'popup_bg': '#111111',
    'popup_text_bg': '#111111',
    'button_bg': '#222222',
    'button_fg': C_TEXT,
    'button_active_bg': C_BORDER,
    'button_active_fg': C_TEXT,
}

UI_THEME_LIGHT = {
    'window_bg': '#ededed',
    'panel_bg': '#ededed',
    'panel_alt_bg': '#fcfcfc',
    'control_bg': '#fcfcfc',
    'text': '#1c1c1c',
    'text_dim': '#6b6b6b',
    'label': '#565656',
    'border': '#c2c2c2',
    'selection_bg': '#0078d4',
    'selection_fg': '#ffffff',
    # Non-Windows fallback.  On Windows menu helpers use Tk's semantic
    # SystemMenu/SystemMenuText/SystemHighlight colors instead of these RGBs.
    'menu_bg': '#f0f0f0',
    'menu_fg': '#000000',
    'menu_active_bg': '#0078d4',
    'menu_active_fg': '#ffffff',
    'menu_disabled_fg': '#6d6d6d',
    'popup_bg': '#ffffff',
    'popup_text_bg': '#ffffff',
    'button_bg': '#eeeeee',
    'button_fg': '#555555',
    'button_active_bg': '#e0e0e0',
    'button_active_fg': '#111111',
}

# Legacy color remap kept as a single compatibility bridge for the large body
# of existing Tk widgets.  New/rewritten UI should use the semantic theme tokens
# above directly.
UI_DARK_TO_LIGHT_COLOR_MAP = {
    '#000000': '#ffffff',
    '#181818': '#f5f5f5',
    '#1a0a00': '#fff1e8',
    '#1e1e1e': '#f4f4f4',
    '#202020': '#f1f1f1',
    '#252525': '#ececec',
    '#2a2a2a': '#e9e9e9',
    '#050505': '#fbfbfb',
    '#0a0a0a': '#fafafa',
    '#101010': '#f9f9f9',
    '#111111': '#f8f8f8',
    '#141414': '#f7f7f7',
    '#151515': '#f6f6f6',
    '#1a1a1a': '#f3f3f3',
    '#222222': '#ebebeb',
    '#242424': '#eaeaea',
    '#282828': '#e8e8e8',
    '#2b2b2b': '#e7e7e7',
    '#343434': '#e5e5e5',
    '#3a3a3a': '#e4e4e4',
    '#3f3f3f': '#e1e1e1',
    '#444444': '#dfdfdf',
    '#303030': '#e3e3e3',
    '#330000': '#ffe2e2',
    '#3c3c3c': '#ededed',
    '#404040': '#e6e6e6',
    '#404060': '#c9cee8',
    '#454545': '#fcfcfc',
    '#505050': '#d6d6d6',
    '#555555': '#c2c2c2',
    '#606060': '#cacaca',
    '#666666': '#b6b6b6',
    '#f0c040': '#8a6500',
    '#ffdd00': '#8a6500',
    '#ff4444': '#cc4444',
    '#ababab': '#565656',
    '#aaaaaa': '#5a5a5a',
    '#c8c8c8': '#4a4a4a',
    '#cccccc': '#333333',
    '#dddddd': '#2d2d2d',
    '#888888': '#6b6b6b',
    '#777777': '#6f6f6f',
    '#e0e0e0': '#1c1c1c',
    '#eeeeee': '#1f1f1f',
}

# ── Canvas / scene colours ───────────────────────────────────────────────────
C_CANVAS_BG   = '#000000'
C_GRID        = '#3a2e1e'
C_NODE        = '#00dd44'
C_NODE_SEL    = '#ffdd00'
C_NODE_PREC   = '#ff8800'
C_NODE_B12_12 = '#66ccff'
C_NODE_B12_5  = '#1133cc'
C_NODE_ZONE   = '#4488ff'
C_NODE_B12_2  = '#ccee44'
C_NODE_B12_4  = '#cc66ff'
C_NODE_B12_6  = '#ffcc00'
C_NODE_B12_7  = '#ff4422'
C_NODE_B12_8  = '#00ccbb'
C_NODE_B12_9  = '#00eeff'
C_NODE_B12_10 = '#88ff00'
C_NODE_B12_11 = '#ffaa00'
C_BREACH_ACTION     = '#ff2222'
C_BREACH_TACTICAL   = '#ffff00'
C_B13_COLORS = {
    0: '#ff6600',
    1: '#ff5500',
    2: '#ff4400',
    3: '#ff3300',
    4: '#ee2200',
    5: '#dd1100',
    6: '#ff8800',
    7: '#cc0000',
    8: '#bb0000',
    9: '#aa0000',
}
C_BREACH_ACTIVATION = '#00ffff'
C_EDGE        = '#007722'
C_EDGE_SEL    = '#aaaa00'
C_EDGE_ONEWAY = '#cc3300'
C_RADIUS      = '#00aa33'
C_RADIUS_SEL  = '#ddcc00'
C_ENTITY      = '#ffc800'
C_VEHICLE     = '#ff8c00'
C_NEVER_EXCL  = '#0055aa'

# Zone ID colour palette — 16 distinct colors, zone 0 = standard green
ZONE_COLORS = [
    '#00dd44',  #  0 = standard green (default)
    '#00ffaa',  #  1 = cyan-green
    '#88ff00',  #  2 = yellow-green
    '#44ddff',  #  3 = sky-blue
    '#ff9900',  #  4 = amber
    '#ff44aa',  #  5 = pink
    '#aa44ff',  #  6 = purple
    '#44ffee',  #  7 = aqua
    '#ff4444',  #  8 = red
    '#4488ff',  #  9 = blue
    '#ffff44',  # 10 = yellow
    '#ff6644',  # 11 = red-orange
    '#44ff88',  # 12 = mint
    '#cc88ff',  # 13 = lavender
    '#88ff44',  # 14 = lime
    '#ff8844',  # 15 = orange
]
