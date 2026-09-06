"""Win32 native menu visual policy — keeps HMENU background brush
aligned with the Tk dark/light theme on Windows."""

import sys

from config.ui_theme import C_PANEL


class _WindowsNativeMenuVisualPolicy:
    """Keep the native Win32 popup-menu *surface* aligned with the Tk theme.

    Windows/Tk popup menus have two visually different layers:

    * Tk owner-draws the menu entries themselves using ``Menu`` colours.
    * USER32 owns the HMENU background that remains visible around those entries.

    On a dark custom Tk menu Windows can therefore leave a light strip around the
    owner-drawn body even when ``borderwidth=0`` and ``relief='flat'``.  That strip
    is not the DWM window border; it is the HMENU background brush.  The correct
    Win32 control point is ``SetMenuInfo(MIM_BACKGROUND)`` on the popup's HMENU.

    EVENT_OBJECT_SHOW is used only to obtain the real native ``#32768`` HWND and
    its HMENU after Windows has created them.  In dark editor mode the HMENU brush
    is set to the exact central menu background colour; in light mode it is reset
    to Windows' COLOR_MENU system brush.  Geometry, shadows, cascade placement,
    hit-testing and menu-item drawing remain native/Tk-owned.
    """

    EVENT_OBJECT_SHOW = 0x8002
    OBJID_WINDOW = 0
    WINEVENT_OUTOFCONTEXT = 0x0000
    MENU_WINDOW_CLASS = '#32768'

    MN_GETHMENU = 0x01E1
    MIM_BACKGROUND = 0x00000002
    MIM_APPLYTOSUBMENUS = 0x80000000
    COLOR_MENU = 4

    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_DRAWFRAME = 0x0020
    RDW_INVALIDATE = 0x0001
    RDW_UPDATENOW = 0x0100
    RDW_FRAME = 0x0400

    def __init__(self):
        self.enabled = bool(sys.platform.startswith('win'))
        self.dark_mode = False
        self.dark_color = C_PANEL
        self._hook = 0
        self._event_proc = None
        self._dark_brush = 0
        self._ctypes = None
        self._user32 = None
        self._kernel32 = None
        self._gdi32 = None
        self._WINEVENTPROC = None
        self._MENUINFO = None
        if not self.enabled:
            return
        try:
            import ctypes
            from ctypes import wintypes

            class MENUINFO(ctypes.Structure):
                _fields_ = [
                    ('cbSize', wintypes.DWORD),
                    ('fMask', wintypes.DWORD),
                    ('dwStyle', wintypes.DWORD),
                    ('cyMax', wintypes.UINT),
                    ('hbrBack', wintypes.HANDLE),
                    ('dwContextHelpID', wintypes.DWORD),
                    ('dwMenuData', ctypes.c_void_p),
                ]

            self._ctypes = ctypes
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
            self._gdi32 = ctypes.windll.gdi32
            self._MENUINFO = MENUINFO

            self._WINEVENTPROC = ctypes.WINFUNCTYPE(
                None,
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HWND,
                wintypes.LONG,
                wintypes.LONG,
                wintypes.DWORD,
                wintypes.DWORD,
            )

            self._user32.SetWinEventHook.argtypes = [
                wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                self._WINEVENTPROC, wintypes.DWORD, wintypes.DWORD,
                wintypes.DWORD,
            ]
            self._user32.SetWinEventHook.restype = wintypes.HANDLE
            self._user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            self._user32.UnhookWinEvent.restype = wintypes.BOOL
            self._user32.GetClassNameW.argtypes = [
                wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            self._user32.GetClassNameW.restype = ctypes.c_int
            self._user32.SendMessageW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self._user32.SendMessageW.restype = ctypes.c_ssize_t
            self._user32.SetMenuInfo.argtypes = [
                wintypes.HMENU, ctypes.POINTER(MENUINFO)]
            self._user32.SetMenuInfo.restype = wintypes.BOOL
            self._user32.GetSysColorBrush.argtypes = [ctypes.c_int]
            self._user32.GetSysColorBrush.restype = wintypes.HANDLE
            self._user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.UINT]
            self._user32.SetWindowPos.restype = wintypes.BOOL
            self._user32.RedrawWindow.argtypes = [
                wintypes.HWND, ctypes.c_void_p, wintypes.HANDLE, wintypes.UINT]
            self._user32.RedrawWindow.restype = wintypes.BOOL
            self._kernel32.GetCurrentProcessId.argtypes = []
            self._kernel32.GetCurrentProcessId.restype = wintypes.DWORD
            self._kernel32.GetCurrentThreadId.argtypes = []
            self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            self._gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
            self._gdi32.CreateSolidBrush.restype = wintypes.HANDLE
            self._gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
            self._gdi32.DeleteObject.restype = wintypes.BOOL

            if not self._install_hook():
                self.enabled = False
        except Exception:
            self.enabled = False
            self._hook = 0
            self._event_proc = None

    @staticmethod
    def _colorref_from_hex(color):
        """Convert #RRGGBB to Win32 COLORREF (0x00BBGGRR)."""
        try:
            value = str(color).strip()
            if len(value) != 7 or value[0] != '#':
                return 0
            r = int(value[1:3], 16)
            g = int(value[3:5], 16)
            b = int(value[5:7], 16)
            return r | (g << 8) | (b << 16)
        except Exception:
            return 0

    def set_dark_mode(self, enabled, menu_bg=None):
        self.dark_mode = bool(enabled)
        if menu_bg:
            self.dark_color = str(menu_bg)

    def _class_name(self, hwnd):
        if not self.enabled or not hwnd:
            return ''
        try:
            buf = self._ctypes.create_unicode_buffer(64)
            if self._user32.GetClassNameW(hwnd, buf, len(buf)):
                return buf.value
        except Exception:
            pass
        return ''

    def _dark_background_brush(self):
        if self._dark_brush:
            return self._dark_brush
        if self._gdi32 is None:
            return 0
        try:
            self._dark_brush = int(self._gdi32.CreateSolidBrush(
                self._colorref_from_hex(self.dark_color)) or 0)
        except Exception:
            self._dark_brush = 0
        return self._dark_brush

    def _style_popup_hwnd(self, hwnd):
        """Theme the native HMENU background exposed around Tk owner-drawn rows."""
        if not self.enabled or not hwnd:
            return False
        if self._class_name(hwnd) != self.MENU_WINDOW_CLASS:
            return False
        try:
            hmenu = int(self._user32.SendMessageW(
                hwnd, self.MN_GETHMENU, 0, 0) or 0)
            if not hmenu:
                return False

            if self.dark_mode:
                brush = self._dark_background_brush()
            else:
                brush = int(self._user32.GetSysColorBrush(self.COLOR_MENU) or 0)
            if not brush:
                return False

            info = self._MENUINFO()
            info.cbSize = self._ctypes.sizeof(self._MENUINFO)
            info.fMask = self.MIM_BACKGROUND | self.MIM_APPLYTOSUBMENUS
            info.hbrBack = brush
            if not self._user32.SetMenuInfo(hmenu, self._ctypes.byref(info)):
                return False

            flags = (self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOZORDER
                     | self.SWP_NOACTIVATE | self.SWP_DRAWFRAME)
            self._user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags)
            self._user32.RedrawWindow(
                hwnd, None, None,
                self.RDW_INVALIDATE | self.RDW_FRAME | self.RDW_UPDATENOW)
            return True
        except Exception:
            return False

    def _install_hook(self):
        if not self.enabled or self._hook:
            return bool(self._hook)
        try:
            @self._WINEVENTPROC
            def _event_proc(_hook, event, hwnd, id_object, id_child,
                            _event_thread, _event_time):
                try:
                    if (int(event) == self.EVENT_OBJECT_SHOW
                            and int(id_object) == self.OBJID_WINDOW
                            and int(id_child) == 0):
                        self._style_popup_hwnd(int(hwnd or 0))
                except Exception:
                    pass

            self._event_proc = _event_proc
            pid = int(self._kernel32.GetCurrentProcessId())
            tid = int(self._kernel32.GetCurrentThreadId())
            self._hook = int(self._user32.SetWinEventHook(
                self.EVENT_OBJECT_SHOW, self.EVENT_OBJECT_SHOW, None,
                self._event_proc, pid, tid, self.WINEVENT_OUTOFCONTEXT) or 0)
            if not self._hook:
                self._event_proc = None
                return False
            return True
        except Exception:
            self._hook = 0
            self._event_proc = None
            return False

    def close(self):
        hook = self._hook
        self._hook = 0
        if hook and self._user32 is not None:
            try:
                self._user32.UnhookWinEvent(hook)
            except Exception:
                pass
        self._event_proc = None
        brush = self._dark_brush
        self._dark_brush = 0
        if brush and self._gdi32 is not None:
            try:
                self._gdi32.DeleteObject(brush)
            except Exception:
                pass
