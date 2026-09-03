"""Rendering engine — tkinter transparent window + full feature parity.

All features from original QQMusicOverlay:
- Right-click context menu (settings, show/hide, element toggles, song info, topmost, click-through, exit)
- Drag to move window
- Scroll wheel opacity adjustment
- Click-through via tkinter event interception
- DPI scaling
"""
import time, ctypes, os
from ctypes import wintypes
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk

from scene.graph import SceneGraph
from render.compositor import Compositor

TRANSPARENT_KEY = "#010101"


class RenderEngine:
    """Manages the tkinter overlay window, render loop, and all user interactions."""

    def __init__(self, config: dict, scene: SceneGraph, compositor: Compositor):
        self.config = config
        self.scene = scene
        self.compositor = compositor

        win_cfg = config.get("window", {})
        anim_cfg = config.get("animation", {})

        self.width = int(win_cfg.get("size", {}).get("width", 400))
        self.height = int(win_cfg.get("size", {}).get("height", 550))
        self.target_fps = int(anim_cfg.get("fps", 60))
        self.window_opacity = win_cfg.get("opacity", 0.85)
        self.always_on_top = win_cfg.get("always_on_top", True)
        self.click_through = win_cfg.get("click_through", False)

        # Callbacks (set by OverlayWindow)
        self._data_provider = lambda: {}
        self._on_settings = None
        self._on_show = None
        self._on_hide = None
        self._on_exit = None
        self._on_topmost_toggle = None
        self._on_clickthrough_toggle = None
        self._on_element_toggle = None
        self._on_drag_save = None
        self._on_update = None
        self._reader = None  # For song info popup

        # State
        self.root: tk.Tk | None = None
        self.canvas: tk.Canvas | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._running = False
        # FPS tracking
        self._fps_samples: list[float] = []
        self._fps_elapsed = 0.0
        self._drag_active = False
        self._drag_x = 0
        self._drag_y = 0
        self._after_id = None

    def set_data_provider(self, provider) -> None:
        self._data_provider = provider

    def set_callbacks(self, **kwargs) -> None:
        """Set action callbacks from OverlayWindow."""
        for key, value in kwargs.items():
            setattr(self, f"_on_{key}", value)

    def set_reader(self, reader) -> None:
        self._reader = reader

    # ================================================================
    # Window creation & mainloop
    # ================================================================

    def run(self) -> None:
        # ★ 1ms timer resolution — tkinter after() defaults to 15.6ms granularity
        #    which limits FPS to ~32-64.  timeBeginPeriod(1) enables ~60+ FPS.
        import ctypes
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.withdraw()  # ★ Hide immediately — prevent ANY flash during setup

        self.root.configure(bg=TRANSPARENT_KEY)
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.always_on_top)
        self.root.attributes("-alpha", 0.0)  # Invisible until show_window()

        pos = self.config.get("window", {}).get("position", {})
        x = pos.get("x", 100)
        y = pos.get("y", 100)
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.root, bg=TRANSPARENT_KEY, highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True)

        # ★ Paint initial blank frame — ensures the canvas never shows
        # default/stale content, even before the first _on_frame runs.
        from PIL import Image, ImageTk
        blank = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        self._photo = ImageTk.PhotoImage(blank)
        self.canvas.create_image(self.width // 2, self.height // 2,
                                 image=self._photo, anchor="center")

        # DPI
        self._setup_dpi()

        # ---- Mouse events ----
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Button-4>", lambda e: self._on_scroll(-1))
        self.canvas.bind("<Button-5>", lambda e: self._on_scroll(1))
        self.canvas.bind("<Button>", self._on_any_button)

        # Taskbar icon
        try:
            ico = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "QQMusicOverlay", "app_icon.ico")
            if os.path.exists(ico):
                self.root.iconbitmap(default=ico)
        except Exception:
            pass

        self._running = True
        self.interval_ms = max(16, 1000 // self.target_fps)
        self._schedule_frame()

        # Bring window into existence (still alpha=0, invisible)
        self.root.update_idletasks()
        self.root.state("normal")
        self.root.deiconify()
        # If window is off-screen, move to visible position
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        if x < -500 or x > 5000 or y < -500 or y > 3000:
            self.root.geometry(f"{self.width}x{self.height}+100+100")
        self.root.lift()
        self.root.focus_force()
        self.root.attributes("-alpha", 0.0)  # Ensure still invisible
        # Write to log file directly (print is captured by tkinter)
        import datetime, os
        try:
            log_path = os.path.join(os.environ.get("TEMP", "."), "qqoverlay_ng.log")
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] [Engine] Window at ({self.root.winfo_x()},{self.root.winfo_y()}) "
                        f"{self.root.winfo_width()}x{self.root.winfo_height()} "
                        f"alpha={self.window_opacity}\n")
        except Exception:
            pass

        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass

    def stop(self) -> None:
        self._running = False
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass

    # ================================================================
    # Render loop
    # ================================================================

    def _schedule_frame(self) -> None:
        if not self._running:
            return
        self._after_id = self.root.after(self.interval_ms, self._on_frame)

    def _on_frame(self) -> None:
        if not self._running:
            return
        _t0 = time.time()
        try:
            # Phase 1: update
            if self._on_update:
                self._on_update(1.0 / max(1, self.target_fps))
        except Exception as e:
            from core.logs import get_logger as _gl; import traceback as _tb
            _gl("error").error(f"PIL frame crashed @update: {e}", exc_info=True)
            self._schedule_frame(); return

        try:
            data = self._data_provider()
        except Exception as e:
            from core.logs import get_logger as _gl; import traceback as _tb
            _gl("error").error(f"PIL frame crashed @data: {e}", exc_info=True)
            self._schedule_frame(); return

        # Guard against late frames after window destruction
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
        except Exception:
            return
        if w < 20 or h < 20:
            self._schedule_frame(); return

        try:
            # Phase 3: compositor render
            pil_image = self.compositor.render_to_pil(self.scene, data, w, h)
        except Exception as e:
            from core.logs import get_logger as _gl; import traceback as _tb
            _gl("error").error(f"PIL frame crashed @compositor: {e}", exc_info=True)
            self._schedule_frame(); return

        try:
            # Phase 4: display — reuse canvas item, recycle PhotoImage
            old_photo = getattr(self, '_photo', None)
            self._photo = ImageTk.PhotoImage(pil_image)
            img_id = getattr(self, '_canvas_img_id', None)
            if img_id:
                self.canvas.itemconfig(img_id, image=self._photo)
            else:
                self._canvas_img_id = self.canvas.create_image(
                    w // 2, h // 2, image=self._photo, anchor="center")
            # Release old Tk image to prevent memory accumulation
            if old_photo is not None:
                try: del old_photo
                except Exception: pass
        except Exception as e:
            from core.logs import get_logger as _gl; import traceback as _tb
            _gl("error").error(f"PIL frame crashed @display: {e}", exc_info=True)

        # ★ Amortize GC: force gen0 collection every 30 frames (~every 0.5s)
        #    to prevent the periodic ~2s GC spike without per-frame overhead.
        self._gc_tick = getattr(self, '_gc_tick', 0) + 1
        if self._gc_tick % 30 == 0:
            import gc
            gc.collect(0)

        # ★ FPS tracking: log to render log (same format as GL engine)
        _dt = time.time() - _t0
        self._fps_samples.append(_dt)
        self._fps_elapsed += _dt
        if self._fps_elapsed >= 2.0:
            count = len(self._fps_samples)
            if count > 0:
                avg_ms = (sum(self._fps_samples) / count) * 1000
                avg_fps = 1.0 / (sum(self._fps_samples) / count) if sum(self._fps_samples) > 0 else 0
                try:
                    from core.logs import get_logger
                    get_logger("render").info(
                        f"FPS {avg_fps:.1f} | frame={avg_ms:.1f}ms | "
                        f"data=0.0ms setup={avg_ms:.1f}ms r+ulw=0.0ms | "
                        f"{count} frames in 2.0s"
                    )
                except Exception:
                    pass
            self._fps_samples.clear()
            self._fps_elapsed = 0.0

        self._schedule_frame()

    # ================================================================
    # Mouse events
    # ================================================================

    def _setup_dpi(self) -> None:
        try:
            hwnd = ctypes.wintypes.HWND(self.root.winfo_id())
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi and dpi != 96:
                self.root.tk.call('tk', 'scaling', dpi / 72.0)
        except Exception:
            pass

    def _on_any_button(self, e) -> str | None:
        """Intercept ALL mouse events. If click-through enabled, return 'break' to stop propagation."""
        if self.click_through:
            return "break"
        return None

    def _on_drag_start(self, e) -> None:
        if self.click_through:
            return
        self._drag_active = True
        self._drag_x = e.x_root
        self._drag_y = e.y_root

    def _on_drag_move(self, e) -> None:
        if not self._drag_active or self.click_through:
            return
        dx = e.x_root - self._drag_x
        dy = e.y_root - self._drag_y
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")
        self._drag_x = e.x_root
        self._drag_y = e.y_root

    def _on_drag_end(self, e) -> None:
        if not self._drag_active:
            return
        self._drag_active = False
        # Save position to config
        self.config.setdefault("window", {})["position"] = {
            "x": self.root.winfo_x(),
            "y": self.root.winfo_y(),
        }
        from config import save_config
        save_config(self.config)

    def _on_scroll(self, delta) -> None:
        """Scroll wheel — adjust window opacity."""
        if self.click_through:
            return
        cur = self.config.get("window", {}).get("opacity", 0.85)
        new = max(0.05, min(1.0, cur + delta * 0.05))
        self.config.setdefault("window", {})["opacity"] = new
        self.window_opacity = new
        self.root.attributes("-alpha", new)
        from config import save_config
        save_config(self.config)

    # ================================================================
    # Right-click context menu (FULL feature parity with original)
    # ================================================================

    def _on_right_click(self, e=None) -> None:
        if self.click_through:
            return
        menu = tk.Menu(self.root, tearoff=0, bg="#2a2a3e", fg="white",
                       activebackground="#4444aa", activeforeground="white")

        menu.add_command(label="  打开设置界面", command=self._menu_settings)
        menu.add_separator()
        menu.add_command(label="  显示窗口", command=self._menu_show)
        menu.add_command(label="  隐藏窗口", command=self._menu_hide)
        menu.add_separator()

        # Element visibility toggles
        _LABELS = {"cover": "封面", "title": "歌名", "artist": "歌手", "lyrics": "歌词"}
        for eid, lb in _LABELS.items():
            visible = self._get_node_visible(eid)
            menu.add_command(
                label=f"  {'[x]' if visible else '[ ]'} {lb}",
                command=lambda eid=eid: self._menu_toggle_element(eid))

        menu.add_separator()
        menu.add_command(label="  歌曲信息", command=self._menu_song_info)
        menu.add_command(label="  窗口置顶", command=self._menu_toggle_topmost)
        menu.add_command(label="  鼠标穿透", command=self._menu_toggle_clickthrough)
        menu.add_separator()
        menu.add_command(label="  退出", command=self._menu_exit)

        # ★ Store reference so click-through can dismiss it
        self._ctx_menu = menu
        if e:
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()
                self._ctx_menu = None

    def _get_node_visible(self, eid: str) -> bool:
        """Check if a node is visible in the scene."""
        node = self.scene.get(eid)
        if node:
            return node.visible
        return True

    def _toggle_node_visible(self, eid: str) -> None:
        """Toggle visibility of a scene node."""
        node = self.scene.get(eid)
        if node:
            node.visible = not node.visible
            # Re-resolve layout since sizes changed
            self.scene.mark_dirty()

    # ---- Menu actions ----

    def _menu_settings(self) -> None:
        if self._on_settings:
            self._on_settings()

    def _menu_show(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        self.root.attributes("-topmost", self.always_on_top)
        self.root.attributes("-alpha", self.window_opacity)

    def _menu_hide(self) -> None:
        self.root.attributes("-alpha", 0.0)
        self.root.withdraw()

    def _menu_toggle_element(self, eid: str) -> None:
        self._toggle_node_visible(eid)

    def _menu_song_info(self) -> None:
        """Show song info popup (matches original behavior exactly)."""
        if not self._reader:
            return
        info_win = tk.Toplevel(self.root)
        info_win.title("")
        info_win.geometry("400x150")
        info_win.configure(bg="#2a2a3e")
        info_win.attributes("-topmost", self.always_on_top)
        info_win.overrideredirect(True)

        # Close on click anywhere
        info_win.bind("<Button-1>", lambda e: info_win.destroy())
        info_win.bind("<Button-3>", lambda e: info_win.destroy())

        tk.Label(info_win, text=" 正在播放:", fg="#888", bg="#2a2a3e",
                 font=("Microsoft YaHei", 11)).pack(pady=(20, 5))
        tk.Label(info_win, text=self._reader.song_name or "未知", fg="white",
                 bg="#2a2a3e", font=("Microsoft YaHei", 14, "bold")).pack()
        tk.Label(info_win, text=self._reader.artist_name or "未知", fg="#aaa",
                 bg="#2a2a3e", font=("Microsoft YaHei", 12)).pack(pady=(2, 15))
        tk.Button(info_win, text="关闭", command=info_win.destroy,
                  bg="#4444aa", fg="white", bd=0, padx=12, pady=4,
                  font=("Microsoft YaHei", 10)).pack()

        cx = self.root.winfo_x() + self.root.winfo_width() // 2 - 200
        cy = self.root.winfo_y() + self.root.winfo_height() // 2 - 75
        info_win.geometry(f"+{cx}+{cy}")
        info_win.lift()
        info_win.focus_force()

    def _menu_toggle_topmost(self) -> None:
        new_v = not self.always_on_top
        self.always_on_top = new_v
        self.config.setdefault("window", {})["always_on_top"] = new_v
        self.root.attributes("-topmost", new_v)
        from config import save_config
        save_config(self.config)
        if self._on_tray_refresh:
            self._on_tray_refresh()

    def _menu_toggle_clickthrough(self) -> None:
        new_v = not self.click_through
        self.click_through = new_v
        self.config.setdefault("window", {})["click_through"] = new_v
        from config import save_config
        save_config(self.config)
        if self._on_tray_refresh:
            self._on_tray_refresh()

    def _menu_exit(self) -> None:
        if self._on_exit:
            # ★ Defer via after() — calling root.destroy() directly from
            # inside the tk_popup callback corrupts Tk's grab/event state
            # and prevents Toplevel windows (settings) from being cleaned
            # up properly. One trip through the event loop lets the popup
            # dismiss cleanly first.
            self.root.after(1, self._on_exit)

    # ================================================================
    # Window controls (called from OverlayWindow)
    # ================================================================

    def set_click_through(self, enabled: bool) -> None:
        self.click_through = enabled
        # ★ If enabling click-through, dismiss any open context menu.
        #    Otherwise clicks pass through and can't dismiss the menu.
        if enabled and hasattr(self, '_ctx_menu') and self._ctx_menu:
            try:
                self._ctx_menu.unpost()
                self._ctx_menu = None
            except Exception:
                pass

    def set_always_on_top(self, enabled: bool) -> None:
        self.always_on_top = enabled
        if self.root:
            self.root.attributes("-topmost", enabled)

    def set_opacity(self, opacity: float) -> None:
        self.window_opacity = max(0.05, min(1.0, opacity))
        if self.root:
            self.root.attributes("-alpha", self.window_opacity)

    def set_background(self, color: str, alpha: float) -> None:
        """No-op in PIL engine (compositor handles background via update_config)."""
        pass

    def show(self) -> None:
        """Show window (restore from hidden state)."""
        if not self.root:
            return
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        self.root.attributes("-topmost", self.always_on_top)
        # ★ Don't set alpha here — let the animation/transition control it
        # via the `alpha` property. We only restore window geometry/state.

    def hide(self) -> None:
        """Hide window (fully transparent, but preserve canvas for next transition)."""
        if not self.root:
            return
        self.root.attributes("-alpha", 0.0)
        self.root.withdraw()
        # Canvas content is intentionally preserved — the last rendered frame
        # stays on the canvas so the next show_window() transition has a starting point.

    # ---- Animatable alpha property (for Tween-based window fades) ----

    @property
    def alpha(self) -> float:
        """Current window alpha (0.0=transparent, 1.0=opaque).
        Animatable — Tweens can target engine.alpha for window-level fades.
        """
        return self.window_opacity

    @alpha.setter
    def alpha(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        self.window_opacity = value
        if self.root:
            try:
                self.root.attributes("-alpha", value)
            except Exception:
                pass

    def get_position(self) -> tuple[int, int]:
        if self.root:
            return (self.root.winfo_x(), self.root.winfo_y())
        return (0, 0)

    def cleanup(self) -> None:
        self.stop()
