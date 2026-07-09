"""OpenGL rendering engine — pyglet window + GPU shader pipeline.

Per-pixel alpha compositing via UpdateLayeredWindow (Windows DWM).
Renders scene to an offscreen FBO each frame, reads the pixel data,
and composites it onto the desktop with full alpha support.

Interface matches RenderEngine (tkinter) so OverlayWindow can swap backends
by changing a single config key.
"""
import os
import time
import ctypes
from ctypes import wintypes
from typing import Optional

from PIL import Image
import pyglet
from pyglet.gl import (
    GL_COLOR_BUFFER_BIT, glClear, glClearColor,
    glViewport, glEnable, GL_BLEND,
    GL_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA, glBlendFunc,
    GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
    GL_TEXTURE_2D, GL_RGBA, GL_BGRA, GL_UNSIGNED_BYTE,
    GL_LINEAR, GL_NEAREST, GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE,
    GL_FRAMEBUFFER_COMPLETE,
    glGenFramebuffers, glBindFramebuffer, glDeleteFramebuffers,
    glFramebufferTexture2D, glCheckFramebufferStatus,
    glGenTextures, glBindTexture, glDeleteTextures,
    glTexImage2D, glTexParameteri,
    glReadPixels, glReadBuffer, GL_BACK,
    GLuint,
    # PBO (Pixel Buffer Object) for async GPU→CPU readback
    GL_PIXEL_PACK_BUFFER, GL_STREAM_READ, GL_READ_ONLY,
    glGenBuffers, glBindBuffer, glDeleteBuffers, glBufferData,
    glMapBuffer, glUnmapBuffer,
)
from pyglet.window import key, mouse

from scene.graph import SceneGraph
from render.painter import Painter
from core.logs import get_logger

_gl_log = get_logger("render")


# ============================================================
# Win32 types and constants
# ============================================================

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOZORDER = 0x0004

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class UPDATELAYEREDWINDOWINFO(ctypes.Structure):
    """Used with UpdateLayeredWindowIndirect — supports prcDirty."""
    _pack_ = 8  # x64 alignment
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("hdcDst", ctypes.c_void_p),
        ("pptDst", ctypes.POINTER(POINT)),
        ("psize", ctypes.POINTER(SIZE)),
        ("hdcSrc", ctypes.c_void_p),
        ("pptSrc", ctypes.POINTER(POINT)),
        ("crKey", ctypes.wintypes.DWORD),
        ("pblend", ctypes.POINTER(BLENDFUNCTION)),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("prcDirty", ctypes.POINTER(RECT)),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def _make_layered(hwnd: int) -> None:
    """Add WS_EX_LAYERED + WS_EX_TOOLWINDOW to a window.

    WS_EX_TOOLWINDOW hides the window from the taskbar.
    SWP_FRAMECHANGED is REQUIRED — without it Windows doesn't re-read
    the extended styles and the taskbar button persists (flashing red
    because a non-foreground window is trying to paint).
    """
    _SWP_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | 0x0020  # SWP_FRAMECHANGED
    ex = _user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
    # ★ Strip WS_EX_APPWINDOW (forces taskbar button) and add TOOLWINDOW + LAYERED
    _user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE,
                           (ex & ~0x00040000) | WS_EX_LAYERED | WS_EX_TOOLWINDOW)
    _user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(0),
                         0, 0, 0, 0, _SWP_FLAGS)


def _set_click_through(hwnd: int, enabled: bool) -> None:
    """Toggle click-through on a layered window."""
    SWP_FRAMECHANGED = 0x0020
    ex = _user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
    if enabled:
        _user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex | WS_EX_TRANSPARENT)
    else:
        _user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex & ~WS_EX_TRANSPARENT)
    # SWP_FRAMECHANGED is REQUIRED for extended-style changes to take effect
    _user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(0), 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED)


def _set_topmost(hwnd: int, enabled: bool) -> None:
    """Set window topmost state. ONLY uses SetWindowPos — the MSDN-approved way."""
    if enabled:
        _user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST),
                             0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    else:
        _user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_NOTOPMOST),
                             0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


# ============================================================
# GL Engine
# ============================================================

