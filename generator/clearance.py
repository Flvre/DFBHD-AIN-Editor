"""Adaptive Radius System — precomputed transition matrix, palette-based resolver,
node-aware placement, toggleable large radius extension, and collision helper."""

import math

from shared.nav_helpers import _gen_measure_clearance

# ── Palette definitions ──────────────────────────────────────────────────────
ADAPTIVE_PALETTE_BASE  = [4, 8, 12, 16, 18, 20, 24, 28, 32, 36, 42, 48]
ADAPTIVE_PALETTE_LARGE = [4, 8, 12, 16, 18, 20, 24, 28, 32, 36, 42, 48, 56, 64, 72, 80, 96, 112, 128]
ADAPTIVE_PALETTE_XLARGE = [4, 8, 12, 16, 18, 20, 24, 28, 32, 36, 42, 48, 56, 64, 72, 80, 96, 112, 128, 160, 192, 224, 255]
ADAPTIVE_OVERLAP       = 1.15   # 15% radius overlap allowed
ADAPTIVE_DIST_STEP     = 0.5    # distance bucket size in meters
ADAPTIVE_DIST_MAX      = 12.0   # maximum neighbour search distance in meters
ADAPTIVE_SEARCH_MULT   = 2.5    # search radius = target_radius * this


def adaptive_clearance_b15(wx, wy, blocked_fn, palette):
    """
    Measure XY clearance at (wx, wy) using 8-direction sampling.
    Returns the largest palette b15 whose radius fits the clearance.
    Returns palette[0] (minimum) if clearance is very tight.
    """
    DIRS = [
        (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0),  (0.0, -1.0),
        (0.707, 0.707), (0.707,-0.707),
        (-0.707, 0.707), (-0.707,-0.707),
    ]
    _ray_clearances = getattr(blocked_fn, 'ray_clearances', None)
    if callable(_ray_clearances):
        clearances = list(_ray_clearances(wx, wy, DIRS, 6.0))
    else:
        clearances = []
        for dx, dy in DIRS:
            d = _gen_measure_clearance(blocked_fn, wx, wy, dx, dy,
                                       step=0.5, max_d=6.0)
            clearances.append(d)

    clearances.sort()
    effective_clearance = clearances[1] if len(clearances) >= 2 else clearances[0]

    max_radius = effective_clearance * 1.08  # 8% wall overlap allowed
    max_b15_float = max_radius / (0x1000 / 65536.0)

    result = palette[0]  # minimum fallback
    for v in palette:
        if v <= max_b15_float:
            result = v
    return result


def _build_adaptive_matrix(palette):
    """
    Pre-compute transition matrix.
    Returns dict: (neighbour_b15, dist_bucket_idx) -> max_candidate_b15 or None
    dist_bucket_idx = int(distance / ADAPTIVE_DIST_STEP) clamped to range.
    """
    n_buckets = int(ADAPTIVE_DIST_MAX / ADAPTIVE_DIST_STEP)
    matrix = {}

    def b15_to_r(b15):
        return b15 * 0x1000 / 65536.0

    def largest_fit(max_r):
        result = None
        for v in palette:
            if v <= max_r / (0x1000 / 65536.0):
                result = v
        return result

    for nb15 in palette:
        nr = b15_to_r(nb15)
        for bi in range(n_buckets + 1):
            d = (bi + 1) * ADAPTIVE_DIST_STEP
            max_r = d * ADAPTIVE_OVERLAP - nr
            if max_r <= 0:
                matrix[(nb15, bi)] = None
            else:
                matrix[(nb15, bi)] = largest_fit(max_r)

    return matrix

# Build both matrices at import time — cheap, runs once
_ADAPTIVE_MATRIX_BASE   = _build_adaptive_matrix(ADAPTIVE_PALETTE_BASE)
_ADAPTIVE_MATRIX_LARGE  = _build_adaptive_matrix(ADAPTIVE_PALETTE_LARGE)
_ADAPTIVE_MATRIX_XLARGE = _build_adaptive_matrix(ADAPTIVE_PALETTE_XLARGE)


