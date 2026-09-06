"""3D wireframe debugger — ring buffer, log file, and render-mesh cache control."""
import os
import time
from shared.runtime_paths import writable_app_file

WF3D_DEBUG_ENABLED = False
WF3D_DEBUG_TO_CONSOLE = False
WF3D_DEBUG_MAX_LINES = 2500
WF3D_DEBUG_LINES = []
WF3D_DEBUG_LOG_PATH = str(writable_app_file('ain_editor_3d_debug.log', __file__))
WF3D_RENDER_CACHE_VERSION = 'med_lod_v5_med_origin_anchor'
WF3D_RENDER_MESH_CACHE_VERSION = 'medlod_v4_cmodel_scan'


def _wf3d_dbg(msg, *, force=False):
    """Append one line to the 3D debug ring buffer and optional log file."""
    try:
        if not force and not WF3D_DEBUG_ENABLED:
            return
        ts = time.strftime('%H:%M:%S')
        line = f'[{ts}] {msg}'
        WF3D_DEBUG_LINES.append(line)
        if len(WF3D_DEBUG_LINES) > WF3D_DEBUG_MAX_LINES:
            del WF3D_DEBUG_LINES[:len(WF3D_DEBUG_LINES)-WF3D_DEBUG_MAX_LINES]
        try:
            with open(WF3D_DEBUG_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass
        if WF3D_DEBUG_TO_CONSOLE:
            print(line)
    except Exception:
        pass

def _wf3d_dbg_clear():
    try:
        WF3D_DEBUG_LINES.clear()
        with open(WF3D_DEBUG_LOG_PATH, 'w', encoding='utf-8') as f:
            f.write('=== 3D WIREFRAME DEBUG LOG CLEARED ===\n')
    except Exception:
        pass

def _wf3d_clear_rendermesh_cache(cache=None):
    """Drop only cached 3D render-mesh parser entries, especially stale negatives."""
    removed = 0
    try:
        if cache is None:
            return 0
        for k in list(cache.keys()):
            if isinstance(k, tuple) and len(k) >= 1 and k[0] == 'rendermesh3d':
                try:
                    del cache[k]
                    removed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return removed
