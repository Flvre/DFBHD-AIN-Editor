"""Editor configuration persistence — dfbhd_editor.cfg read/write utilities."""

import json
from pathlib import Path
from shared.runtime_paths import writable_app_file


def _editor_cfg_path():
    return writable_app_file('dfbhd_editor.cfg', __file__)


def _load_editor_cfg():
    """Load persistent editor preferences from dfbhd_editor.cfg.

    This file already stores the DFBHD game path. Keep it as the small shared
    editor config so UI preferences do not need another config file.
    """
    try:
        cfg_path = _editor_cfg_path()
        if not cfg_path.exists():
            return {}
        data = json.loads(cfg_path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_editor_cfg(data):
    try:
        cfg_path = _editor_cfg_path()
        if not isinstance(data, dict):
            data = {}
        cfg_path.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _editor_cfg_bool(key, default=False):
    try:
        return bool(_load_editor_cfg().get(key, default))
    except Exception:
        return bool(default)


def _configured_additional_pff_dirs():
    """Return configured external PFF folders in user-defined order.

    Paths are preserved even when currently missing so project/config diagnostics
    can report a broken mod-resource location instead of silently forgetting it.
    """
    try:
        raw = _load_editor_cfg().get('additional_pff_dirs', [])
    except Exception:
        raw = []
    if not isinstance(raw, (list, tuple)):
        return []

    out = []
    seen = set()
    for value in raw:
        try:
            s = str(value or '').strip()
            if not s:
                continue
            p = Path(s)
            try:
                key = str(p.expanduser().resolve()).lower()
            except Exception:
                key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        except Exception:
            continue
    return out


def _save_additional_pff_dirs(paths):
    """Persist the external PFF search folders without changing game_path."""
    clean = []
    seen = set()
    for value in (paths or []):
        try:
            s = str(value or '').strip()
            if not s:
                continue
            p = Path(s)
            try:
                key = str(p.expanduser().resolve()).lower()
            except Exception:
                key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            clean.append(str(p))
        except Exception:
            continue
    try:
        cfg = _load_editor_cfg()
        cfg['additional_pff_dirs'] = clean
        _save_editor_cfg(cfg)
    except Exception:
        pass
    return clean
