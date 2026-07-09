"""Window and process detection for QQ Music and NetEase Cloud Music.

Extracted from core.reader to isolate Win32 window enumeration and
process detection from the reader's web API and cover-matching logic.
"""
import ctypes, ctypes.wintypes, threading, time, os
from core.logs import get_logger

_wd_log = get_logger("window_detect")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
_enum_results = []


def _enum_windows_callback(hwnd, _lparam):
    length = user32.GetWindowTextLengthW(hwnd) + 1
    if length <= 1:
        return True
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    title = buf.value
    if title:
        _enum_results.append((hwnd, title))
    return True


EW_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)


# Cache for find_qqmusic_window — EnumWindows is expensive (~200ms).
# QQ Music window handle is stable; re-enumerate only when cache expires
# or the cached handle becomes invalid.
_find_window_cache = {"hwnd": 0, "title": "", "time": 0.0}


def _is_valid_window(hwnd: int) -> str | None:
    """Quick check: is hwnd still a valid QQ Music window? Returns title or None."""
    if not hwnd or not user32.IsWindow(hwnd):
        return None
    length = user32.GetWindowTextLengthW(hwnd) + 1
    if length <= 1:
        return None
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    title = buf.value
    if title and " - " in title:
        return title.strip()
    return None


# ========== Process Detection (templated) ==========

_PROCESS_DETECT_CACHE: dict[str, dict] = {}  # exe_name → {"val": bool, "t": float}


def _is_process_running(exe_name: str) -> bool:
    """Check if a process matching `exe_name` exists.

    Uses CreateToolhelp32Snapshot. Cached per exe_name for 5s.
    """
    now = time.time()
    cache = _PROCESS_DETECT_CACHE.get(exe_name)
    if cache and now - cache["t"] < 5.0:
        return cache["val"]

    try:
        import ctypes.wintypes as _w
        TH32CS_SNAPPROCESS = 0x00000002
        _k32 = ctypes.windll.kernel32

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", _w.DWORD), ("cntUsage", _w.DWORD),
                ("th32ProcessID", _w.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(_w.ULONG)),
                ("th32ModuleID", _w.DWORD), ("cntThreads", _w.DWORD),
                ("th32ParentProcessID", _w.DWORD), ("pcPriClassBase", _w.LONG),
                ("dwFlags", _w.DWORD), ("szExeFile", _w.WCHAR * 260),
            ]

        snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            _PROCESS_DETECT_CACHE[exe_name] = {"val": False, "t": now}
            return False

        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = False
        _filter = exe_name.lower()
        if _k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                if _filter in entry.szExeFile.lower():
                    found = True
                    break
                if not _k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        _k32.CloseHandle(snap)
        _PROCESS_DETECT_CACHE[exe_name] = {"val": found, "t": now}
        return found
    except Exception:
        _PROCESS_DETECT_CACHE[exe_name] = {"val": False, "t": now}
        return False


def _is_qqmusic_running() -> bool:
    return _is_process_running("qqmusic")


def _is_netease_running() -> bool:
    return _is_process_running("cloudmusic")


def find_qqmusic_window():
    global _enum_results, _find_window_cache
    now = time.time()
    cache = _find_window_cache

    # Fast path: cached HWND is still valid (< 10s since last enum)
    if cache["hwnd"] and now - cache["time"] < 10.0:
        title = _is_valid_window(cache["hwnd"])
        if title:
            return cache["hwnd"], title
        # ★ Window might still be alive but title isn't parseable
        # (e.g. QQ Music briefly shows "QQ音乐" during song transitions).
        # Only enter cooldown if the window handle itself is gone.
        if not user32.IsWindow(cache["hwnd"]):
            _find_window_cache = {"hwnd": 0, "title": "", "time": now}
        return None, None

    # ★ Cache expired but HWND still valid → return stale cache + background refresh
    if cache["hwnd"]:
        stale_title = _is_valid_window(cache["hwnd"])
        if stale_title:
            _schedule_window_refresh()
            return cache["hwnd"], stale_title
        if not user32.IsWindow(cache["hwnd"]):
            _find_window_cache = {"hwnd": 0, "title": "", "time": now}
        return None, None

    # ★ No cached HWND (hwnd=0) → only re-enumerate every 30s
    if cache["hwnd"] == 0 and now - cache["time"] < 30.0:
        return None, None

    # Slow path: re-enumerate all windows
    _do_full_window_enum(now)
    cache = _find_window_cache
    if cache["hwnd"]:
        return cache["hwnd"], cache["title"]
    return None, None


# Cache for find_netease_window — same pattern as QQ Music.
_find_netease_cache = {"hwnd": 0, "title": "", "time": 0.0}


