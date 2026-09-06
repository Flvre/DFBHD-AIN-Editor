"""Stable paths for portable EXE settings and logs."""
from pathlib import Path
import sys


def writable_app_file(name, source_file):
    """Keep source paths unchanged; frozen apps write beside the executable."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / name
    return Path(source_file).resolve().parent / name
