deep"""QQMusic Overlay NG — Desktop lyrics/cover display tool."""
import sys, os, time, queue, ctypes, threading, json

# High-DPI
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)


def _find_overlay_pids():
    """Find PIDs of any running QQMusicOverlay NG processes by window title."""
    import ctypes, ctypes.wintypes
    pids = []
    _user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _enum(hwnd, __):
        buf = (ctypes.c_wchar * 256)()
        if _user32.GetWindowTextW(hwnd, buf, 256):
            if "QQMusic Overlay NG" in buf.value:
                pid = ctypes.wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    pids.append(pid.value)
        return True
    _user32.EnumWindows(WNDENUMPROC(_enum), 0)
    return list(set(pids))

def main():
    # ★ Clean up any stale QQMusicOverlay processes before starting.
    # Duplicate GL contexts cause GPU contention → periodic 96fps frame drops.
    import ctypes
    _kernel32 = ctypes.windll.kernel32
    _killed = 0
    for _pid in _find_overlay_pids():
        try:
            _h = _kernel32.OpenProcess(1, False, _pid)
            if _h:
                _kernel32.TerminateProcess(_h, 0)
                _kernel32.CloseHandle(_h)
                _killed += 1
        except Exception:
            pass
    if _killed:
        import time as _t; _t.sleep(0.5)

    # ★ Single-instance mutex: prevents accidental double-launch.
    # If mutex exists, check if the owning process is still alive.
    _mutex = _kernel32.CreateMutexW(None, False, "QQMusicOverlayNG_SingleInstance")
    if _kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # Check if existing process is dead (stale mutex)
        _stale_pids = _find_overlay_pids()
        if not _stale_pids:
            # No overlay process found — stale mutex from crashed process
            _kernel32.CloseHandle(_mutex)
            _mutex = _kernel32.CreateMutexW(None, False, "QQMusicOverlayNG_SingleInstance")
        else:
            import tkinter.messagebox as _mb
            _mb.showwarning("QQMusicOverlay NG", "应用已在运行中")
            return
    _ = _mutex

    # ★ Watchdog heartbeat thread — writes timestamp to heartbeat file.
    # If heartbeat is stale (>10s), external monitor knows the app is dead.
    import threading as _thr
    _watchdog_running = True
    def _watchdog():
        while _watchdog_running:
            try:
                with open(os.path.join(os.environ.get("TEMP", "."),
                          "qqoverlay_heartbeat.txt"), "w") as _wf:
                    _wf.write(str(time.time()))
            except Exception:
                pass
            time.sleep(2.0)
    _wd_thread = _thr.Thread(target=_watchdog, daemon=True, name="watchdog")
    _wd_thread.start()

    # Shared state for tray
    status = {"song": "", "artist": "", "always_on_top": True, "click_through": False}
    cmd_queue = queue.Queue()

    # Icon path
    icon_path = os.path.join(PROJECT_DIR, "assets", "app_icon.png")
    if not os.path.exists(icon_path):
        old = os.path.join(os.path.dirname(PROJECT_DIR), "QQMusicOverlay", "app_icon.png")
        if os.path.exists(old):
            import shutil; shutil.copy(old, icon_path)

    # ====== Config + Fonts FIRST (tray menu needs correct initial state) ======
    from config import load_config, register_custom_fonts
    config = load_config()
    register_custom_fonts()
    # Trim oversized log files on each startup
    from core.logs import trim_logs
    trim_logs(config.get("behavior", {}).get("max_log_lines", 500))
    # Auto-clean polluted learned durations from previous SMTC-freeze sessions
    try:
        from core.duration import clean_suspicious_durations
        _cleaned = clean_suspicious_durations()
        if _cleaned > 0:
            from core.logs import get_logger
            get_logger("main").info(f"Startup: cleaned {_cleaned} suspicious duration entries")
    except Exception:
        pass

    # ★ Log version on startup
    from version import get_version, get_build_stamp
    from core.logs import get_logger as _get_sys_log
    _sys = _get_sys_log("system")
    _sys.info(f"startup: {get_version()} (build {get_build_stamp()})")

    status["always_on_top"] = config.get("window", {}).get("always_on_top", True)
    status["click_through"] = config.get("window", {}).get("click_through", False)

    # ====== Tray (now with correct initial state) ======
    from ui.tray import TrayManager
    tray = TrayManager(cmd_queue, lambda: status, icon_path)
    tray.start()
    time.sleep(0.3)

    # ====== Reader ======
    from core.reader import QQMusicReader
    reader = QQMusicReader()
    reader.set_picture_dir(config.get("behavior", {}).get("qqmusic_picture_dir", ""))
    reader.set_detection_priority(config.get("behavior", {}).get("detection_priority", ["qqmusic", "netease"]))
    reader.set_lyrics_source_priority(config.get("behavior", {}).get("lyrics_source_priority", "qq_first"))

    # ====== SMTC Engine (isolated — runs on its own daemon thread) ======
    # ★ Manager MUST be created on the main thread first. WinRT requires
    # the async request to complete on a thread with a message pump.
    # The daemon thread's MTA init (CoInitializeEx) is unreliable on
    # Python 3.14 for WinRT components.
    from core.smtc_engine import SmtcEngine
    SmtcEngine.init_manager_on_main_thread()
    smtc_engine = SmtcEngine(poll_interval=0.5)
    smtc_engine.set_priority(config.get("behavior", {}).get("detection_priority", ["qqmusic", "netease"]))
    smtc_engine.start()

    # ====== Overlay Window ======
    from ui.window import OverlayWindow
    overlay = OverlayWindow(config, reader, status, smtc_engine=smtc_engine)
    overlay.set_tray_queue(cmd_queue)

    # Settings — always inline (same Tk event loop). No subprocess, no IPC,
    # no orphaned windows. Exiting the overlay destroys all Toplevels cleanly.
    def open_settings():
        from ui.settings import SettingsWindow
        _existing = getattr(overlay, '_settings_window', None)
        if _existing is not None:
            try:
                _existing.window.lift()
                _existing.window.focus_force()
            except Exception:
                pass
            return
        sw = SettingsWindow(overlay, overlay.config, reader,
                           on_apply=lambda c: overlay.reload_config(c),
                           on_close=lambda: setattr(overlay, '_settings_window', None))
        overlay._settings_window = sw

    overlay.set_settings_callback(open_settings)

    # Tray menu live refresh after state changes
    def refresh_tray():
        status["always_on_top"] = overlay.config.get("window", {}).get("always_on_top", True)
        status["click_through"] = overlay.config.get("window", {}).get("click_through", False)
        tray.update_menu()

    overlay.set_tray_refresh(refresh_tray)

    # ====== Run ======
    try:
        overlay.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        from core.logs import get_logger as _gl
        import traceback as _tb
        _gl("system").error(f"FATAL: overlay.run() crashed:\n{_tb.format_exc()}")
    finally:
        # Stop watchdog
        _watchdog_running = False
        tray.stop()
        try:
            from config import save_config
            save_config(overlay.config)
        except Exception:
            pass
        try:
            smtc_engine.stop()
        except Exception:
            pass
        # Force-kill any remaining overlay processes
        for _pid in _find_overlay_pids():
            try:
                _h = _kernel32.OpenProcess(1, False, _pid)
                if _h:
                    _kernel32.TerminateProcess(_h, 0)
                    _kernel32.CloseHandle(_h)
            except Exception:
                pass
        os._exit(0)


if __name__ == "__main__":
    main()
