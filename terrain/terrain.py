"""Terrain format parsing, spatial metadata, and height sampling for DFBHD.

Pure terrain helpers — no Tk, no mutable module state, no game-path dependency.
Functions that need debug logging accept an optional dbg callback.
"""
import math
from pathlib import Path


DEBUG_TERRAIN = False
TERRAIN_TILE_WORLD_SIZE = 512.0   # MED UI grid shows 512m terrain tile blocks; tune later if needed.


def _ensure_ext(name, ext):
    if not name:
        return ''
    s = str(name).strip().strip('"')
    if not s:
        return ''
    ext = ext.lower().lstrip('.')
    if s.lower().endswith('.' + ext):
        return s
    return s + '.' + ext


def _parse_simple_script_kv(text):
    """Parse NovaLogic-ish text files: key value, quoted or unquoted."""
    import shlex
    out = {}
    for raw in str(text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('//') or line.startswith('#') or line.startswith(';'):
            continue
        try:
            toks = shlex.split(line, comments=False, posix=True)
        except Exception:
            toks = line.replace('"', '').split()
        if len(toks) >= 2:
            out[toks[0].lower()] = toks[1]
    return out


def _expand_polytrn_sectors_med(rows, sectorcount, wrapx, wrapy):
    """Expand .TRN polytrn_sectors to MED's fixed 16x16 table.

    Mirrors FUN_004462E0:
      - each parsed row is padded to 16 columns
      - if wrapx == 0, repeat last written column
      - if wrapx != 0, wrap from row start
      - missing rows are padded to 16 rows
      - if wrapy == 0, repeat previous row
      - if wrapy != 0, wrap from first parsed rows
    """
    n = max(1, min(16, int(sectorcount or 1)))
    out = [[0 for _ in range(16)] for __ in range(16)]

    # Copy parsed rows, n columns each.
    parsed_rows = min(16, len(rows or []))
    for r in range(parsed_rows):
        src = list(rows[r] or [])
        for c in range(min(n, len(src), 16)):
            out[r][c] = int(src[c])

        # X extension.
        if n < 16:
            if int(wrapx or 0) == 0:
                fill = out[r][n - 1] if n > 0 else 0
                for c in range(n, 16):
                    out[r][c] = fill
            else:
                for c in range(n, 16):
                    out[r][c] = out[r][c - n]

    # Y extension.
    if parsed_rows < 16:
        if int(wrapy or 0) == 0:
            fill_row = out[parsed_rows - 1][:] if parsed_rows > 0 else [0] * 16
            for r in range(parsed_rows, 16):
                out[r] = fill_row[:]
        else:
            for r in range(parsed_rows, 16):
                out[r] = out[r - parsed_rows][:] if parsed_rows > 0 else [0] * 16

    return out


def _terrain_quadrant_bounds(tile_id, src_w, src_h):
    """Return (x0, y0, x1, y1) for supported MED quadrant ids.

    Current editor terrain rendering/sampling only understands the explicit
    1..4 quadrant layout seen in MED terrain consumers:
      1 TL, 2 BL, 3 TR, 4 BR.

    Any other sector value is treated as unsupported instead of being wrapped
    into fake quadrants, because that was inventing terrain that does not
    necessarily exist in MED.
    """
    half_w = max(1, src_w // 2)
    half_h = max(1, src_h // 2)
    if tile_id == 1:
        return (0, 0, half_w, half_h)
    if tile_id == 2:
        return (0, half_h, half_w, src_h)
    if tile_id == 3:
        return (half_w, 0, src_w, half_h)
    if tile_id == 4:
        return (half_w, half_h, src_w, src_h)
    return None


def _terrain_tile_family(tile_id, dbg=None):
    """Return (family_name, quadrant_id) for supported MED terrain tile ids.

    Confirmed from FUN_00420d00:
    - 0 and 5 are empty/default slots
    - 1..4 select quadrants from the currently selected primary family
    - 6..9 select quadrants from a fixed secondary family
    """
    if tile_id in (0, 5):
        return ('empty', 0)
    if 1 <= tile_id <= 4:
        return ('primary', tile_id)
    if 6 <= tile_id <= 9:
        return ('secondary', tile_id - 5)
    # Unknown tile ID — print once so we can identify Team Sabre tile range
    if dbg:
        dbg('TERRAIN', f'[TERRAIN_TILE] unknown tile_id={tile_id}', once_key=('unknown_tile', tile_id))
    return (None, 0)


TERRAIN_DEBUG_MODES = (
    ('current', 'Terrain: Current'),
    ('primary_only', 'Terrain: Primary'),
    ('detail_only', 'Terrain: Detail'),
    ('secondary_only', 'Terrain: Secondary'),
    ('multiply', 'Terrain: Multiply'),
    ('blend', 'Terrain: Blend'),
)

# Terrain phase modes are separate from image/debug-composition modes.
# They change only which world-coordinate phase is used for sector/table
# selection.  They do NOT scale or stretch the terrain image.
TERRAIN_PHASE_MODES = (
    # Legacy/current keeps the earlier editor behavior for quick comparison.
    ('current', 'Phase: Legacy'),
    # MED cooker lookup reconstructed from FUN_0043B690/FUN_004462E0/FUN_00420D00.
    # It keeps 512m draw cells, uses the expanded 16x16 sector table directly,
    # applies MED wrap/clamp masks, and uses the real cooked-buffer quadrant slots.
    ('medcook_yneg', 'Phase: MED cook -Y'),
    # Same table logic, but without the -Y coordinate sign. This is only a fast
    # axis sanity check because editor/map Y convention may differ from the helper.
    ('medcook_ypos', 'Phase: MED cook +Y'),
    # Old coordinate sign but no 15-row inversion. Useful to catch row-order only.
    ('legacy_noinvert', 'Phase: No row flip'),
    ('med32', 'Phase: MED32'),
)


def _terrain_phase_mode_label(mode_name):
    for key, label in TERRAIN_PHASE_MODES:
        if key == mode_name:
            return label
    return 'Phase: Current'


def _med_fixed32_meter_phase(meters):
    """Return MED-like signed 32-bit fixed-point meter phase.

    DFBHD world positions are commonly stored as int32 fixed point where
    1 meter = 65536 units.  A full int32 rollover is therefore 65536m.
    This helper emulates that phase without changing actual object/node
    coordinates or screen placement.
    """
    try:
        fixed = int(round(float(meters) * 65536.0)) & 0xFFFFFFFF
        if fixed >= 0x80000000:
            fixed -= 0x100000000
        return fixed / 65536.0
    except Exception:
        return float(meters or 0.0)


def _terrain_phase_coord(meters, phase_mode):
    mode = str(phase_mode or 'current').strip().lower()
    if mode == 'med32':
        return _med_fixed32_meter_phase(meters)
    try:
        return float(meters)
    except Exception:
        return 0.0


def _terrain_sector_lookup_raw_index(raw_idx, world_center, terrain_origin, sector_world, phase_mode):
    """Return the raw sector-table lookup index for terrain phase tests.

    Important: this does not change where the 512m tile is drawn and does not
    resize source imagery. It only changes which 16x16 terrain sector-table
    entry supplies the tile/quadrant for that 512m draw cell.
    """
    import math as _math
    mode = str(phase_mode or 'current').strip().lower()
    try:
        raw_idx = int(raw_idx)
    except Exception:
        raw_idx = 0
    try:
        world_center = float(world_center)
        terrain_origin = float(terrain_origin)
        sector_world = float(sector_world)
    except Exception:
        return raw_idx
    if sector_world <= 0:
        return raw_idx

    if mode == 'current':
        return raw_idx

    if mode == 'med32':
        phase = _terrain_phase_coord(world_center, 'med32')
        return int(_math.floor((phase - terrain_origin) / sector_world))

    if mode in ('shift3', 'raw_shift3'):
        # 8 normal 512m sectors per terrain-table step = 4096m per table cell.
        return raw_idx >> 3
    if mode in ('shift4', 'raw_shift4'):
        # 16 normal 512m sectors per terrain-table step = 8192m per table cell.
        return raw_idx >> 4

    if mode.startswith('macro64_4k'):
        # Diagnostic for DVD1: keep 512m texture scale, but choose the table
        # cell from a 65536m macro phase split into 16 cells of 4096m.
        # Offset variants let us test alignment without hardcoding a final fix.
        offset = 0.0
        if mode.endswith('_s8192'):
            offset = 8192.0
        elif mode.endswith('_s16384'):
            offset = 16384.0
        elif mode.endswith('_s32768'):
            offset = 32768.0
        rel = (world_center - terrain_origin + offset) % 65536.0
        return int(_math.floor(rel / 4096.0))

    return raw_idx



def _terrain_is_medcook_phase(phase_mode):
    mode = str(phase_mode or 'current').strip().lower()
    return mode in ('medcook_yneg', 'medcook_ypos', 'legacy_noinvert')


def _terrain_medcook_visible_ranges(wx_min, wx_max, wy_min, wy_max, sp, phase_mode):
    """Return raw 512m sector ranges and cell->world bounds for MED-cooker modes.

    Reconstructed from MED terrain chain:
      FUN_004462E0 expands TRN sectors to a 16x16 table and produces masks.
      FUN_00420D00 indexes that table directly; it does not do 15-row inversion.
      FUN_00421130 shows the terrain helper using -Y before the sector shift.

    The returned y-bounds are sorted as (world_min, world_max) so the normal
    screen projection code can still paste correctly.
    """
    import math as _math
    sector_world = float(sp.get('sector_world', 512.0) or 512.0)
    ox, oy = sp.get('origin', (-4.0, -4.0)) or (-4.0, -4.0)
    ox = float(ox); oy = float(oy)
    mode = str(phase_mode or 'current').strip().lower()

    def x_bounds(raw_col):
        x0 = (ox + raw_col) * sector_world
        return x0, x0 + sector_world

    if mode == 'medcook_yneg':
        # MED helper: sector_y = ((-world_y) >> 25) - origin_y.
        raw_row0 = int(_math.floor((-wy_max) / sector_world - oy))
        raw_row1 = int(_math.floor((-wy_min) / sector_world - oy))
        def y_bounds(raw_row):
            # raw sector covers -y in [oy+row, oy+row+1].
            y0 = -((oy + raw_row + 1.0) * sector_world)
            y1 = -((oy + raw_row) * sector_world)
            return (min(y0, y1), max(y0, y1))
        def local_y(world_y, raw_row):
            return ((-float(world_y)) / sector_world) - (oy + raw_row)
    else:
        # Positive-Y variant. legacy_noinvert uses this too, but table rows are
        # not inverted like the old renderer.
        raw_row0 = int(_math.floor(wy_min / sector_world - oy))
        raw_row1 = int(_math.floor(wy_max / sector_world - oy))
        def y_bounds(raw_row):
            y0 = (oy + raw_row) * sector_world
            return y0, y0 + sector_world
        def local_y(world_y, raw_row):
            return (float(world_y) / sector_world) - (oy + raw_row)

    raw_col0 = int(_math.floor(wx_min / sector_world - ox))
    raw_col1 = int(_math.floor(wx_max / sector_world - ox))
    return raw_col0, raw_col1, raw_row0, raw_row1, x_bounds, y_bounds, local_y


def _terrain_medcook_source_y_span(vis_y0, vis_y1, raw_row, local_y_func, bh, yneg_mode):
    """Convert visible world-y span to source crop y span for a 512px quadrant."""
    import math as _math
    # Screen top is vis_y1 in this editor's top-down projection.
    if yneg_mode:
        top_v = local_y_func(vis_y1, raw_row)
        bot_v = local_y_func(vis_y0, raw_row)
    else:
        # Existing editor convention had to flip image Y for positive world Y.
        top_v = 1.0 - local_y_func(vis_y1, raw_row)
        bot_v = 1.0 - local_y_func(vis_y0, raw_row)
    a = max(0.0, min(1.0, top_v))
    b = max(0.0, min(1.0, bot_v))
    if b < a:
        a, b = b, a
    y0 = max(0, min(bh - 1, int(_math.floor(a * bh))))
    y1 = max(y0 + 1, min(bh, int(_math.ceil(b * bh))))
    return y0, y1

def _terrain_debug_mode_label(mode_name):
    for key, label in TERRAIN_DEBUG_MODES:
        if key == mode_name:
            return label
    return 'Terrain: Current'


def _terrain_debug_compose_image(primary_image, secondary_image, mode_name, cache_dict=None):
    """Return the terrain image to use for the requested debug mode.

    `current` preserves the existing family-driven logic and returns None so
    callers can continue their normal per-family selection.
    """
    mode = str(mode_name or 'current').strip().lower()
    if mode == 'current':
        return None
    if primary_image is None and secondary_image is None:
        return None
    if mode == 'primary_only':
        return primary_image or secondary_image
    if mode in ('detail_only', 'secondary_only'):
        return secondary_image or primary_image
    if primary_image is None:
        return secondary_image
    if secondary_image is None:
        return primary_image

    try:
        from PIL import Image, ImageChops
    except Exception:
        return primary_image

    if secondary_image.size != primary_image.size:
        try:
            secondary_image = secondary_image.resize(primary_image.size, Image.BILINEAR)
        except Exception:
            secondary_image = secondary_image.resize(primary_image.size)

    cache = cache_dict if isinstance(cache_dict, dict) else None
    cache_key = (mode, id(primary_image), id(secondary_image), primary_image.size, secondary_image.size)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    try:
        if mode == 'multiply':
            composed = ImageChops.multiply(primary_image, secondary_image)
        elif mode == 'blend':
            composed = Image.blend(primary_image, secondary_image, 0.5)
        else:
            composed = primary_image
    except Exception:
        composed = primary_image

    if cache is not None:
        cache[cache_key] = composed
    return composed


def _terrain_debug_report_lines(info):
    sp = (info or {}).get('spatial') or {}
    fields = (info or {}).get('trn_fields') or {}
    rows = sp.get('sectors_raw') or []
    lines = [
        'Terrain debug report',
        '=' * 40,
        f"TRN: {Path(str((info or {}).get('terrain_trn') or '')).name}",
        f"Terrain name: {(info or {}).get('terrain_name') or ''}",
        f"Colormap: {(info or {}).get('colormap_file') or ''}",
        f"Detailmap: {(info or {}).get('detailmap_file') or ''}",
        f"Depthmap: {(info or {}).get('depthmap_file') or ''}",
        f"Tilestrip: {(info or {}).get('tilestrip_file') or ''}",
        f"Tileinfo: {(info or {}).get('tileinfo_file') or ''}",
        f"Polydata: {fields.get('polytrn_polydata') or fields.get('polydata') or ''}",
        f"Detail density: {fields.get('polytrn_detaildensity') or fields.get('detaildensity') or ''}",
        f"Water raw/world: {(info or {}).get('water_level_raw')} / {_terrain_water_height_world(info)}",
        f"Water source: {(info or {}).get('water_source') or ''}",
        f"BMS water raw @0x98: {(info or {}).get('bms_water_level_raw')}",
        f"Terrain phase mode: {_terrain_phase_mode_label((info or {}).get('_phase_mode') or 'current')}",
        f"Origin: {sp.get('origin')}",
        f"Wrap X/Y: {sp.get('wrapx')} / {sp.get('wrapy')}",
        f"Sectorcount: {sp.get('sectorcount')}",
        f"World bounds: ({sp.get('world_x0')}, {sp.get('world_y0')}) .. ({sp.get('world_x1')}, {sp.get('world_y1')})",
        f"Approximate mode: {(info or {}).get('approximate_mode')}",
        f"Approximate reason: {(info or {}).get('approximate_reason') or ''}",
        '',
        'Authored sector rows:',
    ]
    for idx, row in enumerate(rows):
        try:
            rendered = ' '.join(str(int(v)) for v in row)
        except Exception:
            rendered = str(row)
        lines.append(f'{idx:02d}: {rendered}')
    return lines


def _parse_trn_spatial_metadata(trn_text):
    """Extract terrain spatial metadata from .TRN.

    MED stores a sector table as a fixed 16x16 map after parsing.  The written
    sectorcount/sector rows are only the authored part; FUN_004462E0 expands
    them using wrapx/wrapy.
    """
    import shlex
    meta = {
        'sectorcount': 8,
        'origin': (-4.0, -4.0),
        'sector_world': 512.0,
        'wrapx': 0,
        'wrapy': 0,
        'sectors_raw': [],
        'sectors': [],
    }
    for raw in str(trn_text or '').splitlines():
        line = raw.split(';', 1)[0].strip()
        if not line or line.startswith('//') or line.startswith('#'):
            continue
        try:
            toks = shlex.split(line, comments=False, posix=True)
        except Exception:
            toks = line.replace('"', '').split()
        if not toks:
            continue
        key = toks[0].lower()
        if key == 'polytrn_sectorcount' and len(toks) >= 2:
            try:
                meta['sectorcount'] = max(1, min(16, int(float(toks[1]))))
            except Exception:
                pass
        elif key == 'polytrn_origin' and len(toks) >= 3:
            try:
                meta['origin'] = (float(toks[1]), float(toks[2]))
            except Exception:
                pass
        elif key == 'polytrn_wrapx' and len(toks) >= 2:
            try:
                meta['wrapx'] = int(float(toks[1]))
            except Exception:
                pass
        elif key == 'polytrn_wrapy' and len(toks) >= 2:
            try:
                meta['wrapy'] = int(float(toks[1]))
            except Exception:
                pass
        elif key == 'polytrn_sectors' and len(toks) >= 2:
            row = []
            for t in toks[1:]:
                try:
                    row.append(int(float(t)))
                except Exception:
                    pass
            if row:
                meta['sectors_raw'].append(row)

    n = int(meta.get('sectorcount') or 8)
    meta['sectors'] = _expand_polytrn_sectors_med(
        meta.get('sectors_raw') or [],
        n,
        int(meta.get('wrapx') or 0),
        int(meta.get('wrapy') or 0)
    )

    # MED sector coordinates use sectorcount/origin for authored terrain, but
    # drawing can address the expanded 16x16 table.  Keep both extents.
    meta['world_size'] = 16.0 * float(meta['sector_world'])
    meta['authored_world_size'] = float(n) * float(meta['sector_world'])
    meta['world_x0'] = float(meta['origin'][0]) * float(meta['sector_world'])
    meta['world_y0'] = float(meta['origin'][1]) * float(meta['sector_world'])
    meta['world_x1'] = meta['world_x0'] + meta['world_size']
    meta['world_y1'] = meta['world_y0'] + meta['world_size']
    return meta


def _extract_cpt_heightmap(cpt_raw, log=lambda msg: None):
    """Extract the 1024x1024 uint16 height payload from a CPT file.

    CPT1 stores the height array immediately after its ``DPTH`` tag.  Older
    editor builds guessed a smooth-looking 2 MiB window and selected 0x100 for
    dvdg1.cpt; the real DPTH payload starts at 0xA4.  That 92-byte / 46-sample
    phase error displaced relief while remaining hard to notice on dvd1.

    Prefer the structural chunk marker.  Retain the bounded plausibility probe
    only as a compatibility fallback for an untagged variant.
    """
    if not cpt_raw:
        return None, {'reason': 'empty CPT'}

    import array, time, struct

    data = cpt_raw
    need = 1024 * 1024 * 2
    size = len(data)
    if size < need + 4:
        return None, {'reason': 'cpt smaller than heightmap', 'size': size}

    dpth_marker = data.find(b'DPTH')
    if dpth_marker >= 0:
        off = dpth_marker + 4
        if off + need <= size:
            arr = array.array('H')
            arr.frombytes(data[off:off + need])
            import sys
            if sys.byteorder != 'little':
                arr.byteswap()
            mn = min(arr)
            mx = max(arr)
            info = {
                'offset': off,
                'marker_offset': dpth_marker,
                'min_raw': mn,
                'max_raw': mx,
                'range_raw': mx - mn,
                'scale': 'raw/256.0 meters',
                'exact_chunk': 'DPTH',
                'fast_probe': False,
            }
            log(
                f"[TERRAIN] CPT DPTH heightmap off=0x{off:x} "
                f"min={mn} max={mx} range={mx - mn}"
            )
            return arr, info
        log(
            f"[TERRAIN] CPT DPTH marker at 0x{dpth_marker:x} is truncated; "
            "trying compatibility probe"
        )

    t0 = time.perf_counter()
    budget_s = 0.35  # keep MIS load responsive
    max_candidates = 320

    # Likely locations:
    # - right after common small headers
    # - right after 4-byte chunk tags
    # - 4K/64K boundaries
    raw_offsets = [
        0, 4, 8, 12, 16, 0x20, 0x40, 0x80, 0x100, 0x200, 0x400, 0x800,
        0x1000, 0x2000, 0x4000, 0x8000, 0x10000,
    ]

    max_off = size - need
    # Add coarse aligned candidates. This is intentionally sparse.
    step = 0x1000
    raw_offsets.extend(range(0, max_off + 1, step))
    raw_offsets.extend(o + 4 for o in range(0, max_off + 1, step))

    # Preserve order, remove invalid/dupes.
    seen = set()
    offsets = []
    for off in raw_offsets:
        if 0 <= off <= max_off and off not in seen:
            seen.add(off)
            offsets.append(off)
        if len(offsets) >= max_candidates:
            break

    def sample_score(start):
        vals = []
        diffs = []
        prev_row_first = None
        # 16x16 sample grid = cheap.
        for sy in range(0, 1024, 64):
            row_base = start + sy * 1024 * 2
            row_prev = None
            first = None
            for sx in range(0, 1024, 64):
                pos = row_base + sx * 2
                if pos + 2 > size:
                    return None
                v = struct.unpack_from('<H', data, pos)[0]
                vals.append(v)
                if first is None:
                    first = v
                if row_prev is not None:
                    diffs.append(abs(v - row_prev))
                row_prev = v
            if prev_row_first is not None and first is not None:
                diffs.append(abs(first - prev_row_first))
            prev_row_first = first

        if not vals:
            return None
        mn, mx = min(vals), max(vals)
        rng = mx - mn
        if rng <= 4:
            return None
        avg_diff = (sum(diffs) / len(diffs)) if diffs else 999999.0

        # CPT height raw appears to convert roughly as raw/256 meters.
        # Score smooth, terrain-like maps higher than compressed/image/noise data.
        score = 0.0
        if 100 <= mn <= 20000:
            score += 1.0
        if 300 <= mx <= 30000:
            score += 1.0
        if 32 <= rng <= 16000:
            score += 2.5
        elif 16 < rng < 32:
            score += 1.5
        if avg_diff < 500:
            score += 4.0
        elif avg_diff < 1500:
            score += 2.0
        elif avg_diff < 4000:
            score += 0.5
        return score, mn, mx, rng, avg_diff

    best = None
    tested = 0
    for off in offsets:
        if time.perf_counter() - t0 > budget_s:
            break
        tested += 1
        sc = sample_score(off)
        if sc is None:
            continue
        score, mn, mx, rng, avg_diff = sc
        # Slightly prefer offsets immediately after a possible 4-byte chunk tag.
        if off >= 4:
            score += 0.15
        if best is None or score > best[0]:
            best = (score, off, mn, mx, rng, avg_diff)

    elapsed = time.perf_counter() - t0
    if best is None or best[0] < 2.5:
        return None, {
            'reason': 'heightmap not found within fast probe budget',
            'tested': tested,
            'elapsed_ms': round(elapsed * 1000, 1),
            'size': size,
            'best': None if best is None else {
                'score': round(best[0], 3),
                'offset': best[1],
                'min': best[2],
                'max': best[3],
                'range': best[4],
                'avg_diff': round(best[5], 3),
            }
        }

    score, off, mn, mx, rng, avg_diff = best

    # Decode full map only once we have a plausible offset.
    arr = array.array('H')
    arr.frombytes(data[off:off + need])
    import sys
    if sys.byteorder != 'little':
        arr.byteswap()

    info = {
        'offset': off,
        'score': round(score, 3),
        'min_raw': mn,
        'max_raw': mx,
        'range_raw': rng,
        'avg_diff': round(avg_diff, 3),
        'tested': tested,
        'elapsed_ms': round(elapsed * 1000, 1),
        'scale': 'raw/256.0 meters',
        'fast_probe': True,
    }
    log(f"[TERRAIN] CPT heightmap fast candidate off=0x{off:x} min={mn} max={mx} range={rng} score={score:.2f} tested={tested} in {elapsed*1000:.1f}ms")
    return arr, info


def _build_cpt_chd_images(heightmap, dbg=None):
    """Build MED-style H (height) and D (relief/flatness) terrain images.

    C is the normal colormap already loaded by the terrain path.  H displays
    DPTH as green at the scale observed in MED (uint16 raw >> 7).  D uses the
    two colors emitted by MED: black for locally flat samples and RGB 64 for
    any immediate elevation change.
    """
    if heightmap is None or len(heightmap) < 1024 * 1024:
        return None, None
    try:
        from PIL import Image

        side = 1024
        count = side * side
        height_bytes = bytearray(count)
        relief_bytes = bytearray(count)

        for i in range(count):
            height_bytes[i] = min(255, int(heightmap[i]) >> 7)

        mask = side - 1
        for y in range(side):
            row = y * side
            up = ((y - 1) & mask) * side
            down = ((y + 1) & mask) * side
            for x in range(side):
                i = row + x
                value = heightmap[i]
                if (value != heightmap[row + ((x - 1) & mask)] or
                        value != heightmap[row + ((x + 1) & mask)] or
                        value != heightmap[up + x] or
                        value != heightmap[down + x]):
                    relief_bytes[i] = 64

        green = Image.frombytes('L', (side, side), bytes(height_bytes))
        zero = Image.new('L', (side, side), 0)
        height_image = Image.merge('RGB', (zero, green, zero))
        depth_image = Image.frombytes('L', (side, side), bytes(relief_bytes)).convert('RGB')
        return height_image, depth_image
    except Exception as exc:
        if dbg:
            dbg('TERRAIN', f'[TERRAIN] failed to build C/H/D images: {exc}',
                 once_key=('terrain_chd_build_error', str(exc)))
        return None, None


def _polytrn_sector_index_med(raw_idx, wrap_enabled):
    """MED sector index behavior from FUN_00420D00/FUN_004462E0.

    The expanded table is 16x16. Coordinates outside 0..15 do not mean
    "terrain missing". If wrap is disabled, MED clamps to the nearest edge.
    If wrap is enabled, MED wraps with & 0xf.
    """
    try:
        i = int(raw_idx)
    except Exception:
        i = 0
    if int(wrap_enabled or 0) != 0:
        return i & 0xf
    if i < 0:
        return 0
    if i > 15:
        return 15
    return i


def _terrain_sample_height(info, wx, wy, dbg=None):
    """Sample terrain height in editor world meters, or return None.

    Height sampling follows the same MED-cooker phase used by the
    visual terrain backdrop. The previous node-placement path still
    used the old positive-Y/15-row-inverted lookup, so nodes on visual mountains
    could sample a flat CPT quadrant instead.

    Confirmed terrain chain:
      FUN_004462E0 -> expanded 16x16 sector table + wrap/clamp masks
      FUN_0043B690 -> loads 1024x1024 CPT/terrain buffers
      FUN_00421130 -> terrain helper indexes Y as -world_y
      FUN_00420D00 -> visual renderer indexes the same expanded sector table

    Height conversion follows the current reconstructed CPT behavior:
      CPT raw uint16 -> raw/256.0 world meters.
    """
    if not isinstance(info, dict):
        return None
    hm = info.get('heightmap')
    if hm is None or len(hm) < 1024 * 1024:
        return None
    sp = info.get('spatial') or {}
    sectors = sp.get('sectors') or []
    if not sectors:
        return None

    import math
    sector_world = float(sp.get('sector_world', 512.0) or 512.0)
    if sector_world <= 0:
        return None

    ox, oy = sp.get('origin', (-4.0, -4.0)) or (-4.0, -4.0)
    try:
        ox = float(ox); oy = float(oy)
    except Exception:
        ox, oy = -4.0, -4.0

    wrapx = int(sp.get('wrapx') or 0)
    wrapy = int(sp.get('wrapy') or 0)
    phase_mode = str(info.get('_phase_mode') or 'medcook_yneg').strip().lower()

    # X is the same in both reconstructed paths.  Keep local pixel phase from
    # raw world-space sector position, while table index applies MED clamp/wrap.
    fx_sector = float(wx) / sector_world - ox
    raw_col = math.floor(fx_sector)
    col = int(_polytrn_sector_index_med(raw_col, wrapx))
    u = fx_sector - math.floor(fx_sector)

    if phase_mode in ('medcook_yneg', 'medcook_ypos', 'legacy_noinvert'):
        # Match the visual terrain cooker lookup. The important default is
        # medcook_yneg, which is the one that visually aligns DVD1/MIS13.
        if phase_mode == 'medcook_yneg':
            fy_sector = (-float(wy)) / sector_world - oy
            py_flip = False     # FUN_00421130-style -Y: local v maps directly.
        else:
            fy_sector = float(wy) / sector_world - oy
            py_flip = True      # positive-Y visual path uses 1-v.
        raw_row = math.floor(fy_sector)
        table_row = int(_polytrn_sector_index_med(raw_row, wrapy))
        v = fy_sector - math.floor(fy_sector)
    else:
        # Legacy/current behavior retained for phase comparisons.
        x0 = float(sp.get('world_x0', -4.0 * sector_world))
        y0 = float(sp.get('world_y0', -4.0 * sector_world))
        fx_sector = (float(wx) - x0) / sector_world
        fy_sector = (float(wy) - y0) / sector_world
        raw_col = math.floor(fx_sector)
        raw_world_row = math.floor(fy_sector)
        col = int(_polytrn_sector_index_med(raw_col, wrapx))
        world_row = int(_polytrn_sector_index_med(raw_world_row, wrapy))
        table_row = 15 - world_row
        u = fx_sector - math.floor(fx_sector)
        v = fy_sector - math.floor(fy_sector)
        py_flip = True

    if table_row < 0 or table_row >= len(sectors):
        return None
    try:
        tile_id = int(sectors[table_row][col])
    except Exception:
        return None
    if tile_id <= 0:
        return None

    family_name, quad_id = _terrain_tile_family(tile_id, dbg=dbg)
    if family_name == 'empty':
        return None
    if family_name is None:
        if dbg:
            dbg(
                'TERRAIN',
                f'[TERRAIN] unsupported terrain tile id {tile_id} at row={table_row} col={col}; '
                'height sample skipped',
                once_key=('unsupported_tile_height', tile_id, table_row, col)
            )
        return None

    bounds = _terrain_quadrant_bounds(quad_id, 1024, 1024)
    if bounds is None:
        return None
    bx, by, _, _ = bounds

    px = bx + u * 512.0
    py = by + ((1.0 - v) if py_flip else v) * 512.0

    x = int(math.floor(px)) & 0x3ff
    y = int(math.floor(py)) & 0x3ff
    tx = px - math.floor(px)
    ty = py - math.floor(py)

    x1 = (x + 1) & 0x3ff
    y1 = (y + 1) & 0x3ff

    h00 = hm[y * 1024 + x]
    h10 = hm[y * 1024 + x1]
    h01 = hm[y1 * 1024 + x]
    h11 = hm[y1 * 1024 + x1]

    h0 = h00 * (1.0 - tx) + h10 * tx
    h1 = h01 * (1.0 - tx) + h11 * tx
    raw = h0 * (1.0 - ty) + h1 * ty

    return raw / 256.0


def _terrain_water_height_world(info):
    """Return the authored water plane in editor-world metres."""
    if not isinstance(info, dict):
        return None

    direct = info.get('water_height_world')
    if direct not in (None, ''):
        try:
            return float(direct)
        except Exception:
            pass

    raw = info.get('water_level_raw')
    if raw not in (None, ''):
        try:
            return float(raw) * 0.5
        except Exception:
            pass

    raw = (info.get('mis_fields') or {}).get('water_level')
    if raw not in (None, ''):
        try:
            return float(raw) * 0.5
        except Exception:
            pass

    fields = info.get('trn_fields') or {}
    raw = fields.get('water_height')
    if raw in (None, ''):
        raw = fields.get('water_level')
    if raw not in (None, ''):
        try:
            return float(raw) * 0.5
        except Exception:
            pass
    return None