class GLRenderEngine:
    """Pyglet-based OpenGL overlay with UpdateLayeredWindow compositing."""

    def __init__(self, config: dict, scene: SceneGraph, compositor):
        self.config = config
        self.scene = scene
        self._compositor = compositor  # accepted for interface compat
        win_cfg = config.get("window", {})
        anim_cfg = config.get("animation", {})

        self.width = int(win_cfg.get("size", {}).get("width", 400))
        self.height = int(win_cfg.get("size", {}).get("height", 550))
        self.target_fps = int(anim_cfg.get("fps", 60))
        self._opacity = win_cfg.get("opacity", 0.85)
        self.always_on_top = win_cfg.get("always_on_top", True)
        self.click_through = win_cfg.get("click_through", False)

        # Background color/opacity (updateable at runtime)
        bg_cfg = win_cfg.get("background", {})
        self._bg_color = tuple(
            c / 255.0 for c in self._hex_to_rgb(bg_cfg.get("color", "#1a1a2e")))
        self._bg_alpha = float(bg_cfg.get("opacity", 1.0))

        # Callbacks
        self._data_provider = lambda: {}
        self._on_settings = None
        self._on_exit = None
        self._on_update = None
        self._on_show = None
        self._on_hide = None
        self._on_topmost_toggle = None
        self._on_clickthrough_toggle = None
        self._on_element_toggle = None
        self._on_tray_refresh = None
        self._on_tray_cmd = None  # ★ Direct tray→gl_queue bypass handler
        self._reader = None

        # State
        self.root = None
        self._window: pyglet.window.Window | None = None
        self._painter: Painter | None = None
        self._running = False
        self._drag_active = False
        self._drag_x = 0
        self._drag_y = 0
        self._hwnd = 0
        self._shown = False

        # ★ Thread-safe queues created early so tray can reference _gl_queue
        # before run() enters the blocking event loop.
        import queue as _q
        self._tk_queue = _q.Queue()
        self._gl_queue = _q.Queue()

        # FBO + DIB + PBO resources (created in run())
        self._fbo = 0
        self._fbo_tex = 0
        self._mem_dc = 0
        self._dib = 0
        self._dib_bits = None
        self._screen_dc = 0
        self._pbo0 = 0  # PBO for async readback (double-buffered)
        self._pbo1 = 0
        self._pbo_index = 0  # which PBO to write into this frame (0 or 1)
        self._pbo_first = True  # first frame has no previous PBO to read from
        self._pixel_buf = None
        self._fbo_w = 0
        self._fbo_h = 0

        # ★ Adaptive FPS: idle at _idle_fps, boost to target_fps on activity.
        # GPU utilisation is dominated by per-frame fixed costs (FBO clear,
        # draw calls, glReadPixels, UpdateLayeredWindow).  When nothing is
        # changing on screen, firing those at 60/120 fps wastes GPU cycles.
        #   - Idle mode:   _idle_fps (10 fps) — sufficient for progress bar
        #                  updates at 1% texture-cache granularity.
        #   - Active mode: target_fps (60/120) — during transitions, spin,
        #                  new song, or any visual change.
        self._idle_fps = 10
        self._active_fps = self.target_fps
        # Continuous FPS cap (Tier 2 — cover spin).  Configurable:
        #   "eco"  → cap at _continuous_fps_cap (default 45fps, power saving)
        #   "full" → match target_fps (old behavior, no cap)
        _cont_mode = anim_cfg.get("continuous_fps_mode", "eco")
        _cont_cap_raw = anim_cfg.get("continuous_fps_cap", 45)
        try:
            _cont_cap = int(str(_cont_cap_raw).split()[0])  # "45 fps（...）" → 45
        except (ValueError, IndexError):
            _cont_cap = 45
        _cont_cap = max(30, min(60, _cont_cap))  # clamp to 30-60 range
        self._continuous_fps = self.target_fps if _cont_mode == "full" else min(_cont_cap, self.target_fps)
        # Fullscreen power saving: when a fullscreen app (game, video) covers
        # the overlay and always_on_top is OFF, apply the selected strategy.
        #   "pause" → stop cover spin entirely (idle FPS)
        #   "30fps" → cap rotation at 30fps
        #   "15fps" → cap rotation at 15fps
        # Only checked every 1s to keep Win32 API overhead negligible.
        self._fullscreen_eco_mode = anim_cfg.get("fullscreen_eco_mode", "pause")
        self._fullscreen_covered = False    # cached detection result
        self._fullscreen_check_time = 0.0   # last detection timestamp
        self._ulw_info = UPDATELAYEREDWINDOWINFO()  # reused struct
        self._ulw_info.cbSize = ctypes.sizeof(UPDATELAYEREDWINDOWINFO)
        self._is_window_hidden = False     # ★ track ShowWindow(SW_HIDE) state
        self._active_until = 0.0          # timestamp — stay boosted until then
        self._active_linger = 2.0         # seconds to stay boosted after last trigger
        self._frame_skip_counter = 0      # counts frames for idle skip pattern
        self._skip_ratio = 0              # computed: ceil(target_fps / idle_fps)
        self._continuous_counter = 0      # frame counter for continuous-mode skip
        self._continuous_ratio = 0        # computed: ceil(target_fps / continuous_fps)
        self._last_data_snap: dict = {}   # snapshot of last rendered frame's data
        self._last_progress_pct = -1      # last rendered progress percentage
        self._frames_since_active = 0     # diagnostics

        # FPS tracking
        self._fps_samples: list[float] = []
        self._fps_elapsed = 0.0
        self._prof_update = 0.0
        self._prof_data = 0.0
        self._prof_setup = 0.0
        self._prof_render = 0.0

    # ================================================================
    # Interface (mirrors RenderEngine)
    # ================================================================

    def set_data_provider(self, provider) -> None:
        self._data_provider = provider

    def set_callbacks(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, f"_on_{k}", v)

    def set_reader(self, reader) -> None:
        self._reader = reader

    @property
    def alpha(self) -> float:
        return self._opacity

    @alpha.setter
    def alpha(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, float(value)))

    # ================================================================
    # Window creation & mainloop
    # ================================================================

    def run(self) -> None:
        pos = self.config.get("window", {}).get("position", {})
        x = pos.get("x", 100)
        y = pos.get("y", 100)

        gl_config = pyglet.gl.Config(
            double_buffer=False,  # We handle compositing ourselves
            alpha_size=8,
            depth_size=0,
            stencil_size=0,
            sample_buffers=0,
        )

        # ★ Create with visible=False so Windows never sees WS_EX_APPWINDOW
        # on this HWND. We apply _make_layered (strip APPWINDOW, add TOOLWINDOW)
        # BEFORE the window first becomes visible → no taskbar button is ever
        # created. Then ShowWindow(SW_SHOWNOACTIVATE) makes it visible without
        # activating (no focus steal). This is safe because pyglet's event loop
        # hasn't started yet; event dispatch works fine once the window is visible.
        self._window = pyglet.window.Window(
            width=self.width,
            height=self.height,
            caption="QQMusic Overlay NG",
            style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
            config=gl_config,
            vsync=False,
            resizable=False,
            visible=False,
        )
        self._window.set_location(x, y)

        # ★ Prevent pyglet's internal _redraw_windows from calling flip() on
        # our window. We render via FBO → glReadPixels → UpdateLayeredWindow,
        # not via pyglet's swap chain. Without this, window.draw() →
        # self.context.flip() crashes because double_buffer=False leaves
        # self.context = None.
        self._window.flip = lambda: None
        self._window.draw = lambda _dt: None

        # Get HWND
        try:
            self._hwnd = self._window._hwnd
        except Exception:
            self._hwnd = ctypes.windll.user32.FindWindowW(None, "QQMusic Overlay NG")

        # ★ Apply layered + toolwindow styles BEFORE the window becomes visible.
        # This ensures Windows never creates a taskbar button for this HWND.
        if self._hwnd:
            _make_layered(self._hwnd)
            _set_topmost(self._hwnd, self.always_on_top)
            _set_click_through(self._hwnd, self.click_through)
            # Now safe to show — styles are correct, no taskbar button
            _user32.ShowWindow(ctypes.c_void_p(self._hwnd), 4)  # SW_SHOWNOACTIVATE

        # Run tkinter in a background thread with its own Tk instance.
        # Completely isolates tkinter from pyglet's event loop.
        import tkinter as tk
        import threading as _thr
        import queue as _q  # needed for _q.Empty refs in _poll() and _scheduled_update
        # ★ _tk_queue and _gl_queue are now created in __init__ so tray can
        # reference _gl_queue before run() enters the blocking event loop.
        self._tk_ready = _thr.Event()
        def _run_tk():
            root = tk.Tk()
            root.withdraw()
            self.root = root
            # Cache HWND for cross-thread WM_CANCELMODE (menu dismissal)
            try:
                self._tk_hwnd = int(root.frame(), 0)
            except Exception:
                self._tk_hwnd = 0
            self._tk_ready.set()
            # Process queued callbacks on the tk thread
            def _poll():
                try:
                    while True:
                        cb = self._tk_queue.get_nowait()
                        try:
                            cb()
                        except Exception:
                            from core.logs import get_logger
                            import traceback as _tb
                            get_logger("error").error(
                                f"tk_queue callback crashed:\n{_tb.format_exc()}")
                except _q.Empty:
                    pass
                root.after(50, _poll)
            root.after(0, _poll)
            root.mainloop()
        _thr.Thread(target=_run_tk, daemon=True, name="tk-mainloop").start()
        self._tk_ready.wait(timeout=3.0)
        # Settings callback: schedule window creation on tk thread
        _orig_on_settings = self._on_settings
        def _wrapped_settings():
            self._tk_queue.put(lambda: _orig_on_settings and _orig_on_settings())
        self._on_settings = _wrapped_settings

        # ★ Pre-clear layered window to transparent (prevents black flash)
        if self._hwnd:
            pt_src = POINT(0, 0)
            pt_dst = POINT(x, y)
            win_size = SIZE(self.width, self.height)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 0, AC_SRC_ALPHA)
            _user32.UpdateLayeredWindow(ctypes.c_void_p(self._hwnd), 0,
                                        ctypes.byref(pt_dst),
                                        ctypes.byref(win_size), 0,
                                        ctypes.byref(pt_src), 0,
                                        ctypes.byref(blend), ULW_ALPHA)

        # Init GL resources
        self._init_fbo()
        self._init_dib()
        self._pixel_buf = (ctypes.c_ubyte * (self.width * self.height * 4))()

        # Init painter
        self._painter = Painter(self.config)
        self._painter.screen_w = self.width
        self._painter.screen_h = self.height
        self._painter.init_gl()

        # Wire pyglet events (pyglet 2.x uses on_xxx = handler)
        # ★ Do NOT wire on_draw — pyglet dispatches it at unbounded rate.
        # Instead, _scheduled_draw calls _on_draw directly with frame cap.
        self._window.on_mouse_press = self._on_mouse_press
        self._window.on_mouse_drag = self._on_mouse_drag
        self._window.on_mouse_release = self._on_mouse_release
        self._window.on_mouse_scroll = self._on_mouse_scroll
        self._window.on_key_press = self._on_key_press

        # ★ Save-config debounce — mouse scroll/release set this flag.
        # save_config() writes JSON to disk synchronously (~5-50ms I/O).
        # Calling it directly from event handlers blocks the render loop.
        # Instead, defer the write to _scheduled_update with a 1-second cooldown.
        self._save_pending = False
        self._save_deadline = 0.0

        # ★ Data update runs on pyglet clock, NOT inside _on_draw.
        self._update_call_count = 0
        def _scheduled_update(dt_scheduled):
            self._update_call_count += 1
            # Drain thread-safe GL action queue (Win32 ops from TK/tray threads)
            drained = 0
            try:
                while True:
                    action = self._gl_queue.get_nowait()
                    try:
                        # ★ Tray bypass: commands arrive as plain strings
                        # (pushed directly from tray menu lambdas via set_gl_queue).
                        # The _on_tray_cmd handler runs the full action logic
                        # (config, save, state transitions, Win32 calls).
                        if isinstance(action, str):
                            _gl_log.info(f"gl_queue tray cmd: {action}")
                            if self._on_tray_cmd:
                                self._on_tray_cmd(action)
                            else:
                                _gl_log.warn(f"gl_queue tray cmd dropped (no handler): {action}")
                        else:
                            action()
                    except Exception:
                        # ★ Don't let a bad queue action kill the scheduler — but LOG it
                        import traceback as _tb2
                        _gl_log.error(f"gl_queue action crashed: {_tb2.format_exc()}")
                    drained += 1
            except _q.Empty:
                pass
            if drained > 0:
                _gl_log.info(f"gl_queue drained {drained} actions")
            # ★ Debounced config save — prevents per-frame disk I/O from
            # mouse scroll events (which fire rapidly and call save_config
            # on every tick, causing multi-ms blocking stalls).
            if self._save_pending and time.time() >= self._save_deadline:
                self._save_pending = False
                from config import save_config
                try:
                    save_config(self.config)
                except Exception as e:
                    _gl_log.warn(f"debounced config save failed: {e}")
            if self._on_update:
                try:
                    self._on_update(dt_scheduled)
                except Exception as e:
                    # ★ Don't let _on_update crash kill the scheduler — but log it
                    _gl_log.error(f"_on_update crashed in scheduler: {e}", exc_info=True)
            # Heartbeat: confirm _scheduled_update fires every ~60s
            if self._update_call_count % 3600 == 1:
                _gl_log.info(f"update #{self._update_call_count} dt={dt_scheduled*1000:.1f}ms")
        pyglet.clock.schedule_interval(_scheduled_update, 1.0 / max(1, self.target_fps))

        # ★ Continuous redraw for WS_EX_LAYERED windows.
        # schedule_interval provides the timing — no secondary guard needed.
        # (The old guard caused frame loss when pyglet's clock drifted
        # even slightly, halving effective FPS to 27-45 instead of 60.)
        self._draw_interval = 1.0 / min(120, max(1, self.target_fps))
        def _scheduled_draw(dt_scheduled):
            if self._window and self._running:
                self._on_draw()
        pyglet.clock.schedule_interval(_scheduled_draw, self._draw_interval)

        self._running = True

        # Log startup
        _gl_log.info(f"{self.width}x{self.height} at ({x},{y}) hwnd={self._hwnd}")

        # ★ Event loop with crash recovery: pyglet can crash on garbled SMTC
        # text (UnicodeDecodeError in _on_update) or context issues. Log and
        # restart silently instead of exiting the process.
        # ★ Crash counter for escalation
        _crash_times: list[float] = []
        while self._running:
            try:
                pyglet.app.run()
            except KeyboardInterrupt:
                break
            except Exception as e:
                from core.logs import get_logger
                err_log = get_logger("error")
                err_log.error(f"pyglet event loop crashed: {e}", exc_info=True)
                _crash_times.append(time.time())
                # Prune old entries
                _crash_times = [t for t in _crash_times if time.time() - t < 10.0]
                if len(_crash_times) > 5:
                    err_log.error(f"CRITICAL: {len(_crash_times)} crashes in 10s, forcing shutdown")
                    self._running = False
                    break
                time.sleep(0.5)

    def stop(self) -> None:
        self._running = False
        if self._window:
            try:
                self._window.close()
            except Exception:
                pass
        # Force pyglet event loop to exit immediately.  window.close() posts
        # WM_CLOSE but the event loop may not process it promptly if blocked
        # on something else.  pyglet.app.exit() breaks the loop directly.
        try:
            import pyglet
            pyglet.app.exit()
        except Exception:
            pass

    def cleanup(self) -> None:
        if self._painter:
            self._painter.cleanup()
        self._delete_fbo()
        self._delete_dib()
        self.stop()

    # ================================================================
    # FBO — offscreen framebuffer for rendering
    # ================================================================

    def _init_fbo(self) -> None:
        """Create FBO + texture for offscreen rendering."""
        w, h = self.width, self.height
        self._fbo = (GLuint * 1)()
        self._fbo_tex = (GLuint * 1)()
        glGenFramebuffers(1, self._fbo)
        glGenTextures(1, self._fbo_tex)

        glBindTexture(GL_TEXTURE_2D, self._fbo_tex[0])
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0,
                     GL_RGBA, GL_UNSIGNED_BYTE, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

        glBindFramebuffer(GL_FRAMEBUFFER, self._fbo[0])
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, self._fbo_tex[0], 0)
        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"FBO incomplete: 0x{status:04X}")
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        self._fbo_w, self._fbo_h = w, h

        # Create double-buffered PBOs for async glReadPixels
        self._init_pbo(w, h)
        self._pbo_first = True

    def _init_pbo(self, w: int, h: int) -> None:
        """Create double-buffered PBOs for asynchronous glReadPixels.

        PBOs allow the GPU to DMA pixel data to system memory without
        stalling the pipeline. One PBO is written by glReadPixels (async)
        while the other is mapped for CPU read (previous frame's data).
        """
        size = w * h * 4
        pbo0 = (GLuint * 1)()
        pbo1 = (GLuint * 1)()
        glGenBuffers(1, pbo0)
        glGenBuffers(1, pbo1)
        self._pbo0, self._pbo1 = pbo0[0], pbo1[0]
        glBindBuffer(GL_PIXEL_PACK_BUFFER, self._pbo0)
        glBufferData(GL_PIXEL_PACK_BUFFER, size, None, GL_STREAM_READ)
        glBindBuffer(GL_PIXEL_PACK_BUFFER, self._pbo1)
        glBufferData(GL_PIXEL_PACK_BUFFER, size, None, GL_STREAM_READ)
        glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)

    def _delete_pbo(self) -> None:
        """Delete PBO resources."""
        try:
            if self._pbo0:
                arr0 = (GLuint * 1)(self._pbo0)
                glDeleteBuffers(1, arr0)
            if self._pbo1:
                arr1 = (GLuint * 1)(self._pbo1)
                glDeleteBuffers(1, arr1)
            self._pbo0 = self._pbo1 = 0
        except Exception:
            pass

    def _delete_fbo(self) -> None:
        try:
            glDeleteFramebuffers(1, self._fbo)
            glDeleteTextures(1, self._fbo_tex)
        except Exception:
            pass
        self._delete_pbo()

    # ================================================================
    # DIB — device-independent bitmap for UpdateLayeredWindow
    # ================================================================

    def _init_dib(self) -> None:
        """Create 32-bit DIB section for UpdateLayeredWindow."""
        w, h = self.width, self.height

        self._screen_dc = _user32.GetDC(0)
        self._mem_dc = _gdi32.CreateCompatibleDC(self._screen_dc)

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = h  # positive = bottom-up DIB (ULW maps buffer[0]→window bottom)
                          # No Y-flip needed — glReadPixels BGRA→DIB is direct copy
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB

        self._dib_bits = ctypes.c_void_p()
        self._dib = _gdi32.CreateDIBSection(
            self._mem_dc,
            ctypes.byref(bmi),
            0,  # DIB_RGB_COLORS
            ctypes.byref(self._dib_bits),
            None, 0,
        )
        _gdi32.SelectObject(self._mem_dc, self._dib)

    def _delete_dib(self) -> None:
        try:
            if self._dib:
                _gdi32.DeleteObject(self._dib)
            if self._mem_dc:
                _gdi32.DeleteDC(self._mem_dc)
            if self._screen_dc:
                _user32.ReleaseDC(0, self._screen_dc)
        except Exception:
            pass

    # ================================================================
    # Window controls
    # ================================================================

    def show(self) -> None:
        """Show the layered window. Idempotent — safe to call repeatedly."""
        self._is_window_hidden = False
        if self._hwnd:
            # ★ Show the window FIRST — UpdateLayeredWindowIndirect (used by
            # _flush_dwm_transparent) needs a visible window.  Showing before
            # flushing is safe because _on_draw skips DWM updates while hidden
            # (fix 6), so the DWM cache is already transparent from hide().
            _user32.ShowWindow(ctypes.c_void_p(self._hwnd), 8)  # SW_SHOWNA
            # ★ Then flush to transparent as a safety net — now that the window
            # is visible, UpdateLayeredWindowIndirect is guaranteed to work.
            self._flush_dwm_transparent()
            insert = HWND_TOPMOST if self.always_on_top else HWND_NOTOPMOST
            _user32.SetWindowPos(ctypes.c_void_p(self._hwnd),
                                 ctypes.c_void_p(insert), 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _flush_dwm_transparent(self) -> None:
        """Update DWM buffer to fully transparent (alpha ≈ 0/255).

        ★ Bug #181: the DIB section retains the last rendered frame across
        ShowWindow(SW_HIDE) → ShowWindow(SW_SHOWNA).  If that frame has a
        full progress bar from the previous song, the user sees a flash.
        This method calls UpdateLayeredWindowIndirect with SourceConstantAlpha=1
        (effectively 0/255 opacity) to make DWM's cached frame invisible,
        so ShowWindow reveals a transparent window.  The next _on_draw then
        composites the correct frame at the proper opacity.
        """
        if not self._hwnd or not hasattr(self, '_mem_dc') or not self._mem_dc:
            return
        w, h = self.width, self.height
        if w < 1 or h < 1:
            return
        try:
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 1, AC_SRC_ALPHA)
            pt_src = POINT(0, 0)
            pt_dst = POINT(self._window.get_location()[0],
                           self._window.get_location()[1])
            win_sz = SIZE(w, h)
            self._ulw_info.hdcDst = 0
            self._ulw_info.pptDst = ctypes.pointer(pt_dst)
            self._ulw_info.psize = ctypes.pointer(win_sz)
            self._ulw_info.hdcSrc = self._mem_dc
            self._ulw_info.pptSrc = ctypes.pointer(pt_src)
            self._ulw_info.crKey = 0
            self._ulw_info.pblend = ctypes.pointer(blend)
            self._ulw_info.dwFlags = ULW_ALPHA
            self._ulw_info.prcDirty = None
            _user32.UpdateLayeredWindowIndirect(
                ctypes.c_void_p(self._hwnd),
                ctypes.byref(self._ulw_info),
            )
        except Exception:
            pass  # Best-effort — if it fails, fall through to normal show

    def hide(self) -> None:
        """Hide the layered window. Idempotent — safe to call repeatedly."""
        self._is_window_hidden = True
        # ★ Bug #181: flush DWM to transparent BEFORE hiding.  Same
        # defense as _flush_dwm_transparent in show(), but applied
        # on hide to cover edge cases where show() is bypassed.
        self._flush_dwm_transparent()
        if self._hwnd:
            _user32.ShowWindow(self._hwnd, 0)  # SW_HIDE

    def set_click_through(self, enabled: bool) -> None:
        self.click_through = enabled
        # ★ If enabling click-through, dismiss any open context menu.
        #    Otherwise clicking the overlay passes through and can't dismiss it.
        if enabled:
            self._tk_queue.put(lambda: self._dismiss_context_menu())
        self._gl_queue.put(lambda v=enabled: self._apply_clickthrough(v))

    def set_always_on_top(self, enabled: bool) -> None:
        self.always_on_top = enabled
        # ★ Push to GL thread-safe queue (caller may be on tray/tk thread)
        self._gl_queue.put(lambda v=enabled: self._apply_topmost(v))

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.05, min(1.0, float(opacity)))

    def set_background(self, color: str, alpha: float) -> None:
        """Update background clear color at runtime (called on settings apply)."""
        r, g, b = self._hex_to_rgb(color)
        self._bg_color = (r / 255.0, g / 255.0, b / 255.0)
        self._bg_alpha = max(0.0, min(1.0, float(alpha)))

    def get_position(self) -> tuple[int, int]:
        if self._window:
            return (self._window.get_location()[0], self._window.get_location()[1])  # type: ignore
        return (0, 0)

    # ================================================================
    # Adaptive FPS / frame skipping
    # ================================================================

    def _signal_activity(self, duration: float | None = None) -> None:
        """Mark the renderer as active — boost to full target_fps.

        Called whenever anything on screen changes (new song, progress
        tick, transition start, cover spin, etc.).  The boost persists
        for ``duration`` seconds (default: _active_linger).
        """
        self._active_until = time.time() + (duration or self._active_linger)
        self._frame_skip_counter = 0
        self._frames_since_active = 0

    def _should_render(self) -> bool:
        """Return True if this frame should be rendered.

        - Active (Tier 1):  full target_fps — every frame.
        - Continuous (Tier 2): capped at _continuous_fps (60) — smooth
          enough for cover rotation, keeps GPU usage in check.
        - Idle:              _idle_fps (10) — progress bar only.

        When a fullscreen app covers the overlay (and always_on_top is OFF),
        all tiers are capped at _fullscreen_fps_cap (30) — the user can't
        see the overlay anyway, so rendering faster wastes GPU.
        """
        now = time.time()
        is_active = now < self._active_until

        # ★ Hidden / transparent window: ultra-low FPS.  When the overlay
        # is invisible (alpha≈0 or ShowWindow hidden), rendering at full
        # continuous FPS wastes GPU — the user can't see the output anyway.
        # 2fps keeps the event loop + tray menu responsive, but cuts GPU
        # usage by 95%+ vs 45fps cover spin.
        _is_hidden = getattr(self, '_is_window_hidden', False)
        _is_transparent = self._opacity < 0.02
        if _is_hidden or _is_transparent:
            self._frame_skip_counter += 1
            _ratio = max(1, round(self.target_fps / 2))  # 2fps
            if self._frame_skip_counter >= _ratio:
                self._frame_skip_counter = 0
                return True
            return False

        # ★ Fullscreen eco: apply selected mode to continuous / activity FPS.
        #   "pause":  already handled by _detect_continuous→False (spin stops)
        #   "30fps":  cap continuous at 30fps, activity at 30fps
        #   "15fps":  cap continuous at 15fps, activity at 15fps
        _fs_mode = self._check_fullscreen()
        _eff_idle = self._idle_fps
        _eff_cont = self._continuous_fps
        _eff_active = self.target_fps
        if _fs_mode in ("30fps", "15fps"):
            _cap = 30 if _fs_mode == "30fps" else 15
            _eff_cont = min(_eff_cont, _cap)
            _eff_active = min(_eff_active, _cap)

        # Continuous mode: cover spinning etc. — capped at _eff_cont
        if getattr(self, '_continuous_active', False):
            if _eff_active <= _eff_cont:
                return True  # no cap needed
            self._continuous_ratio = max(1, round(
                _eff_active / _eff_cont))
            self._continuous_counter += 1
            if self._continuous_counter >= self._continuous_ratio:
                self._continuous_counter = 0
                return True
            return False

        if is_active:
            # Full FPS — render every frame (may be capped by fullscreen eco)
            self._skip_ratio = max(1, round(_eff_active / _eff_idle))
            self._frame_skip_counter = 0
            self._frames_since_active = 0
            return True

        # Idle mode — skip _skip_ratio-1 frames out of every _skip_ratio
        self._frames_since_active += 1
        self._frame_skip_counter += 1
        if self._frame_skip_counter >= self._skip_ratio:
            self._frame_skip_counter = 0
            return True
        return False

    def _detect_change(self, data: dict) -> bool:
        """Return True if a *significant* visual change occurred — song,
        cover, play/pause, or animation.  These boost FPS to full speed
        and reset the 2s activity timer.

        Continuous conditions (cover_spinning) and progress changes are
        handled separately — they force rendering but don't reset the timer.
        """
        prev = self._last_data_snap
        if not prev:
            return True  # first frame always renders

        # Song/artist change → new song
        if data.get('song_name') != prev.get('song_name'):
            return True
        if data.get('artist_name') != prev.get('artist_name'):
            return True

        # Cover image change (compare object identity)
        if id(data.get('cover_image')) != prev.get('_cover_id'):
            return True

        # Playback state change (play/pause/stop)
        if data.get('is_paused') != prev.get('is_paused'):
            return True

        # State machine transition active (fade in/out, show/hide animation)
        if data.get('is_animating'):
            return True

        # Scene layout changed (e.g. resize, element toggle)
        if self.scene.layout_dirty:
            return True

        return False

    def _detect_progress(self, data: dict) -> bool:
        """Return True if progress crossed a 1% boundary since last render.
        Does NOT count as 'activity' — idle FPS handles this fine.
        """
        _pct = int(data.get('progress', 0) * 100)
        return _pct != self._last_progress_pct

    def _detect_continuous(self, data: dict) -> bool:
        """Return True if a continuous animation is active (cover spin).
        Forces full-FPS rendering WITHOUT resetting the activity timer.
        When the condition ends, FPS drops immediately to idle — no 2s linger.

        ★ Fullscreen eco: when occluded by a fullscreen app (always_on_top OFF):
          "pause" → return False (stop spin, idle FPS)
          "30fps" / "15fps" → return True (spin continues, capped by _should_render)
        """
        if not data.get('cover_spinning'):
            return False
        _fs = self._check_fullscreen()
        if _fs == "pause":
            return False
        return True

    def _check_fullscreen(self) -> str | None:
        """Return the active fullscreen eco mode, or None if not applicable.

        Only relevant when always_on_top is OFF (overlay can be occluded).
        Checks the foreground window — if it fills the entire primary monitor
        it's treated as fullscreen (game, video player, presentation, etc.).
        Result is cached for 1 second to keep Win32 API overhead trivial.

        Returns one of "pause", "30fps", "15fps", or None.
        """
        if self.always_on_top:
            return None
        _mode = self._fullscreen_eco_mode
        if not _mode:
            return None
        now = time.time()
        if now - self._fullscreen_check_time < 1.0:
            return self._cached_fs_mode
        self._fullscreen_check_time = now
        self._cached_fs_mode = None
        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return None
            r = ctypes.wintypes.RECT()
            _user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
            sw = _user32.GetSystemMetrics(0)   # SM_CXSCREEN
            sh = _user32.GetSystemMetrics(1)   # SM_CYSCREEN
            fw = r.right - r.left
            fh = r.bottom - r.top
            if fw >= sw and fh >= sh:
                self._cached_fs_mode = _mode
        except Exception:
            self._cached_fs_mode = None
        return self._cached_fs_mode

    # ================================================================
    # Render loop
    # ================================================================

    def _on_draw(self) -> None:
        """Render one frame to FBO, then composite via UpdateLayeredWindow."""
        if not self._running:
            return

        # ★ Guard: prevent double-render (ghost image) from re-entrant calls.
        if getattr(self, '_in_draw', False):
            return

        # ★ Adaptive FPS: check BEFORE touching any GL state.
        # When idle (no visual changes), skip most frames to reduce GPU load.
        # _should_render tracks the idle skip pattern (e.g., render 1 of every
        # 6 frames at 60→10fps).  Activity detection runs further below after
        # we have the data snapshot — if anything changed, we render regardless.
        _should_skip = not self._should_render()

        self._in_draw = True
        try:
            _t0 = time.time()
            w, h = self.width, self.height

            # ★ Frame profiler — detect intermittent stutter.
            # Measures each phase independently to isolate root cause.
            _prof = None
            try:
                from debug.frame_profiler import get_profiler
                _prof = get_profiler(self.target_fps)
            except Exception:
                pass

            # Resize FBO if window size changed
            if w != self._fbo_w or h != self._fbo_h:
                self._delete_fbo()
                self._delete_dib()
                self._init_fbo()
                self._init_dib()
                self._pixel_buf = (ctypes.c_ubyte * (w * h * 4))()
                if self._painter:
                    self._painter.screen_w = w
                    self._painter.screen_h = h
                self._signal_activity(3.0)  # resize = active for 3s

            # Data update is handled by pyglet.clock (non-blocking, separate from draw)

            # ---- Data provider ----
            data = self._data_provider()
            _t1 = time.time()

            # ---- Change detection (for adaptive FPS) ----
            # Three-tier rendering decisions:
            #   Tier 1 (activity):    song/cover/anim CHANGES → boost FPS, reset timer
            #   Tier 2 (continuous):  cover_spinning → full FPS now, no timer reset
            #   Tier 3 (progress):    progress 1% tick → render, stay at current tier
            _activity = self._detect_change(data)
            _continuous = self._detect_continuous(data) if not _activity else False
            _progress = self._detect_progress(data) if not _activity else False

            if _activity:
                # Significant visual change — boost to full target FPS + 2s linger
                self._signal_activity()
                _should_skip = False
                self._last_data_snap = {
                    'song_name': data.get('song_name'),
                    'artist_name': data.get('artist_name'),
                    '_cover_id': id(data.get('cover_image')),
                    'is_paused': data.get('is_paused'),
                    'is_animating': data.get('is_animating'),
                    'cover_spinning': data.get('cover_spinning'),
                }
                self._last_progress_pct = int(data.get('progress', 0) * 100)

            if _continuous:
                # Cover spinning etc. — let _should_render() throttle via
                # _continuous_fps cap (60fps).  Don't override _should_skip —
                # the throttle in _should_render() is authoritative.
                self._continuous_active = True
            else:
                self._continuous_active = False

            if _progress:
                # Progress tick — render this frame at whatever current FPS tier.
                # Don't boost: idle 10fps is plenty for 1% granularity updates.
                _should_skip = False
                self._last_progress_pct = int(data.get('progress', 0) * 100)

            # ★ Skip rendering if idle and nothing changed.
            # The DIB still holds the last rendered frame — the screen won't change.
            if _should_skip:
                self._in_draw = False
                return

            # ========================================================
            # FULL RENDER PATH
            # ========================================================
            glBindFramebuffer(GL_FRAMEBUFFER, self._fbo[0])
            glViewport(0, 0, w, h)

            # Pre-multiplied alpha: RGB *= bg_alpha for ULW AC_SRC_ALPHA convention
            glClearColor(self._bg_color[0] * self._bg_alpha,
                        self._bg_color[1] * self._bg_alpha,
                        self._bg_color[2] * self._bg_alpha,
                        self._bg_alpha)
            glClear(GL_COLOR_BUFFER_BIT)
            glEnable(GL_BLEND)
            glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)  # Pre-multiplied alpha

            # Update painter
            self._painter.set_data(**data)
            self._painter.screen_w = w
            self._painter.screen_h = h

            # Resolve layout if needed
            if self.scene.layout_dirty:
                self.scene.resolve_layout(w, h)

            # Render all nodes
            cover_node = self.scene.get("cover")
            if cover_node:
                self._painter._cover_x = int(cover_node.abs_x)
                self._painter._cover_y = int(cover_node.abs_y)
                self._painter._cover_size = int(cover_node.abs_width)
                self._painter._cover_border_radius = float(
                    cover_node.data.get("border_radius", 20))
            for node in self.scene.render_order():
                try:
                    self._painter.paint_node(node)
                except Exception:
                    import traceback as _tb
                    _gl_log.error(f"paint_node({node.id}) crashed:\n{_tb.format_exc()}")
            _t2 = time.time()

            # ---- PBO async readback (double-buffered) ----
            _size = w * h * 4
            _write_pbo = self._pbo0 if self._pbo_index == 0 else self._pbo1
            _read_pbo = self._pbo1 if self._pbo_index == 0 else self._pbo0

            glBindBuffer(GL_PIXEL_PACK_BUFFER, _write_pbo)
            glReadPixels(0, 0, w, h, GL_BGRA, GL_UNSIGNED_BYTE, 0)

            if not self._pbo_first:
                glBindBuffer(GL_PIXEL_PACK_BUFFER, _read_pbo)
                _mapped = glMapBuffer(GL_PIXEL_PACK_BUFFER, GL_READ_ONLY)
                if _mapped:
                    _dest = ctypes.cast(self._dib_bits, ctypes.POINTER(ctypes.c_ubyte))
                    try:
                        ctypes.memmove(
                            ctypes.addressof(_dest.contents),
                            _mapped, _size)
                    except Exception:
                        try:
                            _ptr = _mapped.value if hasattr(_mapped, 'value') else int(_mapped)  # type: ignore
                            ctypes.memmove(ctypes.addressof(_dest.contents), _ptr, _size)
                        except Exception:
                            pass
                    glUnmapBuffer(GL_PIXEL_PACK_BUFFER)
            else:
                glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
                glReadPixels(0, 0, w, h, GL_BGRA, GL_UNSIGNED_BYTE, self._pixel_buf)
                _dest = ctypes.cast(self._dib_bits, ctypes.POINTER(ctypes.c_ubyte))
                ctypes.memmove(ctypes.addressof(_dest.contents),
                               self._pixel_buf, _size)
                self._pbo_first = False

            self._pbo_index = 1 - self._pbo_index
            glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
            _t_read = time.time()

            # ---- UpdateLayeredWindowIndirect (full window) ----
            if self._hwnd:
                global_alpha = max(1, min(255, int(self._opacity * 255)))
                pt_src = POINT(0, 0)
                pt_dst = POINT(self._window.get_location()[0], self._window.get_location()[1])  # type: ignore
                win_sz = SIZE(w, h)
                blend = BLENDFUNCTION(AC_SRC_OVER, 0, global_alpha, AC_SRC_ALPHA)

                _p_dst = ctypes.pointer(pt_dst)
                _p_sz = ctypes.pointer(win_sz)
                _p_src = ctypes.pointer(pt_src)
                _p_blend = ctypes.pointer(blend)
                # prcDirty=NULL forces DWM to process the entire window.
                self._ulw_info.hdcDst = 0
                self._ulw_info.pptDst = _p_dst
                self._ulw_info.psize = _p_sz
                self._ulw_info.hdcSrc = self._mem_dc
                self._ulw_info.pptSrc = _p_src
                self._ulw_info.crKey = 0
                self._ulw_info.pblend = _p_blend
                self._ulw_info.dwFlags = ULW_ALPHA
                self._ulw_info.prcDirty = None  # NULL → full window

                # ★ Bug #181 (fix 6): Don't update DWM while the window is
                # hidden.  UpdateLayeredWindowIndirect composites to DWM's
                # cached frame even when ShowWindow(SW_HIDE) is in effect —
                # every call during the hidden period overwrites the
                # transparent flush done in hide().  Skipping only the DWM
                # update (not the GL render) keeps the cache clean while
                # ensuring the DIB and progress tracker stay fresh so
                # rendering resumes immediately on show().
                if not self._is_window_hidden:
                    _user32.UpdateLayeredWindowIndirect(
                        ctypes.c_void_p(self._hwnd),
                        ctypes.byref(self._ulw_info),
                    )

            glBindFramebuffer(GL_FRAMEBUFFER, 0)
            _t_done = time.time()
        except Exception as _draw_exc:
            # ★ GL context loss recovery: if the FBO/DIB/PBO become
            # invalid (e.g. after display sleep, driver update, or
            # remote desktop), catch the error and rebuild resources.
            # Without this, the event loop crashes and the overlay
            # freezes until the next manual restart.
            _exc_name = type(_draw_exc).__name__
            _gl_log.error(f"_on_draw crashed ({_exc_name}): {_draw_exc}")
            try:
                self._delete_fbo()
                self._delete_dib()
                self._init_fbo()
                self._init_dib()
                self._painter.init_gl()
                _gl_log.warn("GL resources rebuilt after draw crash")
            except Exception as _rebuild_exc:
                _gl_log.error(f"GL rebuild failed: {_rebuild_exc}")
        finally:
            self._in_draw = False

        # ★ UpdateLayeredWindow resets Z-order (MSDN-documented behavior).
        # Without immediate re-apply, fullscreen apps / notifications /
        # other topmost windows push the overlay behind them permanently.
        # SetWindowPos(HWND_TOPMOST, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE)
        # is ~1µs — just posts WM_WINDOWPOSCHANGING internally.
        # ★ EXCEPTION: when the context menu is open, skip TOPMOST re-apply.
        # Otherwise the overlay fights the menu Toplevel every frame and
        # the menu gets buried behind the overlay — making it unusable.
        if not getattr(self, '_ctx_menu_active', False):
            insert = HWND_TOPMOST if self.always_on_top else HWND_NOTOPMOST
            _user32.SetWindowPos(ctypes.c_void_p(self._hwnd),
                                 ctypes.c_void_p(insert), 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            self._topmost_dirty = False

        # ---- Frame profiler recording ----
        _t_total = (_t_done - _t0) * 1000.0
        if _prof is not None:
            _prof.record_frame(
                total_ms=_t_total,
                data_ms=(_t1 - _t0) * 1000.0,
                setup_ms=(_t2 - _t1) * 1000.0,
                read_ms=(_t_read - _t2) * 1000.0,
                ulw_ms=(_t_done - _t_read) * 1000.0,
            )

        # ---- Publish stats ----
        self._prof_data += (_t1 - _t0)
        self._prof_setup += (_t2 - _t1)
        self._prof_render += (_t_done - _t2)
        self._track_fps(_t_total / 1000.0)

        # ★ Heartbeat diagnostic — confirm render loop is alive
        self._frame_count = getattr(self, '_frame_count', 0) + 1
        if self._frame_count <= 3 or self._frame_count % 600 == 0:
            _gl_log.info(f"frame={self._frame_count} dt={_t_total:.1f}ms "
                        f"data={(_t1-_t0)*1000:.1f}ms setup={(_t2-_t1)*1000:.1f}ms "
                        f"read={(_t_read-_t2)*1000:.1f}ms ulw={(_t_done-_t_read)*1000:.1f}ms"
                        f" | cont={_continuous} act={_activity}")
        # ★ Periodic taskbar-style guard: every ~5s verify WS_EX_TOOLWINDOW
        # is set and WS_EX_APPWINDOW is stripped. UpdateLayeredWindow can
        # cause Windows to re-evaluate taskbar visibility.
        if self._frame_count % 300 == 1 and self._hwnd:
            try:
                ex = _user32.GetWindowLongW(ctypes.c_void_p(self._hwnd), GWL_EXSTYLE)
                if (ex & 0x00040000) or not (ex & WS_EX_TOOLWINDOW):
                    _gl_log.warn("taskbar styles corrupted — re-applying _make_layered")
                    _make_layered(self._hwnd)
            except Exception:
                pass

    def _track_fps(self, dt: float) -> None:
        """Log FPS and per-phase profiling every 2 seconds (wall-clock)."""
        now = time.time()
        self._fps_samples.append(dt)
        # Track wall-clock window
        _fps_wall_start = getattr(self, '_fps_wall_start', 0.0)
        if _fps_wall_start == 0.0:
            self._fps_wall_start = now
        if now - self._fps_wall_start >= 2.0:
            count = len(self._fps_samples)
            if count > 0:
                total = sum(self._fps_samples)
                n = max(1, count)
                avg_ms = (total / count) * 1000
                wall_elapsed = now - self._fps_wall_start
                avg_fps = count / wall_elapsed if wall_elapsed > 0 else 0
                dat_ms = (self._prof_data / n) * 1000
                setup_ms = (self._prof_setup / n) * 1000
                render_ms = (self._prof_render / n) * 1000
                _gl_log.info(f"FPS {avg_fps:.1f} | frame={avg_ms:.1f}ms | "
                            f"data={dat_ms:.1f}ms setup={setup_ms:.1f}ms "
                            f"r+ulw={render_ms:.1f}ms | {count}f in {wall_elapsed:.1f}s")
                if avg_fps > 30 and count > 50:
                    # Stop detailed profiling once stable — just log summary
                    self._prof_data = 0.0
                    self._prof_setup = 0.0
                    self._prof_render = 0.0
            self._fps_samples.clear()
            self._fps_wall_start = now
            self._prof_data = 0.0
            self._prof_setup = 0.0
            self._prof_render = 0.0

    # ================================================================
    # Mouse events
    # ================================================================

    def _on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.click_through:
            return
        # ★ If context menu (Toplevel) is open, any click dismisses it
        if getattr(self, '_ctx_menu_active', False):
            self._tk_queue.put(lambda: self._dismiss_context_menu())
            return
        if button == mouse.LEFT:
            self._drag_active = True
            self._drag_x = x
            self._drag_y = y
        elif button == mouse.RIGHT:
            # ★ Show context menu on the tk thread (only one at a time)
            win_x, win_y = self._window.get_location()[0], self._window.get_location()[1]  # type: ignore
            screen_x, screen_y = int(win_x + x), int(win_y + self.height - y)
            self._tk_queue.put(lambda: self._show_context_menu(screen_x, screen_y))

    def _dismiss_context_menu(self) -> None:
        """Close the active context menu if any."""
        menu = getattr(self, '_ctx_menu', None)
        if menu is not None:
            try:
                menu.destroy()
            except Exception:
                pass
            self._ctx_menu = None
            self._ctx_menu_active = False
        # ★ Defensive: re-verify taskbar-hiding styles. UpdateLayeredWindow
        # can cause Windows to re-evaluate taskbar visibility; if WS_EX_TOOLWINDOW
        # was lost or WS_EX_APPWINDOW crept back, the taskbar button reappears.
        if self._hwnd:
            try:
                ex = _user32.GetWindowLongW(ctypes.c_void_p(self._hwnd), GWL_EXSTYLE)
                if (ex & 0x00040000) or not (ex & WS_EX_TOOLWINDOW):
                    _make_layered(self._hwnd)
            except Exception:
                pass

    def _show_context_menu(self, screen_x: int, screen_y: int) -> None:
        """Build and show the right-click context menu.

        Uses a borderless Toplevel instead of tkinter Menu — tk_popup()
        creates a Windows modal loop (TrackPopupMenu) that captures ALL
        mouse events, making click-away impossible with a pyglet window.
        A Toplevel has no modal loop — click-away works naturally.
        """
        self._dismiss_context_menu()

        # ★ Defensive: ensure taskbar-hiding styles are intact before showing menu.
        # Context menu operations (focus changes, Toplevel creation) can trigger
        # Windows to re-evaluate taskbar visibility of the overlay.
        if self._hwnd:
            try:
                ex = _user32.GetWindowLongW(ctypes.c_void_p(self._hwnd), GWL_EXSTYLE)
                if (ex & 0x00040000) or not (ex & WS_EX_TOOLWINDOW):
                    _make_layered(self._hwnd)
            except Exception:
                pass

        import tkinter as tk
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#2a2a3e")
        # Position: initial guess — final geometry applied after items added
        win.geometry(f"+{screen_x}+{screen_y}")
        win.focus_force()

        items = []
        items.append(("  打开设置界面", self._menu_settings, False))
        items.append(("---", None, False))
        items.append(("  显示窗口", self._menu_show, False))
        items.append(("  隐藏窗口", self._menu_hide, False))
        items.append(("---", None, False))
        _LABELS = {"cover": "封面", "title": "歌名", "artist": "歌手", "lyrics": "歌词"}
        for eid, lb in _LABELS.items():
            node = self.scene.get(eid)
            visible = node.visible if node else True
            items.append((f"  {'✓' if visible else '☐'} {lb}",
                          lambda eid=eid: self._menu_toggle_element(eid), False))
        items.append(("---", None, False))
        items.append(("  歌曲信息", lambda: self._menu_song_info(screen_x, screen_y), False))
        items.append((f"  {'✓' if self.always_on_top else '☐'} 窗口置顶",
                      self._menu_toggle_topmost, False))
        items.append((f"  {'✓' if self.click_through else '☐'} 鼠标穿透",
                      self._menu_toggle_clickthrough, False))
        items.append(("---", None, False))
        items.append(("  退出", self._menu_exit, False))

        def _on_click(fn):
            def _handler(e=None):
                self._ctx_menu_active = False
                self._ctx_menu = None
                win.destroy()
                fn()
            return _handler

        for label, fn, _ in items:
            if label == "---":
                f = tk.Frame(win, height=1, bg="#555570")
                f.pack(fill="x", padx=4, pady=2)
            else:
                btn = tk.Label(win, text=label, bg="#2a2a3e", fg="white",
                               font=("Microsoft YaHei", 10),
                               padx=16, pady=3, anchor="w")
                btn.pack(fill="x")
                btn.bind("<Enter>", lambda e, b=btn: b.configure(bg="#4444aa"))
                btn.bind("<Leave>", lambda e, b=btn: b.configure(bg="#2a2a3e"))
                btn.bind("<Button-1>", _on_click(fn))

        # Force geometry calculation, then check bounds
        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        if screen_x + w > sw:
            screen_x = sw - w - 5
        if screen_y + h > sh:
            screen_y = sh - h - 5
        win.geometry(f"+{screen_x}+{screen_y}")

        # ★ Force menu to the very top of the topmost Z-order.
        # Tk's lift() + -topmost is not enough when the overlay itself
        # is HWND_TOPMOST — the layered overlay window stays above the
        # menu unless we explicitly re-stack via Win32.
        win.update_idletasks()
        try:
            _menu_hwnd = int(win.frame(), 0)
        except Exception:
            _menu_hwnd = 0
        if _menu_hwnd:
            _user32.SetWindowPos(ctypes.c_void_p(_menu_hwnd),
                                 ctypes.c_void_p(HWND_TOPMOST),
                                 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        win.lift()

        # Dismiss on focus loss or Escape
        def _on_focus_out(e=None):
            self._ctx_menu_active = False
            self._ctx_menu = None
            win.destroy()
        win.bind("<FocusOut>", _on_focus_out)
        win.bind("<Escape>", lambda e: win.destroy())

        self._ctx_menu = win
        self._ctx_menu_active = True

    # ---- Context menu actions ----

    def _menu_settings(self) -> None:
        if self._on_settings:
            self._on_settings()

    def _menu_show(self) -> None:
        # ★ Delegate to window callback so state machine stays consistent
        if self._on_show:
            self._on_show()

    def _menu_hide(self) -> None:
        # ★ Delegate to window callback so state machine stays consistent
        if self._on_hide:
            self._on_hide()

    def _menu_toggle_element(self, eid: str) -> None:
        node = self.scene.get(eid)
        if node:
            node.visible = not node.visible
            self.scene.mark_dirty()

    def _menu_song_info(self, screen_x: int, screen_y: int) -> None:
        """Show song info popup (runs on tk thread)."""
        import tkinter as tk
        if not self._reader:
            return
        info_win = tk.Toplevel(self.root)
        info_win.title("")
        info_win.geometry("400x150")
        info_win.configure(bg="#2a2a3e")
        info_win.attributes("-topmost", self.always_on_top)
        info_win.overrideredirect(True)

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

        info_win.geometry(f"+{screen_x-200}+{screen_y-75}")
        info_win.lift()
        info_win.focus_force()

    def _menu_toggle_topmost(self) -> None:
        new_v = not self.always_on_top
        self.always_on_top = new_v
        self.config.setdefault("window", {})["always_on_top"] = new_v
        # ★ Push to GL thread-safe queue (we're on TK thread, Win32 needs pyglet thread)
        self._gl_queue.put(lambda v=new_v: self._apply_topmost(v))
        self._defer_save_config()
        if self._on_tray_refresh:
            self._gl_queue.put(lambda: self._on_tray_refresh())

    def _apply_topmost(self, enabled: bool) -> None:
        """Apply topmost state (called on pyglet main thread via _gl_queue)."""
        _gl_log.info(f"apply_topmost hwnd={self._hwnd} enabled={enabled}")
        if self._hwnd:
            _set_topmost(self._hwnd, enabled)
            # ★ Flag for re-application after next UpdateLayeredWindow
            self._topmost_dirty = True

    def _menu_toggle_clickthrough(self) -> None:
        new_v = not self.click_through
        self.click_through = new_v
        self.config.setdefault("window", {})["click_through"] = new_v
        self._gl_queue.put(lambda v=new_v: self._apply_clickthrough(v))
        self._defer_save_config()
        # ★ Defer tray refresh to GL thread — pystray.update_menu()
        #    is not thread-safe when called from the TK thread.
        if self._on_tray_refresh:
            self._gl_queue.put(lambda: self._on_tray_refresh())

    def _apply_clickthrough(self, enabled: bool) -> None:
        """Apply click-through state (called on pyglet main thread via _gl_queue)."""
        _gl_log.info(f"apply_clickthrough hwnd={self._hwnd} enabled={enabled}")
        if self._hwnd:
            _set_click_through(self._hwnd, enabled)

    def _menu_exit(self) -> None:
        if self._on_exit:
            self._on_exit()

    def _on_mouse_drag(self, x, y, dx, dy, buttons, modifiers) -> None:
        if not self._drag_active or self.click_through:
            return
        win_x = self._window.get_location()[0]  # type: ignore
        win_y = self._window.get_location()[1]  # type: ignore
        new_x = int(win_x + (x - self._drag_x))
        new_y = int(win_y - (y - self._drag_y))
        self._window.set_location(new_x, new_y)

    def _on_mouse_release(self, x, y, button, modifiers) -> None:
        if not self._drag_active:
            return
        self._drag_active = False
        wnx = int(self._window.get_location()[0])  # type: ignore
        wny = int(self._window.get_location()[1])  # type: ignore
        self.config.setdefault("window", {})["position"] = {"x": wnx, "y": wny}
        # ★ Save immediately after drag — deferred save (1s cooldown) could
        #    leave stale position on disk when settings are opened quickly.
        try:
            from config import save_config
            save_config(self.config)
        except Exception as e:
            _gl_log.warn(f"drag-position config save failed: {e}")

    def _on_mouse_scroll(self, x, y, scroll_x, scroll_y) -> None:
        if self.click_through:
            return
        delta = -scroll_y * 0.05
        cur = self.config.get("window", {}).get("opacity", 0.85)
        new = max(0.05, min(1.0, cur + delta))
        self.config.setdefault("window", {})["opacity"] = new
        self._opacity = new
        self._defer_save_config()

    def _on_key_press(self, symbol, modifiers) -> None:
        if symbol == key.ESCAPE:
            if self._on_exit:
                self._on_exit()

    # ================================================================
    # Helpers
    # ================================================================

    def _defer_save_config(self, cooldown: float = 1.0) -> None:
        """Schedule a deferred config save with cooldown.

        Prevents per-frame disk I/O from rapid events (mouse scroll,
        drag release, menu toggles). The actual save happens in
        _scheduled_update after the cooldown expires.
        """
        self._save_pending = True
        self._save_deadline = time.time() + cooldown

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 3:
            hex_color = "".join(c * 2 for c in hex_color)
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
