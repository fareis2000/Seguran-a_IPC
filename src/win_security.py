"""Integração nativa Win32 (64-bit safe): bloqueio de atalhos de sistema, barra de tarefas,
múltiplos monitores, eventos de desbloqueio/suspensão do Windows e inicialização automática."""

from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import threading
from dataclasses import dataclass
from typing import Callable
import winreg

from src.paths import APP_NAME, MUTEX_NAME, get_executable_command

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

# Tipos inteiros 64-bit / 32-bit seguros para ponteiros e callbacks Win32
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

# Constantes Win32
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1

PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0

LLKHF_ALTDOWN = 0x0020

VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_SNAPSHOT = 0x2C
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D
VK_F4 = 0x73
VK_CONTROL = 0x11
VK_MENU = 0x12

SW_HIDE = 0
SW_SHOW = 5

ERROR_ALREADY_EXISTS = 183
MONITORINFOF_PRIMARY = 0x00000001
RUN_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


WNDPROC_TYPE = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
HOOKPROC_TYPE = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
MONITORENUMPROC_TYPE = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), LPARAM
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC_TYPE),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


# Assinaturas explícitas 64-bit para evitar truncamento de ponteiros
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT

user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC_TYPE,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, WPARAM, LPARAM]
user32.CallNextHookEx.restype = LRESULT

user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT

user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, WPARAM, LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND

user32.FindWindowExW.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
]
user32.FindWindowExW.restype = wintypes.HWND

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(RECT),
    MONITORENUMPROC_TYPE,
    LPARAM,
]
user32.EnumDisplayMonitors.restype = wintypes.BOOL

user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL

wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL


@dataclass
class MonitorGeometry:
    x: int
    y: int
    width: int
    height: int
    is_primary: bool


