"""Game asset resolution — loose files and PFF archives."""
from pathlib import Path
from shared.debug_log import _dbg
from resources.pff_archive import (_iter_pff_archives, _read_from_pff,
    _read_from_pff_fuzzy, _pff_find_candidates)
from config.editor_config import _configured_additional_pff_dirs


def _terrain_asset_search_roots(base_dir):
    """Likely direct-file locations before falling back to PFF."""
    base = Path(base_dir) if base_dir else Path.cwd()
    roots = [
        base,
        base / 'resource',
        base / 'terrain',
        base / 'terrains',
        base / 'maps',
        base / 'missions',
        base / 'textures',
    ]
    try:
        here = Path(__file__).resolve().parent
        roots.append(here)
    except Exception:
        pass
    seen = set()
    result = []
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result


def _resolve_game_asset_bytes(game_dir, filename, *, pff_cache=None):
    """Find an asset either loose on disk or inside known PFF archives.

    Returns (bytes, source_label) or (None, None).

    Terrain files are frequently archived and may use uppercase/path variants,
    so this resolver tries:
      - direct loose file check (exact path only - fast)
      - exact PFF lookup (cached index - fast)
      - fuzzy PFF basename lookup
      - one-level loose case-insensitive scan
      - recursive loose basename search (slowest - last resort)
    """
    if not filename:
        return None, None
    fn = str(filename).strip().strip('"')
    if not fn:
        return None, None
    gp = Path(game_dir) if game_dir else Path.cwd()
    low = fn.lower()
    want_base = low.replace('\\', '/').split('/')[-1]
    if pff_cache is None:
        pff_cache = {}

    for root in _terrain_asset_search_roots(gp):
        cand = root / fn
        if cand.exists():
            try:
                return cand.read_bytes(), str(cand)
            except Exception:
                pass

    candidate_debug = []
    for p in _iter_pff_archives(gp, additional_dirs=_configured_additional_pff_dirs()):
        raw = _read_from_pff(p, fn, cache=pff_cache, dbg=_dbg)
        if raw is not None:
            return raw, f'{p}:{fn}'

        raw, matched = _read_from_pff_fuzzy(p, fn, cache=pff_cache, dbg=_dbg)
        if raw is not None:
            return raw, f'{p}:{matched}'

        for name, size in _pff_find_candidates(p, fn, limit=6, cache=pff_cache):
            candidate_debug.append(f'{p}:{name} ({size} bytes)')

    for root in _terrain_asset_search_roots(gp):
        try:
            if root.exists():
                for child in root.iterdir():
                    if child.is_file() and child.name.lower() == want_base:
                        try:
                            return child.read_bytes(), str(child)
                        except Exception:
                            pass
        except Exception:
            pass

    for root in _terrain_asset_search_roots(gp):
        try:
            if root.exists():
                for child in root.rglob('*'):
                    if child.is_file() and child.name.lower() == want_base:
                        try:
                            return child.read_bytes(), str(child)
                        except Exception:
                            pass
        except Exception:
            pass

    if candidate_debug:
        _dbg(
            'TERRAIN',
            '[TERRAIN] related archive entries for '
            f'{fn}: ' + '; '.join(candidate_debug[:12]),
            once_key=('asset_candidates', fn)
        )
    return None, None