def _adaptive_target_b15(base_b15, use_large=False, use_xlarge=False, values_locked=False):
    """Return the radius ceiling used for one adaptive placement."""
    base_b15 = int(base_b15)
    if not bool(values_locked):
        if bool(use_xlarge):
            return int(ADAPTIVE_PALETTE_XLARGE[-1])
        if bool(use_large):
            return int(ADAPTIVE_PALETTE_LARGE[-1])
    return base_b15


def adaptive_resolve_b15(wx, wy, nodes, target_b15, use_large=False, use_xlarge=False, blocked_fn=None):
    """
    Resolve the largest valid b15 for a candidate at (wx, wy).
    Checks existing nodes as neighbours — no geometry/collision check.
    """
    if use_xlarge:
        palette = ADAPTIVE_PALETTE_XLARGE
        matrix  = _ADAPTIVE_MATRIX_XLARGE
    elif use_large:
        palette = ADAPTIVE_PALETTE_LARGE
        matrix  = _ADAPTIVE_MATRIX_LARGE
    else:
        palette = ADAPTIVE_PALETTE_BASE
        matrix  = _ADAPTIVE_MATRIX_BASE

    # Clamp target to palette
    target_b15 = min(target_b15, palette[-1])

    # Search radius based on target
    target_r     = target_b15 * 0x1000 / 65536.0
    search_r     = target_r * ADAPTIVE_SEARCH_MULT

    # Find all neighbours within search radius
    candidate_b15 = target_b15
    n_buckets     = int(ADAPTIVE_DIST_MAX / ADAPTIVE_DIST_STEP)

    for n in nodes:
        d = math.hypot(n.x - wx, n.y - wy)
        if d >= search_r:
            continue
        if d < 0.01:
            # Coincident — can't place here
            return None

        # Snap to distance bucket
        bi = min(int(d / ADAPTIVE_DIST_STEP), n_buckets)
        nb15 = int(getattr(n, 'b15', 36))

        # Find nearest palette value for neighbour b15
        nb15_key = min(palette, key=lambda v: abs(v - nb15))

        max_b15 = matrix.get((nb15_key, bi))
        if max_b15 is None:
            return None  # can't place anything near this neighbour
        candidate_b15 = min(candidate_b15, max_b15)

    # Apply clearance constraint if blocked_fn provided
    if blocked_fn is not None:
        clearance_b15 = adaptive_clearance_b15(wx, wy, blocked_fn, palette)
        candidate_b15 = min(candidate_b15, clearance_b15)

    # Snap result down to palette
    result = None
    for v in palette:
        if v <= candidate_b15:
            result = v

    if result is None:
        return None

    # Minimum floor: Large/XL modes skip placement if the space is too tight
    if use_xlarge and result < 36:
        return None
    if use_large and result < 28:
        return None

    return result


# ── Collision helper ────────────────────────────────────────────────────────

