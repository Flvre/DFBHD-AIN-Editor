"""Height-aware line-of-sight helpers for graph-link operations."""

import math


def build_world_collision_segments(
        entities, game_path, get_segments_3d, get_segments_2d,
        excluded_type_ids, xy_from_point):
    """Return collision entries transformed into world space.

    Six-value entries are collision-only 3D segments. Five-value entries are
    legacy 2D fallback segments with the entity origin Z as their reference
    height. The latter are retained only for models without cached 3D data.
    """
    entries = []
    for entity in (entities or []):
        try:
            type_id = int(entity.get('type_id', -1))
        except Exception:
            continue
        if not entity.get('is_static') or type_id in excluded_type_ids:
            continue
        try:
            ex = float(entity.get('x', 0.0))
            ey = float(entity.get('y', 0.0))
            ez = float(entity.get('z', 0.0))
            heading = float(entity.get('heading', 0.0) or 0.0)
        except Exception:
            continue
        angle = math.radians(-heading)
        ca, sa = math.cos(angle), math.sin(angle)
        local_3d = None
        if callable(get_segments_3d):
            try:
                local_3d = get_segments_3d(
                    type_id, game_path, allow_load=True, log=None)
            except Exception:
                local_3d = None
        if local_3d:
            for segment in local_3d:
                try:
                    first, second = segment
                    ox1, oy1, oz1 = map(float, first[:3])
                    ox2, oy2, oz2 = map(float, second[:3])
                    entries.append((
                        ex + ca * ox1 - sa * oy1,
                        ey + sa * ox1 + ca * oy1,
                        ez + oz1,
                        ex + ca * ox2 - sa * oy2,
                        ey + sa * ox2 + ca * oy2,
                        ez + oz2))
                except Exception:
                    continue
            continue
        local_2d = None
        if callable(get_segments_2d):
            try:
                local_2d = get_segments_2d(
                    type_id, game_path, allow_load=False)
            except Exception:
                local_2d = None
        for segment in (local_2d or ()):
            try:
                first, second = segment
                ox1, oy1 = xy_from_point(first)
                ox2, oy2 = xy_from_point(second)
                entries.append((
                    ex + ca * float(ox1) - sa * float(oy1),
                    ey + sa * float(ox1) + ca * float(oy1),
                    ex + ca * float(ox2) - sa * float(oy2),
                    ey + sa * float(ox2) + ca * float(oy2),
                    ez))
            except Exception:
                continue
    return entries


def build_segment_grid(entries, cell_size):
    """Index mixed 3D/fallback collision entries by their XY bounds."""
    grid = {}
    cell = float(cell_size)
    for entry in entries:
        if len(entry) >= 6:
            sx1, sy1, sx2, sy2 = entry[0], entry[1], entry[3], entry[4]
        else:
            sx1, sy1, sx2, sy2 = entry[:4]
        gx0, gx1 = sorted((int(sx1 / cell), int(sx2 / cell)))
        gy0, gy1 = sorted((int(sy1 / cell), int(sy2 / cell)))
        for gx in range(gx0 - 1, gx1 + 2):
            for gy in range(gy0 - 1, gy1 + 2):
                grid.setdefault((gx, gy), []).append(entry)
    return grid


def segment_blocked_3d(ax, ay, az, bx, by, bz, seg_grid, cell_size,
                        z_tolerance=0.35, fallback_z_tolerance=1.8):
    """Return whether AB intersects a collision entry at the same height."""
    d1x, d1y = bx - ax, by - ay
    line_len2 = d1x * d1x + d1y * d1y
    if line_len2 <= 1.0e-12:
        return False
    line_len = math.sqrt(line_len2)
    line_min_x, line_max_x = min(ax, bx), max(ax, bx)
    line_min_y, line_max_y = min(ay, by), max(ay, by)
    cell = float(cell_size)
    steps = max(2, int(line_len / cell) + 1)
    checked_cells = set()
    checked_entries = set()
    for index in range(steps + 1):
        t_line = index / steps
        px, py = ax + d1x * t_line, ay + d1y * t_line
        cx, cy = int(px / cell), int(py / cell)
        for dcx in range(-1, 2):
            for dcy in range(-1, 2):
                key = (cx + dcx, cy + dcy)
                if key in checked_cells:
                    continue
                checked_cells.add(key)
                for entry in seg_grid.get(key, ()):
                    if entry in checked_entries:
                        continue
                    checked_entries.add(entry)
                    if len(entry) >= 6:
                        sx1, sy1, sz1 = entry[0], entry[1], entry[2]
                        sx2, sy2, sz2 = entry[3], entry[4], entry[5]
                    else:
                        sx1, sy1, sx2, sy2, zref = entry
                    if (max(sx1, sx2) < line_min_x
                            or min(sx1, sx2) > line_max_x
                            or max(sy1, sy2) < line_min_y
                            or min(sy1, sy2) > line_max_y):
                        continue
                    sdx, sdy = sx2 - sx1, sy2 - sy1
                    cross = d1x * sdy - d1y * sdx
                    if abs(cross) < 1.0e-10:
                        # A vertical 3D wall edge has a point-like XY
                        # projection. Handle it explicitly instead of dropping
                        # it as a parallel 2D segment.
                        if len(entry) < 6 or (sdx * sdx + sdy * sdy) > 1.0e-10:
                            continue
                        t = ((sx1 - ax) * d1x + (sy1 - ay) * d1y) / line_len2
                        if not 0.01 < t < 0.99:
                            continue
                        distance = abs((sx1 - ax) * d1y - (sy1 - ay) * d1x) / line_len
                        if distance > 0.05:
                            continue
                        line_z = az + t * (bz - az)
                        zlo, zhi = sorted((sz1, sz2))
                        if zlo - z_tolerance <= line_z <= zhi + z_tolerance:
                            return True
                        continue
                    t = ((sx1 - ax) * sdy - (sy1 - ay) * sdx) / cross
                    u = ((sx1 - ax) * d1y - (sy1 - ay) * d1x) / cross
                    if not (0.01 < t < 0.99 and 0.01 < u < 0.99):
                        continue
                    line_z = az + t * (bz - az)
                    if len(entry) >= 6:
                        segment_z = sz1 + u * (sz2 - sz1)
                        if abs(line_z - segment_z) <= z_tolerance:
                            return True
                    elif abs(line_z - zref) <= fallback_z_tolerance:
                        return True
    return False
