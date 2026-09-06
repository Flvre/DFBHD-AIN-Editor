"""BMS file parser — reads entity tables, resolves terrain Z, detects terrain name."""
import re as _re
import struct, time
from collections import Counter as _ZCtr
from pathlib import Path

from shared.nav_helpers import ENTITY_INVINCIBLE_FLAG_OFFSET, ENTITY_INVINCIBLE_FLAG_MASK
from entity.entity_data import NEVER_EXCLUDE
from entity.entity_classify import _item_category_allows_cmodel
from config.render_config import CMODEL_STATIC_CATEGORIES, CMODEL_SKIP_CATEGORIES
from shared.debug_log import QUIET_DEBUG_LOGS
from resources.items_def import _load_items_def
from terrain.terrain import _ensure_ext
from resources.asset_resolve import _resolve_game_asset_bytes


def parse_bms(path, log=print, *, init_game_path=None, pff_cache=None,
              trace=None):
    _trace = trace or (lambda msg: None)
    _trace(f'parse_bms: opening {path}')
    _t_read = time.perf_counter()
    with open(path, 'rb') as f:
        data = f.read()
    _trace(f'parse_bms: read {len(data):,} bytes in {time.perf_counter() - _t_read:.3f}s')
    if not data.startswith(b'BMS\x11'):
        _trace('parse_bms: invalid BMS header')
        log("ERROR: Not a valid BMS file")
        return [], 18.5
    block_a = struct.unpack_from('<H', data, 0x242)[0]
    block_b = struct.unpack_from('<H', data, 0x246)[0]
    start = 0x268 + block_a + block_b
    log(f"BMS entity_start=0x{start:04x}")
    _trace(f'parse_bms: block_a={block_a} block_b={block_b} entity_start=0x{start:04x}')

    _gp_t0 = time.perf_counter()
    gp = init_game_path(path) if init_game_path else None
    _trace(f'parse_bms: game path resolved to {gp!s} in {time.perf_counter() - _gp_t0:.3f}s')
    item_defs = {}
    if gp:
        try:
            _items_t0 = time.perf_counter()
            item_defs = _load_items_def(gp, log=log, pff_cache=pff_cache) or {}
            _trace(f'parse_bms: items.def ready ({len(item_defs):,} definitions) in {time.perf_counter() - _items_t0:.3f}s')
        except Exception as e:
            log(f"[items.def] category lookup unavailable during BMS parse: {e}")
            item_defs = {}

    static_categories = set(CMODEL_STATIC_CATEGORIES)
    skip_categories = set(CMODEL_SKIP_CATEGORIES)
    RS = 0xB0
    entities = []
    _scan_t0 = time.perf_counter()
    static_from_category = 0
    category_counts = {}
    r = start
    leading_empty_slots = 0
    while r + RS <= len(data) and struct.unpack_from('<H', data, r)[0] == 0:
        leading_empty_slots += 1
        if leading_empty_slots > 32:
            break
        r += RS
    if leading_empty_slots and leading_empty_slots <= 32 and r + RS <= len(data):
        log(f"BMS skipped {leading_empty_slots} leading empty entity slot(s)")
    elif leading_empty_slots > 32:
        r = len(data)
    while r + RS <= len(data):
        tid = struct.unpack_from('<H', data, r + 0x00)[0]
        if tid == 0:
            break
        heading = struct.unpack_from('<h', data, r + 0x34)[0]
        if tid == 1747:
            heading = 60
        item = item_defs.get(int(tid), {}) if item_defs else {}
        cat = (item.get('category') or '').lower()
        graphic = item.get('graphic') or ''
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1
        category_static = bool(cat and cat in static_categories and cat not in skip_categories and (cat == 'foliage' or _item_category_allows_cmodel(item)))
        if cat == 'foliage':
            if not QUIET_DEBUG_LOGS:
                print(f"[DEBUG foliage2] tid={tid} cat={cat!r} in_static_cats={cat in static_categories} static_categories={static_categories} skip={cat in skip_categories}")
        is_static = (tid not in NEVER_EXCLUDE) and category_static
        if cat == 'foliage':
            if not QUIET_DEBUG_LOGS:
                print(f"[DEBUG foliage] tid={tid} cat={cat} is_static={is_static} category_static={category_static} in_NEVER={tid in NEVER_EXCLUDE}")
        if is_static:
            static_from_category += 1
        entities.append({
            'type_id': tid,
            'ssn': struct.unpack_from('<H', data, r + 0x04)[0],
            'x': struct.unpack_from('<i', data, r + 0x10)[0] / 65536.0,
            'y': struct.unpack_from('<i', data, r + 0x14)[0] / 65536.0,
            'z': struct.unpack_from('<i', data, r + 0x18)[0] / 65536.0,
            'is_static': is_static,
            'heading': heading,
            # BMS stores entity pitch as a signed degree value immediately
            # after heading.  Preserve it so renderers can reproduce tilted
            # mission objects instead of silently treating every model as
            # upright.
            'pitch': struct.unpack_from('<h', data, r + 0x36)[0],
            'category': cat,
            'graphic': graphic,
            'invincible': bool(data[r + ENTITY_INVINCIBLE_FLAG_OFFSET] & ENTITY_INVINCIBLE_FLAG_MASK),
        })
        r += RS
    log(f"Parsed {len(entities)} entities")
    _trace(
        f'parse_bms: entity table scan complete in {time.perf_counter() - _scan_t0:.3f}s; '
        f'entities={len(entities)} static_candidates={static_from_category} leading_empty={leading_empty_slots}')
    log(f"Static collision candidates: items.def category={static_from_category}")
    if category_counts:
        top_cats = ', '.join(f"{k}:{v}" for k, v in sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
        log(f"Entity categories: {top_cats}")
    dyn_zs = [e['z'] for e in entities
              if not e['is_static'] and e['type_id'] not in NEVER_EXCLUDE]
    if dyn_zs:
        _zbin = _ZCtr(round(z) for z in dyn_zs)
        _zthr = max(4, 0.12 * max(_zbin.values()))
        _zdense = sorted(b for b, c in _zbin.items() if c >= _zthr)
        if _zdense:
            _gb = _zdense[0]
            _gcl = [round(z, 3) for z in dyn_zs if abs(z - _gb) <= 1.5]
            terrain_z = float(_ZCtr(_gcl).most_common(1)[0][0]) if _gcl else float(_gb)
        else:
            terrain_z = min(dyn_zs)
    else:
        terrain_z = 18.5
    if dyn_zs and _zdense and _zbin.get(_gb, 0) < 20:
        _all_zs = [e['z'] for e in entities if e['type_id'] not in NEVER_EXCLUDE]
        if _all_zs:
            _abin = _ZCtr(round(z) for z in _all_zs)
            _adense = sorted(b for b, c in _abin.items() if c >= max(4, 0.12 * max(_abin.values())))
            if _adense:
                _agb = _adense[0]
                if _agb < terrain_z - 0.5 and _abin[_agb] >= 5 * _zbin.get(_gb, 0):
                    _acl = [round(z, 3) for z in _all_zs if abs(z - _agb) <= 1.5]
                    terrain_z = float(_ZCtr(_acl).most_common(1)[0][0]) if _acl else float(_agb)
    log(f"Terrain Z: {terrain_z:.2f}m")
    _trace(f'parse_bms: terrain floor resolved to {terrain_z:.4f}m; returning')
    return entities, terrain_z


def _detect_bms_terrain_name(bms_path, game_dir=None, log=None,
                             init_game_path=None, pff_cache=None):
    """Detect a BMS-declared terrain by resolving printable tokens as TRN assets."""
    if not bms_path:
        return '', ''
    try:
        data = Path(bms_path).read_bytes()
    except Exception as e:
        if log:
            log(f"[TERRAIN] failed to read BMS terrain tokens: {e}")
        return '', ''

    _igp = init_game_path or (lambda _p: None)
    gp = game_dir or _igp(bms_path) or Path(bms_path).parent
    _cache = pff_cache if pff_cache is not None else {}
    seen = set()
    for raw in _re.findall(rb'[A-Za-z0-9_\\-]{2,40}', data):
        try:
            name = raw.decode('latin1', errors='ignore').strip()
        except Exception:
            continue
        key = name.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        trn_file = _ensure_ext(name, 'trn')
        trn_raw, trn_source = _resolve_game_asset_bytes(gp, trn_file, pff_cache=_cache)
        if trn_raw:
            if log:
                log(f"[TERRAIN] BMS terrain token {name!r} resolved as {trn_file!r} from {trn_source}")
            return name, trn_source or ''

    if log:
        log("[TERRAIN] no BMS terrain token resolved to a TRN asset")
    return '', ''
