"""Pure helpers for the 3D wireframe view.

These functions have no view state and do not import the application class.
"""

import math
import re

def canonical_view_preset_name(mode):
    """Return the public preset name used as the config key."""
    m = str(mode or "Normal")
    return {
        "Local Building": "Normal",
        "Interior Focus": "Interior Edit",
        "Navigation X-Ray": "X-Ray",
        "⚠ Full View": "X-Ray",
    }.get(m, m)


def view_preset_setting_names():
    """Settings that built-in presets own and are therefore safe to save.

    Deliberately excludes camera/selection/node-lock/block-filter state and
    global preferences such as the Node Focus color.
    """
    return (
        "show_grid", "ground_mode", "show_node_overlay", "show_radii",
        "show_graph_ends", "show_building_levels", "show_buildings", "show_vehicles",
        "show_decorations", "show_visual_mesh", "show_cmodel_overlay",
        "building_scope", "decoration_scope", "visual_scope",
        "collision_scope", "cmodel_color_mode", "cmodel_detail_mode",
        "tunnel_draw_mode", "tunnel_visual_transform", "context_radius",
        "max_buildings", "max_vehicles", "max_decor",
        "perf_drag_preview", "perf_offscreen_cull", "perf_line_budget",
    )


def draw_screen_dashed_line(draw, a, b, color, width=2, dash=9.0, gap=6.0):
    """Draw one inexpensive dotted/dashed screen-space preview line."""
    try:
        x0, y0 = float(a[0]), float(a[1])
        x1, y1 = float(b[0]), float(b[1])
        dx = x1 - x0
        dy = y1 - y0
        length = math.hypot(dx, dy)
        if length <= 0.5:
            return
        ux = dx / length
        uy = dy / length
        pos = 0.0
        while pos < length:
            end = min(length, pos + dash)
            draw.line(
                (x0 + ux * pos, y0 + uy * pos,
                 x0 + ux * end, y0 + uy * end),
                fill=color, width=width)
            pos += dash + gap
    except Exception:
        pass


def node_focus_hex_to_rgb(value, fallback=(74, 64, 54)):
    try:
        text = str(value or '').strip()
        if re.fullmatch(r'#[0-9A-Fa-f]{6}', text):
            return tuple(int(text[i:i+2], 16) for i in (1, 3, 5))
    except Exception:
        pass
    return tuple(fallback)


def node_focus_rgb_to_hex(rgb):
    try:
        r, g, b = (max(0, min(255, int(v))) for v in rgb)
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return "#4A4036"


def wf_segment_hits_triangle(a, b, triangle):
    """Return True when open segment AB crosses a collision triangle.

    Moller-Trumbore is used as a two-sided test. Intersections extremely
    close to either node endpoint are ignored so a node touching/supporting
    geometry does not make every incident edge look blocked.
    """
    try:
        ax, ay, az = map(float, a)
        bx, by, bz = map(float, b)
        v0, v1, v2 = triangle
        x0, y0, z0 = map(float, v0)
        x1, y1, z1 = map(float, v1)
        x2, y2, z2 = map(float, v2)

        dx, dy, dz = bx-ax, by-ay, bz-az
        seg_len = math.sqrt(dx*dx + dy*dy + dz*dz)
        if seg_len <= 1.0e-7:
            return False

        e1x, e1y, e1z = x1-x0, y1-y0, z1-z0
        e2x, e2y, e2z = x2-x0, y2-y0, z2-z0
        hx = dy*e2z - dz*e2y
        hy = dz*e2x - dx*e2z
        hz = dx*e2y - dy*e2x
        det = e1x*hx + e1y*hy + e1z*hz
        if abs(det) < 1.0e-10:
            return False
        inv_det = 1.0 / det
        sx, sy, sz = ax-x0, ay-y0, az-z0
        u = (sx*hx + sy*hy + sz*hz) * inv_det
        if u < -1.0e-7 or u > 1.0000001:
            return False
        qx = sy*e1z - sz*e1y
        qy = sz*e1x - sx*e1z
        qz = sx*e1y - sy*e1x
        v = (dx*qx + dy*qy + dz*qz) * inv_det
        if v < -1.0e-7 or u + v > 1.0000001:
            return False
        t = (e2x*qx + e2y*qy + e2z*qz) * inv_det
        # Ignore roughly the first/last 3 cm of the link. This prevents
        # endpoint contact with a wall/floor from being treated as a wall
        # penetration while still flagging genuine crossings immediately.
        endpoint_t = min(0.05, 0.03 / seg_len)
        return endpoint_t < t < (1.0 - endpoint_t)
    except Exception:
        return False