def _make_clearance_blocked_fn(entities, game_path, margin=0.0, *,
                               get_cmodel_segments=None):
    """Build the manual Adaptive Radius collision helper.

    The old implementation expanded every cached CModel segment into world
    space, inserted those segments into a broad 8 m spatial hash, then sampled
    96 individual points for every node placement.  Dense campaign maps could
    therefore spend several seconds repeatedly walking the same segment lists.

    Keep the exact same source of truth (already-cached static CModel segments),
    but retain them in model-local space.  The fast ``ray_clearances`` path
    intersects the eight Adaptive Radius probe rays directly with nearby model
    segments, so each nearby segment is visited once instead of being searched
    again for every 0.5 m sample point.

    ``blocked_fn`` remains available as a compatibility fallback for callers
    that need point tests.  Both paths are XY-only, matching the original
    Adaptive Radius behaviour.
    """
    records = []
    _radius_by_model = {}
    _get_segs = get_cmodel_segments or (lambda tid, gp, allow_load=False: None)
    for e in (entities or []):
        if not e.get('is_static'):
            continue
        tid = e.get('type_id')
        if tid is None:
            continue
        raw = _get_segs(tid, game_path, allow_load=False)
        if not raw:
            continue

        model_key = id(raw)
        model_radius = _radius_by_model.get(model_key)
        if model_radius is None:
            r2 = 0.0
            for seg in raw:
                for pt in seg:
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        try:
                            px, py = float(pt[0]), float(pt[1])
                        except Exception:
                            continue
                        r2 = max(r2, px*px + py*py)
            model_radius = math.sqrt(r2)
            _radius_by_model[model_key] = model_radius

        ex, ey = float(e.get('x', 0.0)), float(e.get('y', 0.0))
        hdg = float(e.get('heading', 0.0))
        ca = math.cos(math.radians(-hdg))
        sa = math.sin(math.radians(-hdg))
        records.append((ex, ey, ca, sa, raw, float(model_radius)))

    if not records:
        fn = lambda x, y: False
        fn.ray_clearances = lambda x, y, dirs, max_d: [float(max_d)] * len(dirs)
        fn.record_count = 0
        return fn

    def _dist_point_to_seg(px, py, ax, ay, bx, by):
        dx, dy = bx-ax, by-ay
        l2 = dx*dx + dy*dy
        if l2 < 1e-12:
            return math.hypot(px-ax, py-ay)
        t = max(0.0, min(1.0, ((px-ax)*dx + (py-ay)*dy) / l2))
        return math.hypot(px - (ax+t*dx), py - (ay+t*dy))

    def _world_to_local(ex, ey, ca, sa, x, y):
        dx, dy = x-ex, y-ey
        return ca*dx + sa*dy, -sa*dx + ca*dy

    def blocked_fn(x, y):
        """Compatibility point test without the old huge world spatial hash."""
        x, y = float(x), float(y)
        for ex, ey, ca, sa, raw, model_radius in records:
            dx, dy = x-ex, y-ey
            limit = model_radius + float(margin)
            if dx*dx + dy*dy > limit*limit:
                continue
            lx, ly = _world_to_local(ex, ey, ca, sa, x, y)
            for seg in raw:
                if not isinstance(seg, (list, tuple)) or len(seg) < 2:
                    continue
                a, b = seg[0], seg[1]
                if (not isinstance(a, (list, tuple)) or len(a) < 2 or
                        not isinstance(b, (list, tuple)) or len(b) < 2):
                    continue
                ax, ay = float(a[0]), float(a[1])
                bx, by = float(b[0]), float(b[1])
                if _dist_point_to_seg(lx, ly, ax, ay, bx, by) <= margin:
                    return True
        return False

    def ray_clearances(x, y, dirs, max_d):
        """Return nearest exact segment hit distance for each probe direction."""
        x, y = float(x), float(y)
        max_d = float(max_d)
        clear = [max_d] * len(dirs)
        max_d2 = max_d * max_d
        eps = 1.0e-9

        for ex, ey, ca, sa, raw, model_radius in records:
            cdx, cdy = x-ex, y-ey
            reach = model_radius + max_d + float(margin)
            if cdx*cdx + cdy*cdy > reach*reach:
                continue

            ox, oy = _world_to_local(ex, ey, ca, sa, x, y)
            local_dirs = [
                (ca*float(dx) + sa*float(dy),
                 -sa*float(dx) + ca*float(dy))
                for dx, dy in dirs
            ]

            minx, maxx = ox-max_d, ox+max_d
            miny, maxy = oy-max_d, oy+max_d
            for seg in raw:
                if not isinstance(seg, (list, tuple)) or len(seg) < 2:
                    continue
                a, b = seg[0], seg[1]
                if (not isinstance(a, (list, tuple)) or len(a) < 2 or
                        not isinstance(b, (list, tuple)) or len(b) < 2):
                    continue
                ax, ay = float(a[0]), float(a[1])
                bx, by = float(b[0]), float(b[1])
                if ((ax < minx and bx < minx) or (ax > maxx and bx > maxx) or
                        (ay < miny and by < miny) or (ay > maxy and by > maxy)):
                    continue

                sx, sy = bx-ax, by-ay
                qx, qy = ax-ox, ay-oy
                for i, (rx, ry) in enumerate(local_dirs):
                    if clear[i] <= eps:
                        continue
                    den = rx*sy - ry*sx
                    if -eps < den < eps:
                        continue
                    t = (qx*sy - qy*sx) / den
                    if t < -eps or t > clear[i] + eps or t > max_d + eps:
                        continue
                    u = (qx*ry - qy*rx) / den
                    if -eps <= u <= 1.0 + eps:
                        clear[i] = max(0.0, t)

        return clear

    blocked_fn.ray_clearances = ray_clearances
    blocked_fn.record_count = len(records)
    return blocked_fn
