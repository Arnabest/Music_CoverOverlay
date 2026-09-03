"""System tray — matches original: dynamic checkboxes, live menu rebuild."""
import os, sys, threading, queue, time
from PIL import Image as PILImage
import pystray


class TrayManager:
    def __init__(self, cmd_queue: queue.Queue, get_state, icon_path: str | None = None):
        self._queue = cmd_queue
        self._get_state = get_state
        self._icon_path = icon_path
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._gl_queue: queue.Queue | None = None  # ★ Direct GL queue bypass
        self._action_handler = None  # ★ Direct callable (nuclear option)

    def set_gl_queue(self, gl_queue: queue.Queue) -> None:
        """Set the engine's GL queue for direct pyglet-thread action dispatch.

        When set, tray menu items push actions to BOTH cmd_queue (legacy)
        AND gl_queue (bypass). The gl_queue is drained at the top of every
        frame in _scheduled_update, making it the most reliable path.
        """
        self._gl_queue = gl_queue

    def set_action_handler(self, handler) -> None:
        """Set a direct callable for tray menu actions.

        This is the NUCLEAR OPTION: bypasses ALL queues entirely.
        The handler is called DIRECTLY from the tray's daemon thread
        when a menu item is clicked. No queue, no scheduler, no delay.

        The handler receives the command string ('toggle_topmost',
        'show_window', etc.) and must be thread-safe.
        """
        self._action_handler = handler

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._icon = self._build_icon()
                self._icon.run()
            except Exception as e:
                print(f"[Tray] Error: {e}")
                time.sleep(1)

    def _build_icon(self):
        img = self._load_icon()
        menu = self._build_menu()
        icon = pystray.Icon("Music_CoverOverlay", img, "Music CoverOverlay", menu=menu)
        return icon

    def _load_icon(self):
        if self._icon_path and os.path.exists(self._icon_path):
            try:
                return PILImage.open(self._icon_path).resize((64, 64), PILImage.LANCZOS).convert("RGBA")
            except Exception:
                pass
        return PILImage.new("RGBA", (64, 64), (68, 68, 170, 255))

    def _build_menu(self):
        state = self._get_state() if self._get_state else {}
        atop = state.get("always_on_top", True)
        ct = state.get("click_through", False)
        song = state.get("song", "")
        artist = state.get("artist", "")

        # Markers for toggle state
        chk_on = "  [x] "
        chk_off = "  [ ] "

        # Helper: call the action handler directly (nuclear option).
        # Queues (cmd_queue, gl_queue) are kept as fallbacks only.
        _tray_log = os.path.join(os.environ.get("TEMP", "."), "qqoverlay_tray_diag.log")
        def _dispatch(cmd: str):
            # Diagnostic
            try:
                import datetime as _dt
                _ah = "SET" if self._action_handler is not None else "None"
                _gl = "SET" if self._gl_queue is not None else "None"
                with open(_tray_log, "a", encoding="utf-8") as _f:
                    _f.write(f"[{_dt.datetime.now():%H:%M:%S.%f}] TRAY CLICK: {cmd} (action_handler={_ah}, gl_queue={_gl})\n")
            except Exception:
                pass
            # ★ NUCLEAR OPTION: call the action handler directly.
            # No queue, no scheduler, no delay. The handler runs on
            # this (tray daemon) thread and must be GIL-safe.
            if self._action_handler is not None:
                try:
                    self._action_handler(cmd)
                except Exception as e:
                    try:
                        with open(_tray_log, "a", encoding="utf-8") as _f:
                            import traceback
                            _f.write(f"  ACTION HANDLER CRASHED: {e}\n{traceback.format_exc()}\n")
                    except Exception:
                        pass
            # Fallbacks (kept for redundancy)
            self._queue.put(cmd)
            if self._gl_queue is not None:
                self._gl_queue.put(cmd)

        items = []

        # ---- Song info (if playing) ----
        if song:
            items.append(pystray.MenuItem(f"  {song[:30]}", None, enabled=False))
            if artist:
                items.append(pystray.MenuItem(f"  - {artist[:30]}", None, enabled=False))
            items.append(pystray.Menu.SEPARATOR)

        # ---- Actions ----
        items.append(pystray.MenuItem("  打开设置", lambda: _dispatch("open_settings")))
        items.append(pystray.Menu.SEPARATOR)

        # ---- Show / Hide ----
        items.append(pystray.MenuItem("  显示窗口", lambda: _dispatch("show_window"), default=True))
        items.append(pystray.MenuItem("  隐藏窗口", lambda: _dispatch("hide_window")))
        items.append(pystray.Menu.SEPARATOR)

        # ---- State toggles with checkmarks ----
        items.append(pystray.MenuItem(
            f"{chk_on if atop else chk_off}窗口置顶",
            lambda: _dispatch("toggle_topmost")))
        items.append(pystray.MenuItem(
            f"{chk_on if ct else chk_off}鼠标穿透",
            lambda: _dispatch("toggle_clickthrough")))
        items.append(pystray.Menu.SEPARATOR)

        # ---- Exit ----
        items.append(pystray.MenuItem("  退出", lambda: _dispatch("do_close")))

        return pystray.Menu(*items)

    def update_menu(self):
        """Rebuild menu to reflect current toggle state."""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.menu = self._build_menu()
            icon.update_menu()
        except Exception as e:
            print(f"[Tray] update_menu error: {e}", flush=True)
            import traceback
            traceback.print_exc()

    def stop(self):
        self._stop_event.set()
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
        self._icon = None
        # ★ Wait for the daemon thread to call NIM_DELETE.
        # pystray.Icon.stop() signals the event loop but returns
        # immediately.  The daemon thread needs ~50-200ms to process
        # the signal, call Shell_NotifyIcon(NIM_DELETE), and exit.
        # 0.5s is ample — the old 3.0s was the #1 cause of slow shutdown.
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
