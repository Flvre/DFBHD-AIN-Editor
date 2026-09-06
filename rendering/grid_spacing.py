"""2D grid display modes and spacing calculation."""

GRID_MODE_ADAPTIVE = "Adaptive"
GRID_MODE_MED_FIXED = "MED fixed"

_GRID_RENDER_MODE = GRID_MODE_ADAPTIVE
_GRID_FIXED_SPACING_M = 16.0
_GRID_MAX_LINES = 6000

MED_GRID_SPACING_CHOICES = [
    ("4096 m", 4096.0), ("2048 m", 2048.0), ("1024 m", 1024.0),
    ("512 m", 512.0), ("256 m", 256.0), ("128 m", 128.0),
    ("64 m", 64.0), ("32 m", 32.0), ("16 m", 16.0), ("8 m", 8.0),
    ("4 m", 4.0), ("2 m", 2.0), ("1 m", 1.0),
    ("50.000 cm", 0.5), ("25.000 cm", 0.25), ("12.500 cm", 0.125),
    ("6.250 cm", 0.0625), ("3.125 cm", 0.03125),
    ("1.563 cm", 0.015625), ("0.781 cm", 0.0078125),
    ("0.391 cm", 0.00390625),
]
MED_GRID_SPACING_BY_LABEL = dict(MED_GRID_SPACING_CHOICES)


def _adaptive_grid_spacing_for_zoom(zoom):
    if zoom >= 20:   return 5.0
    if zoom >= 5:    return 10.0
    if zoom >= 1:    return 50.0
    return 200.0


def _grid_spacing_for_render(zoom, wx0=None, wy0=None, wx1=None, wy1=None):
    """Return (spacing_m, effective_mode, skipped_reason).

    Fixed cm grids can request millions of lines when zoomed out. Instead of
    freezing the editor, promote to the next coarser MED spacing until the line
    count is safe. The selected value is still kept in the UI/global state.
    """
    if _GRID_RENDER_MODE != GRID_MODE_MED_FIXED:
        return _adaptive_grid_spacing_for_zoom(zoom), GRID_MODE_ADAPTIVE, None

    spacing = max(0.000001, float(_GRID_FIXED_SPACING_M or 16.0))
    if wx0 is None or wy0 is None or wx1 is None or wy1 is None:
        return spacing, GRID_MODE_MED_FIXED, None

    max_lines = int(_GRID_MAX_LINES or 6000)
    choices = [v for _, v in MED_GRID_SPACING_CHOICES]
    safe = spacing
    while True:
        n_v = int(abs(wx1 - wx0) / safe) + 3
        n_h = int(abs(wy1 - wy0) / safe) + 3
        if (n_v + n_h) <= max_lines:
            break
        coarser = [v for v in choices if v > safe]
        if not coarser:
            break
        safe = min(coarser)
    if safe != spacing:
        return safe, GRID_MODE_MED_FIXED, f"requested {spacing:g}m promoted to {safe:g}m for safety"
    return spacing, GRID_MODE_MED_FIXED, None
