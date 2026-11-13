"""Windows clipboard and keystrokes for AI Actions (ctypes only).

grab_selection() copies whatever is selected in the foreground app by
simulating Ctrl+C, reads it, and puts the previous clipboard back.
paste_into() puts text on the clipboard, focuses the window the text came
from, simulates Ctrl+V and then restores the previous clipboard.
"""

import ctypes
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYUP = 0x0002
VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN = 0x11, 0x12, 0x10, 0x5B, 0x5C
# Formats whose handle is not an HGLOBAL (GDI objects, owner-drawn) can't be
# copied byte for byte; Windows re-creates the bitmap ones from CF_DIB.
_SKIP = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]


def _open():
    for _ in range(25):  # another app may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            return
        time.sleep(0.04)
    raise OSError("the clipboard is busy")


def _global(data):
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
    if not h:
        raise OSError("GlobalAlloc failed")
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    return h


def _read(handle):
    size = kernel32.GlobalSize(handle)
    p = kernel32.GlobalLock(handle)
    if not p:
        return None
    try:
        return ctypes.string_at(p, size)
    finally:
        kernel32.GlobalUnlock(handle)


def sequence():
    return user32.GetClipboardSequenceNumber()


def get_text():
    _open()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        data = _read(h) if h else None
    finally:
        user32.CloseClipboard()
    if not data:
        return ""
    return data.decode("utf-16-le", "replace").split("\x00", 1)[0]


def set_text(text):
    put([(CF_UNICODETEXT, (text + "\x00").encode("utf-16-le"))])


def snapshot():
    """Everything on the clipboard that can be copied byte for byte."""
    saved = []
    _open()
    try:
        fmt = user32.EnumClipboardFormats(0)
        while fmt:
            if fmt not in _SKIP:
                h = user32.GetClipboardData(fmt)
                data = _read(h) if h else None
                if data is not None:
                    saved.append((fmt, data))
            fmt = user32.EnumClipboardFormats(fmt)
    finally:
        user32.CloseClipboard()
    return saved


def put(formats):
    _open()
    try:
        user32.EmptyClipboard()
        for fmt, data in formats:
            h = _global(data)
            if not user32.SetClipboardData(fmt, h):
                kernel32.GlobalFree(h)
    finally:
        user32.CloseClipboard()


def _key(vk, up=False):
    user32.keybd_event(vk, 0, KEYUP if up else 0, None)


def _chord(letter):
    _key(VK_CONTROL)
    _key(ord(letter))
    _key(ord(letter), True)
    _key(VK_CONTROL, True)


def wait_keys_released(extra=(), timeout=1.5):
    """Waits until the hotkey's modifiers are let go, so the simulated Ctrl+C
    isn't read as Ctrl+Alt+C."""
    keys = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN) + tuple(extra)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(user32.GetAsyncKeyState(k) & 0x8000 for k in keys):
            return True
        time.sleep(0.02)
    return False


def foreground():
    return user32.GetForegroundWindow()


def focus(hwnd):
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        # Windows only lets the process that got the last input change the
        # foreground window; a tap of Alt counts as that input.
        _key(VK_MENU)
        _key(VK_MENU, True)
        user32.SetForegroundWindow(hwnd)
    time.sleep(0.08)
    return user32.GetForegroundWindow() == hwnd


def grab_selection(hotkey_vk=None):
    """Returns (text, came_from_selection). Falls back to the clipboard's
    current text when nothing is selected. The old clipboard is restored."""
    wait_keys_released((hotkey_vk,) if hotkey_vk else ())
    try:
        old = snapshot()
    except OSError:
        old = None
    before = sequence()
    _chord("C")
    end = time.monotonic() + 0.6
    while time.monotonic() < end and sequence() == before:
        time.sleep(0.02)
    if sequence() == before:
        return get_text(), False
    time.sleep(0.05)  # some apps put several formats in a row
    text = get_text()
    if old is not None:
        try:
            put(old)
        except OSError:
            pass
    if text.strip():
        return text, True
    return (_text_of(old) if old else ""), False


def _text_of(formats):
    for fmt, data in formats:
        if fmt == CF_UNICODETEXT:
            return data.decode("utf-16-le", "replace").split("\x00", 1)[0]
    return ""


def paste_into(hwnd, text, restore=True):
    """Pastes text into hwnd with Ctrl+V. Returns False if the window is gone."""
    try:
        old = snapshot() if restore else None
    except OSError:
        old = None
    set_text(text)
    if not focus(hwnd):
        return False
    wait_keys_released()
    _chord("V")
    if old is not None:
        time.sleep(0.5)  # let the app read the clipboard before restoring it
        try:
            put(old)
        except OSError:
            pass
    return True
