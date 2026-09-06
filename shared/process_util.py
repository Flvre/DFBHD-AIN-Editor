"""Process memory and cache utility functions."""
import os


def _status_log(log, msg):
    try:
        if log:
            log(msg)
        else:
            print(msg)
    except Exception:
        print(msg)


def _process_memory_mb():
    """Return current process RSS/working-set in MB."""
    try:
        import psutil
        return float(psutil.Process(os.getpid()).memory_info().rss) / (1024.0 * 1024.0)
    except Exception:
        pass

    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()

        for dll_name, fn_name in (("kernel32", "K32GetProcessMemoryInfo"), ("psapi", "GetProcessMemoryInfo")):
            try:
                dll = getattr(ctypes.windll, dll_name)
                fn = getattr(dll, fn_name)
                fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
                fn.restype = wintypes.BOOL
                if fn(handle, ctypes.byref(counters), counters.cb):
                    return float(counters.WorkingSetSize) / (1024.0 * 1024.0)
            except Exception:
                continue
    except Exception:
        pass

    try:
        import subprocess, csv
        pid = str(os.getpid())
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        ).strip()
        if out:
            row = next(csv.reader([out]))
            mem = row[-1].replace("K", "").replace(",", "").replace(" ", "")
            kb = float(mem)
            return kb / 1024.0
    except Exception:
        pass

    try:
        import resource
        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if rss > 1024 * 1024:
            return rss / (1024.0 * 1024.0)
        return rss / 1024.0
    except Exception:
        return None


def _trim_large_cache_dict(d, max_entries=256):
    """Trim dict-like caches without inspecting expensive values."""
    try:
        n = len(d)
        if n <= max_entries:
            return 0
        remove_n = n - max_entries
        for k in list(d.keys())[:remove_n]:
            try:
                del d[k]
            except Exception:
                pass
        return remove_n
    except Exception:
        return 0


def _process_memory_details():
    """Return a best-effort dict of process memory counters in MB."""
    details = {}
    try:
        import psutil
        mi = psutil.Process(os.getpid()).memory_info()
        for name in ('rss', 'vms', 'shared', 'private', 'peak_wset', 'wset'):
            if hasattr(mi, name):
                try:
                    details[name] = float(getattr(mi, name)) / (1024.0 * 1024.0)
                except Exception:
                    pass
        try:
            mfi = psutil.Process(os.getpid()).memory_full_info()
            for name in ('uss', 'pss', 'swap'):
                if hasattr(mfi, name):
                    details[name] = float(getattr(mfi, name)) / (1024.0 * 1024.0)
        except Exception:
            pass
        return details
    except Exception:
        pass
    try:
        rss = _process_memory_mb()
        if rss is not None:
            details['rss'] = float(rss)
    except Exception:
        pass
    return details
