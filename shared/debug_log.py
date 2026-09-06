"""Debug logging infrastructure for the PFF/CModel pipeline."""
import time

DEBUG_RENDER_TIMING = False
DEBUG_PFF_INDEX = False
DEBUG_ITEMS_DEF = False
DEBUG_CMODEL_QUEUE = False
DEBUG_CMODEL_PARSE = False
DEBUG_CMODEL_FAIL = False
DEBUG_CMODEL_MISSING = False
DEBUG_CMODEL_DRAW = False
_DEBUG_ONCE_KEYS = set()
_DEBUG_LAST = {}
QUIET_DEBUG_LOGS = True


def _dbg(channel, msg, *, once_key=None, throttle=0.0):
    """Small global debug logger for the PFF/CModel pipeline."""
    try:
        if globals().get('QUIET_DEBUG_LOGS', False) and channel not in ('MEMORY',):
            return
        enabled = globals().get('DEBUG_' + channel, False)
        if not enabled:
            return
        if once_key is not None:
            k = (channel, once_key)
            if k in _DEBUG_ONCE_KEYS:
                return
            _DEBUG_ONCE_KEYS.add(k)
        if throttle:
            now = time.time()
            last = _DEBUG_LAST.get(channel, 0.0)
            if now - last < throttle:
                return
            _DEBUG_LAST[channel] = now
        print(msg)
    except Exception:
        pass
