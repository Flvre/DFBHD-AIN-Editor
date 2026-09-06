"""PFF archive format reader and diagnostic inspection for DFBHD/NovaLogic .pff files.

Reads the EOF-index binary format, enumerates archive stacks with
expansion/local override priority, provides single-file extraction,
and exposes diagnostic utilities for PFF header/index inspection.
"""
import re
import struct
import traceback
from pathlib import Path

from shared.debug_log import _dbg
from config.editor_config import _configured_additional_pff_dirs

_get_game_path = lambda: None
_get_pff_cache = lambda: {}


def _pff_priority_key(path):
    """Sort PFF archives so expansion/local overrides are checked before base."""
    name = Path(path).name.lower()
    if name == 'localres.pff':
        return (0, name)
    if name.startswith('exp') or 'exp' in name or 'xpack' in name or 'expansion' in name:
        return (1, name)
    if name == 'resource.pff':
        return (9, name)
    return (5, name)


def _build_pff_index(pff_path, cache=None, dbg=None):
    """Parse a PFF index.  Reads only the EOF index, not the whole archive.

    Parameters
    ----------
    cache : dict or None
        If provided, memoizes parsed indices keyed by lowered path string.
    dbg : callable or None
        Debug logger with signature ``dbg(channel, msg, *, once_key=...)``.
    """
    pff_path = Path(pff_path)
    key = str(pff_path).lower()
    if cache is not None and key in cache:
        return cache[key]
    if not pff_path.exists():
        if cache is not None:
            cache[key] = {}
        return {}

    _empty = {}

    def _store(result):
        if cache is not None:
            cache[key] = result
        return result

    try:
        file_size = pff_path.stat().st_size
        with open(pff_path, 'rb') as f:
            header = f.read(0x14)
            if len(header) != 0x14:
                return _store(_empty)
            hdr_size, archive_id, file_count, entry_size, index_offset = struct.unpack_from('<IIIII', header, 0)
            if hdr_size != 0x14 or entry_size != 0x24:
                if dbg:
                    dbg('PFF_INDEX', f'[PFF] unsupported header in {pff_path.name}: hdr={hdr_size:#x} stride={entry_size:#x}', once_key=('bad_header', str(pff_path)))
                return _store(_empty)
            index_end = index_offset + file_count * entry_size
            if index_end > file_size:
                if dbg:
                    dbg('PFF_INDEX', f'[PFF] bad EOF index in {pff_path.name}: index={index_offset:#x} count={file_count} stride={entry_size} end={index_end:#x} size={file_size:#x}', once_key=('bad_index', str(pff_path)))
                return _store(_empty)
            if index_end != file_size:
                if dbg:
                    dbg('PFF_INDEX', f'[PFF] trailing footer in {pff_path.name}: {file_size-index_end} bytes', once_key=('index_footer', str(pff_path)))
            f.seek(index_offset)
            index_data = f.read(file_count * entry_size)

        index = {}
        for i in range(file_count):
            base = i * entry_size
            off = struct.unpack_from('<I', index_data, base + 0x04)[0]
            size = struct.unpack_from('<I', index_data, base + 0x08)[0]
            raw_name = index_data[base + 0x10:base + 0x20]
            name = raw_name.split(b'\x00', 1)[0].decode('latin1', errors='replace')
            if name:
                index[name.lower()] = (off, size)
        if dbg:
            dbg('PFF_INDEX', f'[PFF] indexed {pff_path.name}: {len(index)} files, {sum(1 for n in index if n.endswith(".3di"))} 3DI', once_key=('indexed', str(pff_path)))
        return _store(index)
    except Exception as e:
        if dbg:
            dbg('PFF_INDEX', f'[PFF] index error {pff_path}: {e}', once_key=('index_error', str(pff_path)))
        return _store(_empty)


def _read_from_pff(pff_path, filename, cache=None, dbg=None):
    """Read one file from a PFF by seek+size."""
    try:
        idx = _build_pff_index(pff_path, cache=cache, dbg=dbg)
        ent = idx.get(filename.lower())
        if not ent:
            return None
        off, size = ent
        with open(pff_path, 'rb') as f:
            f.seek(off)
            return f.read(size)
    except Exception as e:
        if dbg:
            dbg('PFF_INDEX', f'[PFF] read error {filename} from {pff_path}: {e}', once_key=('read_error', str(pff_path), filename))
        return None


def _read_from_pff_fuzzy(pff_path, filename, cache=None, dbg=None):
    """Read a file from PFF by exact name, then basename/suffix fallback.

    Some NovaLogic archives store terrain assets with paths or mixed naming.
    """
    try:
        idx = _build_pff_index(pff_path, cache=cache, dbg=dbg)
        if not idx:
            return None, None

        want = str(filename or '').replace('\\', '/').lower()
        want_base = want.split('/')[-1]

        ent = idx.get(want)
        matched = want if ent else None

        if ent is None:
            for name, val in idx.items():
                n = name.replace('\\', '/').lower()
                if n.split('/')[-1] == want_base:
                    ent = val
                    matched = name
                    break

        if ent is None:
            for name, val in idx.items():
                n = name.replace('\\', '/').lower()
                if n.endswith('/' + want_base) or n.endswith('\\' + want_base):
                    ent = val
                    matched = name
                    break

        if ent is None:
            return None, None

        off, size = ent
        with open(pff_path, 'rb') as f:
            f.seek(off)
            return f.read(size), matched
    except Exception as e:
        if dbg:
            dbg('PFF_INDEX', f'[PFF] fuzzy read error {filename} from {pff_path}: {e}', once_key=('fuzzy_read_error', str(pff_path), filename))
        return None, None