class SingleInstanceGuard:
    """Garante apenas uma instância do SentinelPC em execução na sessão."""

    def __init__(self, mutex_name: str = MUTEX_NAME) -> None:
        self._handle = kernel32.CreateMutexW(None, False, mutex_name)
        last_err = ctypes.get_last_error()
        self.already_running = bool(last_err == ERROR_ALREADY_EXISTS)

    def release(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


def get_all_monitors() -> list[MonitorGeometry]:
    """Enumera todos os monitores conectados (primário + secundários)."""
    monitors: list[MonitorGeometry] = []

    def _callback(
        hmonitor: wintypes.HMONITOR,
        _hdc: wintypes.HDC,
        _lprect: ctypes.POINTER(RECT),
        _lparam: LPARAM,
    ) -> bool:
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append(
                MonitorGeometry(
                    x=int(r.left),
                    y=int(r.top),
                    width=int(r.right - r.left),
                    height=int(r.bottom - r.top),
                    is_primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return True

    cb = MONITORENUMPROC_TYPE(_callback)
    user32.EnumDisplayMonitors(None, None, cb, 0)
    if not monitors:
        w = int(user32.GetSystemMetrics(0))
        h = int(user32.GetSystemMetrics(1))
        monitors.append(MonitorGeometry(0, 0, w, h, True))
    monitors.sort(key=lambda m: (not m.is_primary, m.x))
    return monitors


def set_taskbar_visible(visible: bool) -> None:
    """Oculta ou restaura a barra de tarefas em todos os monitores."""
    cmd = SW_SHOW if visible else SW_HIDE
    primary = user32.FindWindowW("Shell_TrayWnd", None)
    if primary:
        user32.ShowWindow(primary, cmd)

    secondary = user32.FindWindowExW(None, None, "Shell_SecondaryTrayWnd", None)
    while secondary:
        user32.ShowWindow(secondary, cmd)
        secondary = user32.FindWindowExW(None, secondary, "Shell_SecondaryTrayWnd", None)


def _restore_taskbar_failsafe() -> None:
    try:
        set_taskbar_visible(True)
    except Exception:
        pass


atexit.register(_restore_taskbar_failsafe)


def lock_windows_workstation() -> None:
    """Aciona o bloqueio nativo da estação Windows (Win+L)."""
    try:
        user32.LockWorkStation()
    except Exception:
        pass


# ------------------------------------------------------------------
# Inicialização Automática no Logon do Windows (Registry Run)
# ------------------------------------------------------------------
def is_windows_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_REGISTRY_PATH, 0, winreg.KEY_READ
        ) as key:
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_windows_startup_enabled(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_REGISTRY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                cmd = get_executable_command(startup_mode=True)
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ------------------------------------------------------------------
# Monitor de Sessão & Hook de Teclado de Baixo Nível
# ------------------------------------------------------------------
class WinSecurityController:
    """Mantém thread Win32 dedicada para:
    1. Interceptar e bloquear teclas de fuga do sistema durante o modo Kiosk.
    2. Escutar eventos nativos de desbloqueio de tela (Win+L) e retorno de suspensão.
    """

    def __init__(self, on_system_event: Callable[[str], None] | None = None) -> None:
        self._on_system_event = on_system_event
        self.kiosk_keyboard_locked = False
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._hook_handle: wintypes.HHOOK | None = None
        self._hwnd: wintypes.HWND | None = None
        self._ready = threading.Event()
        # Referências fortes a callbacks C para impedir coleta pelo GC
        self._kb_proc_ref: HOOKPROC_TYPE | None = None
        self._wnd_proc_ref: WNDPROC_TYPE | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._win32_loop, name="SentinelWin32Hook", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def stop(self) -> None:
        self.set_kiosk_lock(False)
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def set_kiosk_lock(self, locked: bool) -> None:
        self.kiosk_keyboard_locked = locked
        set_taskbar_visible(not locked)

    def _emit(self, event_name: str) -> None:
        if self._on_system_event is not None:
            try:
                self._on_system_event(event_name)
            except Exception:
                pass

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and self.kiosk_keyboard_locked:
            if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(kb.vkCode)
                alt_down = bool(kb.flags & LLKHF_ALTDOWN) or (
                    user32.GetAsyncKeyState(VK_MENU) < 0
                )
                ctrl_down = user32.GetAsyncKeyState(VK_CONTROL) < 0

                # Bloqueia teclas Windows, Menu de Contexto e PrintScreen
                if vk in (VK_LWIN, VK_RWIN, VK_APPS, VK_SNAPSHOT):
                    return 1
                # Bloqueia Alt+Tab, Alt+F4, Alt+Esc, Alt+Space
                if alt_down and vk in (VK_TAB, VK_F4, VK_ESCAPE, VK_SPACE):
                    return 1
                # Bloqueia Ctrl+Esc e Ctrl+Shift+Esc
                if ctrl_down and vk == VK_ESCAPE:
                    return 1

        return int(user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param))

    def _window_proc(
        self, hwnd: wintypes.HWND, msg: int, w_param: int, l_param: int
    ) -> int:
        if msg == WM_WTSSESSION_CHANGE:
            if w_param == WTS_SESSION_UNLOCK:
                self._emit("SESSION_UNLOCK")
            elif w_param == WTS_SESSION_LOCK:
                self._emit("SESSION_LOCK")
            return 0
        if msg == WM_POWERBROADCAST:
            if w_param in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                self._emit("POWER_RESUME")
            return 1
        if msg == WM_DESTROY:
            return 0
        return int(user32.DefWindowProcW(hwnd, msg, w_param, l_param))

    def _win32_loop(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        h_instance = kernel32.GetModuleHandleW(None)

        self._wnd_proc_ref = WNDPROC_TYPE(self._window_proc)
        self._kb_proc_ref = HOOKPROC_TYPE(self._keyboard_callback)

        class_name = f"SentinelPC_MsgWnd_{self._thread_id}"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = h_instance
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))

        self._hwnd = user32.CreateWindowExW(
            0, class_name, "SentinelPCListener", 0, 0, 0, 0, 0, None, None, h_instance, None
        )
        if self._hwnd:
            wtsapi32.WTSRegisterSessionNotification(self._hwnd, NOTIFY_FOR_THIS_SESSION)

        self._hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kb_proc_ref, h_instance, 0
        )

        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook_handle:
            user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None
        if self._hwnd:
            wtsapi32.WTSUnRegisterSessionNotification(self._hwnd)
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
