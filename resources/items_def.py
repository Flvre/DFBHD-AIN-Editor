"""Pure NovaLogic SCR transformation, ITEMS.DEF text parsing, and item loading."""
from pathlib import Path

from shared.debug_log import _dbg
from shared.process_util import _status_log
from resources.pff_archive import _iter_pff_archives, _read_from_pff, _read_from_pff_stack
from config.editor_config import _configured_additional_pff_dirs

# ── SCR cryptography ────────────────────────────────────────────────────────

BHD_SCRIPT_KEY = 0x2A5A8EAD


def _rol32(x, n):
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _descr_crypt(data, key=BHD_SCRIPT_KEY):
    """NovaLogic SCR stream transform used by BHD .def/.aip/etc."""
    out = bytearray(data)
    state = key & 0xFFFFFFFF
    for i in range(len(out)):
        state = _rol32((_rol32(state, 11) + state) & 0xFFFFFFFF, 4) ^ 1
        state &= 0xFFFFFFFF
        out[i] ^= state & 0xFF
    return bytes(out)


def _decrypt_scr(raw, key=BHD_SCRIPT_KEY):
    if raw[:4] != b'SCR\x01':
        return raw
    return _descr_crypt(raw[4:][::-1], key)


def _parse_items_def_text(text):
    """Parse decrypted items.def into type_id -> item dict."""
    import shlex
    items = {}
    cur = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('//') or line.startswith('#'):
            continue
        try:
            toks = shlex.split(line, comments=False, posix=True)
        except Exception:
            toks = line.replace('"', '').split()
        if not toks:
            continue
        key = toks[0].lower()
        if key == 'begin':
            cur = {'name': toks[1] if len(toks) > 1 else '', 'attribs': []}
        elif key == 'end':
            if cur and 'type_id' in cur:
                items[cur['type_id']] = cur
            cur = None
        elif cur is not None:
            if key == 'id' and len(toks) > 1:
                try:
                    cur['raw_id'] = int(toks[1], 0)
                    cur['type_id'] = cur['raw_id'] - 100000
                except Exception:
                    pass
            elif key == 'type' and len(toks) > 1:
                cur['category'] = toks[1].lower()
            elif key == 'graphic' and len(toks) > 1:
                cur['graphic'] = toks[1]
            elif key == 'attrib:':
                cur.setdefault('attribs', []).extend(toks[1:])
            elif key == 'scale' and len(toks) > 1:
                try:
                    cur['scale'] = float(toks[1])
                except Exception:
                    cur['scale'] = toks[1]
            elif len(toks) > 1:
                cur[key.rstrip(':')] = toks[1] if len(toks) == 2 else toks[1:]
    return items


def _graphic_to_filename(graphic):
    name = (graphic or '').strip()
    if not name:
        return None
    if '.' not in name:
        name += '.3di'
    return name


# ── Items.def loading and 3DI file resolution ──────────────────────────────

_items_def_cache = {}       # {game_path: {type_id: item}}


def _load_items_def(game_path, log=None, *, pff_cache=None):
    """Load/decrypt/parse items.def from loose file and the PFF stack.

    Expansion/local archives can carry additional or overriding item records, so
    this merges every items.def found in priority order. Later records override
    earlier records when type IDs collide.
    """
    if pff_cache is None:
        pff_cache = {}
    gp = Path(game_path)
    key = str(gp).lower()
    if key in _items_def_cache:
        return _items_def_cache[key]

    merged = {}

    def merge_raw(raw, source):
        if not raw:
            return 0
        try:
            plain = _decrypt_scr(raw)
            src_text = plain.decode('latin1', errors='replace')
            parsed = _parse_items_def_text(src_text)
            merged.update(parsed)
            _dbg('ITEMS_DEF', f'[items.def] merged {len(parsed)} definitions from {source}', once_key=('loaded', key, str(source)))
            return len(parsed)
        except Exception as e:
            _status_log(log, f'[items.def] parse error from {source}: {e}')
            _dbg('ITEMS_DEF', f'[items.def] parse error from {source}: {e}', once_key=('parse_error', str(source)))
            return 0

    for pff in reversed(_iter_pff_archives(gp, additional_dirs=_configured_additional_pff_dirs())):
        raw = _read_from_pff(pff, 'items.def', cache=pff_cache, dbg=_dbg)
        if raw:
            merge_raw(raw, pff.name)

    loose = gp / 'items.def'
    if loose.exists():
        try:
            merge_raw(loose.read_bytes(), loose.name)
        except Exception:
            pass

    if not merged:
        _status_log(log, '[items.def] not found; CModel lookup will be unavailable')
        _dbg('ITEMS_DEF', '[items.def] not found; CModel lookup will be unavailable', once_key='missing')
        _items_def_cache[key] = {}
        return {}

    _dbg('ITEMS_DEF', f'[items.def] total merged definitions: {len(merged)}', once_key=('merged_total', key))
    _items_def_cache[key] = merged
    return merged


def _get_3di_bytes_for_type(type_id, game_path, log=None, *, pff_cache=None):
    if pff_cache is None:
        pff_cache = {}
    items = _load_items_def(game_path, log=log, pff_cache=pff_cache)
    item = items.get(type_id)
    if not item:
        _dbg('CMODEL_MISSING', f'[3DI] no items.def record for type {type_id}', once_key=('no_item', int(type_id)))
        return None, None
    filename = _graphic_to_filename(item.get('graphic'))
    if not filename:
        _dbg('CMODEL_MISSING', f'[3DI] no graphic filename for type {type_id}: {item}', once_key=('no_graphic', int(type_id)))
        return None, item

    gp = Path(game_path)
    for direct in (gp / filename, gp / 'models' / filename, gp / 'resource' / filename):
        if direct.exists():
            try:
                return direct.read_bytes(), item
            except Exception:
                pass

    raw, pff_source = _read_from_pff_stack(gp, filename, additional_dirs=_configured_additional_pff_dirs(), cache=pff_cache, dbg=_dbg)
    if raw:
        return raw, item
    _dbg('CMODEL_MISSING', f'[3DI] missing file {filename} for type {type_id}', once_key=('missing_file', int(type_id), filename))
    return None, item