def _pff_find_candidates(pff_path, filename, limit=12, cache=None):
    """Return archive entries whose basename contains the wanted stem."""
    try:
        idx = _build_pff_index(pff_path, cache=cache)
        want = str(filename or '').replace('\\', '/').lower()
        base = want.split('/')[-1]
        stem = base.rsplit('.', 1)[0]
        out = []
        for name, (off, size) in idx.items():
            n = name.replace('\\', '/').lower()
            b = n.split('/')[-1]
            if stem and (stem in b or b in base):
                out.append((name, size))
                if len(out) >= limit:
                    break
        return out
    except Exception:
        return []


def _iter_pff_archive_records(game_path, additional_dirs=None):
    """Return PFF records in resource lookup order (highest priority first).

    Parameters
    ----------
    additional_dirs : list of Path or None
        External PFF folders. Later entries override earlier entries.
    """
    gp = Path(game_path)
    seen = set()
    records = []

    def _key(path):
        try:
            return str(Path(path).resolve()).lower()
        except Exception:
            return str(Path(path)).lower()

    def _add(path, source):
        p = Path(path)
        try:
            if not p.is_file() or p.suffix.lower() != '.pff':
                return
        except Exception:
            return
        if p.name.lower() == 'med.pff':
            return
        k = _key(p)
        if k in seen:
            return
        seen.add(k)
        records.append({'path': p, 'source': source})

    extra_dirs = additional_dirs if additional_dirs is not None else []
    for idx in range(len(extra_dirs) - 1, -1, -1):
        root = extra_dirs[idx]
        if not root.is_dir():
            continue
        group = []
        try:
            group.extend(root.glob('*.pff'))
        except Exception:
            pass
        try:
            pff_sub = root / 'pff'
            if pff_sub.is_dir():
                group.extend(pff_sub.glob('*.pff'))
        except Exception:
            pass
        unique_group = {}
        for p in group:
            unique_group[_key(p)] = p
        for p in sorted(unique_group.values(), key=_pff_priority_key):
            _add(p, f"Additional #{idx + 1}")

    base_candidates = []
    roots = [gp, gp / 'pff', gp.parent / 'pff', gp / 'resource', gp / 'resources']
    for root in roots:
        try:
            if root and root.is_dir():
                base_candidates.extend(root.glob('*.pff'))
        except Exception:
            pass

    for name in ('localres.pff', 'exp1.pff', 'exp2.pff', 'exp3.pff', 'exp4.pff', 'resource.pff'):
        p = gp / name
        if p.exists():
            base_candidates.append(p)

    unique_base = {}
    for p in base_candidates:
        unique_base[_key(p)] = p
    for p in sorted(unique_base.values(), key=_pff_priority_key):
        _add(p, 'Game Directory')

    return records


def _iter_pff_archives(game_path, additional_dirs=None):
    """Return all active PFF archive paths in lookup order."""
    return [rec['path'] for rec in _iter_pff_archive_records(game_path, additional_dirs=additional_dirs)]


def _read_from_pff_stack(game_path, filename, additional_dirs=None, cache=None, dbg=None):
    """Read filename from the best matching PFF in the game/expansion stack."""
    for p in _iter_pff_archives(game_path, additional_dirs=additional_dirs):
        raw = _read_from_pff(p, filename, cache=cache, dbg=dbg)
        if raw:
            return raw, p
    return None, None