def find_netease_window():
    """Find NetEase Cloud Music window. Returns (hwnd, title) or (None, None).

    NetEase window title is typically just '网易云音乐' — it does NOT carry
    song/artist info. This function is only used for is_playing detection.
    Uses the same caching strategy as find_qqmusic_window().
    """
    global _enum_results, _find_netease_cache
    now = time.time()
    cache = _find_netease_cache

    # Fast path: cached HWND is still valid (< 10s since last enum)
    if cache["hwnd"] and now - cache["time"] < 10.0:
        if user32.IsWindow(cache["hwnd"]):
            return cache["hwnd"], cache["title"]
        _find_netease_cache = {"hwnd": 0, "title": "", "time": now}
        return None, None

    # Cache expired but HWND still valid
    if cache["hwnd"]:
        if user32.IsWindow(cache["hwnd"]):
            _find_netease_cache["time"] = now
            return cache["hwnd"], cache["title"]
        _find_netease_cache = {"hwnd": 0, "title": "", "time": now}
        return None, None

    # No cached HWND → 30s cooldown
    if cache["hwnd"] == 0 and now - cache["time"] < 30.0:
        return None, None

    # Slow path: enumerate windows looking for OrpheusBrowserHost
    _own_pid = os.getpid()
    _enum_results = []
    user32.EnumWindows(EW_CB(_enum_windows_callback), 0)
    for hwnd, title in _enum_results:
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == _own_pid:
            continue
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value == "OrpheusBrowserHost":
            # Verify process is cloudmusic.exe
            proc = kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
            if proc:
                exe_buf = ctypes.create_unicode_buffer(260)
                size = ctypes.wintypes.DWORD(260)
                ok = kernel32.QueryFullProcessImageNameW(proc, 0, exe_buf, ctypes.byref(size))
                kernel32.CloseHandle(proc)
                if ok and "cloudmusic" in exe_buf.value.lower():
                    _find_netease_cache = {"hwnd": hwnd, "title": title.strip(),
                                           "time": now}
                    return hwnd, title.strip()
    _find_netease_cache = {"hwnd": 0, "title": "", "time": now}
    return None, None


# ---- Background window refresh ----
_refresh_lock = threading.Lock()
_refresh_scheduled = False


def _schedule_window_refresh():
    """Schedule a non-blocking window enumeration in a background thread."""
    global _refresh_scheduled
    with _refresh_lock:
        if _refresh_scheduled:
            return
        _refresh_scheduled = True
    threading.Thread(target=_do_window_refresh, daemon=True, name="window-refresh").start()


def _do_window_refresh():
    global _refresh_scheduled
    try:
        _do_full_window_enum(time.time())
    except Exception as e:
        _wd_log.warn(f"background window refresh failed: {e}")
    finally:
        with _refresh_lock:
            _refresh_scheduled = False


def _do_full_window_enum(now: float | None = None):
    """Enumerate all windows and update the cache (may block 50-200ms)."""
    global _enum_results, _find_window_cache
    if now is None:
        now = time.time()
    _enum_results = []
    user32.EnumWindows(EW_CB(_enum_windows_callback), 0)

    # ★ Exclude the overlay's own process ID to prevent self-detection.
    # Strategy 3 would otherwise match "QQMusic Overlay NG" window title.
    _own_pid = os.getpid()

    # Strategy 1: TXGuiFoundation
    main_window = None
    for hwnd, title in _enum_results:
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value == "TXGuiFoundation" and title.strip():
            if " - " in title:
                _find_window_cache = {"hwnd": hwnd, "title": title.strip(), "time": now}
                return
            if main_window is None:
                main_window = (hwnd, title.strip())
    if main_window:
        hwnd, title = main_window
        _find_window_cache = {"hwnd": hwnd, "title": title, "time": now}
        return
    # Strategy 2: QQMusic.exe process
    for hwnd, title in _enum_results:
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == _own_pid:
            continue
        proc = kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
        if proc:
            exe_buf = ctypes.create_unicode_buffer(260)
            size = ctypes.wintypes.DWORD(260)
            kernel32.QueryFullProcessImageNameW(proc, 0, exe_buf, ctypes.byref(size))
            kernel32.CloseHandle(proc)
            if "QQMusic" in exe_buf.value and title.strip():
                _find_window_cache = {"hwnd": hwnd, "title": title.strip(), "time": now}
                return
    # Strategy 3: title contains QQMusic — verify process to avoid false positives
    # (VS Code editing QQMusicOverlay project, other apps with "QQMusic" in title).
    for hwnd, title in _enum_results:
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == _own_pid:
            continue
        t = title.strip()
        if not t or ("QQMusic" not in t and "QQ音乐" not in t):
            continue
        # Verify process: if we can open it, confirm it's QQ Music.
        # If we can't open it (permissions), accept the title as a tentative match.
        proc = kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
        if proc:
            exe_buf = ctypes.create_unicode_buffer(260)
            size = ctypes.wintypes.DWORD(260)
            ok = kernel32.QueryFullProcessImageNameW(proc, 0, exe_buf, ctypes.byref(size))
            kernel32.CloseHandle(proc)
            if ok:
                exe_lower = exe_buf.value.lower()
                if "qqmusic" not in exe_lower:
                    continue  # Not QQ Music — skip (e.g. VS Code, editor, etc.)
        _find_window_cache = {"hwnd": hwnd, "title": t, "time": now}
        return
    _find_window_cache = {"hwnd": 0, "title": "", "time": now}


# ========== Player detection dispatchers ==========

def _is_player_running(priority: list[str]) -> bool:
    """Check if any player in the priority list is running."""
    for p in priority:
        if p == "qqmusic" and _is_qqmusic_running():
            return True
        if p == "netease" and _is_netease_running():
            return True
    return False


def find_player_window(priority: list[str]):
    """Find window for the highest-priority player that has one.

    Returns (hwnd, title) or (None, None).
    Tries players in priority list order.
    """
    for p in priority:
        if p == "qqmusic":
            hwnd, title = find_qqmusic_window()
            if hwnd:
                return hwnd, title
        elif p == "netease":
            hwnd, title = find_netease_window()
            if hwnd:
                return hwnd, title
    return None, None


def parse_window_title(title):
    if not title or " - " not in title:
        return None, None
    parts = title.split(" - ", 1)
    song = parts[0].strip()
    artist = parts[1].strip()
    if song and artist:
        return song, artist
    return None, None