def _inspect_pff_records(pff_path, max_records=24):
    """Return a human-readable report of a PFF header/index.

    This is intentionally separate from _build_pff_index so we can diagnose
    expansion archives whose record layout may differ from the base PFF parser.
    """
    pff_path = Path(pff_path)
    out = []
    out.append(f"PFF: {pff_path}")
    if not pff_path.exists():
        out.append("  missing")
        return "\n".join(out)
    try:
        size = pff_path.stat().st_size
        out.append(f"  size: {size:,} bytes")
        with open(pff_path, 'rb') as f:
            header = f.read(0x40)
        out.append(f"  first 0x40 bytes: {header[:0x40].hex(' ')}")
        if len(header) < 0x14:
            out.append("  too small for known header")
            return "\n".join(out)

        vals = struct.unpack_from('<IIIII', header, 0)
        hdr_size, archive_id, file_count, entry_size, index_offset = vals
        out.append(f"  header u32: hdr_size=0x{hdr_size:x} archive_id=0x{archive_id:x} file_count={file_count} entry_size=0x{entry_size:x} index_offset=0x{index_offset:x}")
        expected_end = index_offset + file_count * entry_size
        out.append(f"  expected index end: 0x{expected_end:x} ({expected_end:,}) {'OK' if expected_end == size else '!= file size'}")

        candidates = []
        if 0 < file_count < 200000 and 0 < entry_size < 0x200 and 0 <= index_offset < size:
            candidates.append((index_offset, entry_size, file_count, "header-declared"))
        for stride in (0x20, 0x24, 0x28, 0x30, 0x40):
            if size >= stride * 4:
                if 0 <= index_offset < size and index_offset + stride <= size:
                    candidates.append((index_offset, stride, min(file_count if 0 < file_count < 200000 else 32, 32), f"declared-offset stride 0x{stride:x}"))

        seen = set()
        for idx_off, stride, count, label in candidates:
            k = (idx_off, stride, count)
            if k in seen:
                continue
            seen.add(k)
            out.append("")
            out.append(f"  candidate {label}: index_off=0x{idx_off:x} stride=0x{stride:x} count_sample={min(count, max_records)}")
            if idx_off < 0 or idx_off >= size or stride <= 0:
                out.append("    invalid candidate")
                continue
            try:
                with open(pff_path, 'rb') as f:
                    f.seek(idx_off)
                    blob = f.read(min(count, max_records) * stride)
                decoded_names = []
                for i in range(min(count, max_records)):
                    rec = blob[i*stride:(i+1)*stride]
                    if len(rec) < stride:
                        break
                    u0 = struct.unpack_from('<I', rec + b'\0\0\0\0', 0)[0] if len(rec) >= 4 else 0
                    u4 = struct.unpack_from('<I', rec, 4)[0] if len(rec) >= 8 else 0
                    u8 = struct.unpack_from('<I', rec, 8)[0] if len(rec) >= 12 else 0
                    uc = struct.unpack_from('<I', rec, 12)[0] if len(rec) >= 16 else 0
                    raw16 = rec[0x10:0x20] if len(rec) >= 0x20 else b''
                    name16 = raw16.split(b'\0', 1)[0].decode('latin1', errors='replace')
                    printable = []
                    for m in re.finditer(rb'[ -~]{3,}', rec):
                        s = m.group(0).decode('latin1', errors='replace')
                        printable.append(f"+0x{m.start():02x}:{s}")
                    decoded_names.append(name16.lower())
                    out.append(f"    rec {i:04d}: u0=0x{u0:08x} u4=0x{u4:08x} u8=0x{u8:08x} uc=0x{uc:08x} name16='{name16}' print={printable} raw={rec.hex(' ')}")
                ext_counts = {}
                for n in decoded_names:
                    if '.' in n:
                        ext = n.rsplit('.', 1)[-1]
                        ext_counts[ext] = ext_counts.get(ext, 0) + 1
                if ext_counts:
                    out.append(f"    sample ext counts: {ext_counts}")
            except Exception as e:
                out.append(f"    candidate read failed: {e}")

        idx = _build_pff_index(pff_path, cache=_get_pff_cache(), dbg=_dbg)
        out.append("")
        out.append(f"  current parser sees: {len(idx)} files")
        if idx:
            names = sorted(idx.keys())
            out.append(f"  current parser first 30 names: {names[:30]}")
            out.append(f"  current parser .3di count: {sum(1 for n in names if n.endswith('.3di'))}")
            out.append(f"  current parser .def count: {sum(1 for n in names if n.endswith('.def'))}")
            out.append(f"  has items.def: {'items.def' in idx}")
        return "\n".join(out)
    except Exception as e:
        out.append(f"  inspect error: {e}")
        out.append(traceback.format_exc())
        return "\n".join(out)


def _inspect_all_pffs(game_path):
    gp = Path(game_path) if game_path else _get_game_path()
    if not gp:
        return "No game path set."

    out = []
    gp = Path(gp)
    configured = _configured_additional_pff_dirs()
    records = _iter_pff_archive_records(gp, additional_dirs=_configured_additional_pff_dirs())

    out.append(f"Game path: {gp}")
    out.append("")
    out.append(f"Configured additional PFF folders: {len(configured)}")
    if configured:
        for i, p in enumerate(configured, 1):
            status = 'OK' if p.is_dir() else 'MISSING'
            out.append(f"  {i:02d}. [{status}] {p}")
        out.append("  Note: later additional folders have higher override priority.")
    else:
        out.append("  (none)")

    out.append("")
    out.append(f"Loaded/detected PFF archives: {len(records)}")
    out.append("Search order (highest priority first):")
    for i, rec in enumerate(records, 1):
        p = rec['path']
        source = rec.get('source', 'Game Directory')
        try:
            count = len(_build_pff_index(p, cache=_get_pff_cache(), dbg=_dbg))
            count_text = f"{count:,} files"
        except Exception:
            count_text = "index error"
        out.append(f"  {i:02d}. [{source}] {p}  ({count_text})")

    out.append("")
    out.append("Detailed archive inspection")
    out.append("=" * 72)
    for rec in records:
        out.append(f"SOURCE: {rec.get('source', 'Game Directory')}")
        out.append(_inspect_pff_records(rec['path'], max_records=12))
        out.append("\n" + "=" * 72 + "\n")
    return "\n".join(out)
