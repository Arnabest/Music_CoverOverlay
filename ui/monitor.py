"""Comprehensive real-time monitoring window for QQMusicOverlayNG.

Usage:
    python ui/monitor.py                    # Standalone
    MonitorWindow(parent, log_dir, ...)     # Embedded (from tray/settings)

Features:
  - FPS Monitor: real-time FPS, frame timing, phase breakdown, trend sparkline
  - SMTC Monitor: playback status, position/duration, player, poll count
  - Log Viewer:   dropdown to switch between ALL log types, real-time tail
  - System:       heartbeat status, process info, log file sizes, uptime
  - Tray Diag:    tray click log, nuclear option status
"""
import os, sys, time, re, queue, threading, datetime
import tkinter as tk
from tkinter import ttk, scrolledtext
from collections import deque

TEMP_DIR = os.environ.get("TEMP", ".")

# ── Log file registry ──────────────────────────────────────────
# (filename, logger_name, display_label)
LOG_SOURCES: list[tuple[str, str, str]] = [
    ("qqoverlay_render.log",     "render",      "Render (FPS/GPU)"),
    ("qqoverlay_smtc.log",       "smtc",        "SMTC 引擎"),
    ("qqoverlay_reader.log",     "reader",      "Reader (歌曲/封面/歌词)"),
    ("qqoverlay_window.log",     "window",      "Window (状态机/托盘队列)"),
    ("qqoverlay_settings.log",   "settings",    "Settings (配置变更)"),
    ("qqoverlay_system.log",     "system",      "System (启动/关闭/崩溃)"),
    ("qqoverlay_errors.log",     "errors",      "Errors (跨模块错误)"),
    ("qqoverlay_compositor.log", "compositor",  "Compositor (合成)"),
    ("qqoverlay_engine.log",     "engine",      "Engine (PIL引擎)"),
    ("qqoverlay_timeline.log",   "timeline",    "Timeline (动画)"),
    ("qqoverlay_state_machine.log", "state_machine", "StateMachine"),
    ("qqoverlay_web_api.log",    "web_api",     "Web API (歌词/封面请求)"),
    ("qqoverlay_cover_prefs.log","cover_prefs", "Cover Prefs (封面偏好)"),
    ("qqoverlay_text.log",       "text",        "Text (文字渲染)"),
    ("qqoverlay_versioning.log", "versioning",  "Versioning (版本管理)"),
    ("qqoverlay_window_detect.log","window_detect","Window Detect (窗口检测)"),
    ("qqoverlay_smtc_features.log","smtc_features","SMTC Features"),
]

HEARTBEAT_FILE = os.path.join(TEMP_DIR, "qqoverlay_heartbeat.txt")
TRAY_DIAG_FILE = os.path.join(TEMP_DIR, "qqoverlay_tray_diag.log")

REFRESH_MS = 500  # UI refresh interval


# ═══════════════════════════════════════════════════════════════
# Log Tailer
# ═══════════════════════════════════════════════════════════════

class LogTailer:
    """Thread-safe log tailer that reads new lines from a file."""

    def __init__(self, filepath: str):
        self._path = filepath
        self._queue: queue.Queue[str] = queue.Queue()
        self._running = True
        self._pos = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while self._running:
            try:
                if os.path.exists(self._path):
                    with open(self._path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self._pos)
                        for line in f:
                            self._queue.put(line.rstrip("\n\r"))
                        self._pos = f.tell()
            except Exception:
                pass
            time.sleep(0.25)

    def stop(self) -> None:
        self._running = False

    def get_lines(self, max_lines: int = 200) -> list[str]:
        """Drain all pending lines from the queue."""
        lines: list[str] = []
        try:
            while True:
                lines.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return lines


# ═══════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════

# FPS line: [HH:MM:SS.mmm] [render] [INFO] FPS 60.0 | frame=16.7ms | data=2.1ms setup=3.4ms r+ulw=5.2ms | 60 frames in 1.0s
FPS_RE = re.compile(
    r'FPS\s+([\d.]+)\s*\|\s*frame=([\d.]+)ms\s*\|\s*'
    r'data=([\d.]+)ms\s+setup=([\d.]+)ms\s+r\+ulw=([\d.]+)ms')


class FPSData:
    """Tracks FPS history and phase timing."""

    def __init__(self, max_samples: int = 300):
        self.fps = deque(maxlen=max_samples)
        self.frame_ms = deque(maxlen=max_samples)
        self.data_ms = deque(maxlen=max_samples)
        self.setup_ms = deque(maxlen=max_samples)
        self.render_ms = deque(maxlen=max_samples)
        self.last_ts = 0.0

    def feed(self, line: str) -> bool:
        m = FPS_RE.search(line)
        if not m:
            return False
        self.fps.append(float(m.group(1)))
        self.frame_ms.append(float(m.group(2)))
        self.data_ms.append(float(m.group(3)))
        self.setup_ms.append(float(m.group(4)))
        self.render_ms.append(float(m.group(5)))
        self.last_ts = time.time()
        return True

    @property
    def current(self) -> float | None:
        return self.fps[-1] if self.fps else None

    @property
    def stats(self) -> dict:
        if not self.fps:
            return {"current": "--", "min": "--", "max": "--",
                    "avg": "--", "1pct": "--", "frame_ms": "--",
                    "data_ms": "--", "setup_ms": "--",
                    "render_ms": "--", "samples": "0"}
        recent = list(self.fps)[-60:]  # last 60 samples (~1 min)
        return {
            "current":  f"{self.fps[-1]:.1f}",
            "min":      f"{min(recent):.1f}",
            "max":      f"{max(recent):.1f}",
            "avg":      f"{sum(recent)/len(recent):.1f}",
            "1pct":     f"{sorted(recent)[max(0, len(recent)//100)]:.1f}" if len(recent) >= 100 else "--",
            "frame_ms": f"{self.frame_ms[-1]:.1f}",
            "data_ms":  f"{self.data_ms[-1]:.1f}",
            "setup_ms": f"{self.setup_ms[-1]:.1f}",
            "render_ms":f"{self.render_ms[-1]:.1f}",
            "samples":  str(len(self.fps)),
        }

    def sparkline(self, width: int = 60) -> str:
        if len(self.fps) < 2:
            return ""
        recent = list(self.fps)[-width:]
        mn, mx = min(recent), max(recent)
        if mx == mn:
            mx = mn + 1
        chars = " ▁▂▃▄▅▆▇█"
        return "".join(chars[min(8, int((v - mn) / (mx - mn) * 8))]
                       for v in recent)

    def phase_pcts(self) -> tuple[float, float, float]:
        """Return (data%, setup%, render%) of total frame time."""
        if not self.frame_ms:
            return (0, 0, 0)
        total = self.frame_ms[-1]
        if total <= 0:
            return (0, 0, 0)
        return (
            self.data_ms[-1] / total * 100,
            self.setup_ms[-1] / total * 100,
            self.render_ms[-1] / total * 100,
        )


# SMTC log patterns
SMTC_STATUS_RE = re.compile(r'status\s*[=:]\s*(\w+)', re.IGNORECASE)
SMTC_PLAYER_RE = re.compile(r'player[=:]\s*(\w+)', re.IGNORECASE)
SMTC_POS_RE = re.compile(r'pos[=:]\s*([\d.]+)', re.IGNORECASE)
SMTC_DUR_RE = re.compile(r'dur(?:ation)?[=:]\s*([\d.]+)', re.IGNORECASE)
SMTC_TITLE_RE = re.compile(r"title[=:]\s*'([^']*)'", re.IGNORECASE)
SMTC_ARTIST_RE = re.compile(r"artist[=:]\s*'([^']*)'", re.IGNORECASE)
SMTC_POLL_RE = re.compile(r"poll\s*#?(\d+)", re.IGNORECASE)
SMTC_PULSE_RE = re.compile(r"pulse\s*#?(\d+)", re.IGNORECASE)
SMTC_SESSION_RE = re.compile(r"session[:=]", re.IGNORECASE)


class SMTCData:
    """Parsed SMTC state from log lines."""

    def __init__(self):
        self.status = "—"
        self.player = "—"
        self.position = "—"
        self.duration = "—"
        self.title = "—"
        self.artist = "—"
        self.poll_count = "—"
        self.last_session_change = "—"
        self._lines_buf: deque[str] = deque(maxlen=200)

    def feed(self, line: str) -> None:
        self._lines_buf.append(line)
        for regex, attr, fmt in [
            (SMTC_STATUS_RE,   "status",    str),
            (SMTC_PLAYER_RE,   "player",    str),
            (SMTC_POS_RE,      "position",  lambda v: f"{float(v):.1f}s"),
            (SMTC_DUR_RE,      "duration",  lambda v: f"{float(v):.1f}s"),
            (SMTC_TITLE_RE,    "title",     str),
            (SMTC_ARTIST_RE,   "artist",    str),
            (SMTC_POLL_RE,     "poll_count",str),
        ]:
            m = regex.search(line)
            if m:
                try:
                    setattr(self, attr, fmt(m.group(1)))
                except (ValueError, IndexError):
                    pass
        if SMTC_SESSION_RE.search(line):
            ts_match = re.match(r'\[([\d:.]+)]', line)
            if ts_match:
                self.last_session_change = ts_match.group(1)


# Tray diag patterns
TRAY_CLICK_RE = re.compile(
    r'TRAY CLICK:\s*(\S+)\s*\(action_handler=(\S+),\s*gl_queue=(\S+)\)')
TRAY_CRASH_RE = re.compile(r'ACTION HANDLER CRASHED:\s*(.*)')


class TrayDiagData:
    """Parsed tray diagnostic state."""

    def __init__(self):
        self.clicks: deque[str] = deque(maxlen=100)
        self.crashes: deque[str] = deque(maxlen=20)
        self.action_handler_status = "未知"
        self.gl_queue_status = "未知"

    def feed(self, line: str) -> None:
        m = TRAY_CLICK_RE.search(line)
        if m:
            cmd, ah, gl = m.group(1), m.group(2), m.group(3)
            self.action_handler_status = "✓ 已连接" if ah == "SET" else "✗ 未连接"
            self.gl_queue_status = "✓ 已连接" if gl == "SET" else "✗ 未连接"
            self.clicks.append(
                f"[{datetime.datetime.now():%H:%M:%S}] {cmd} "
                f"(handler={ah}, gl_queue={gl})")
        m = TRAY_CRASH_RE.search(line)
        if m:
            self.crashes.append(
                f"[{datetime.datetime.now():%H:%M:%S}] {m.group(1)[:120]}")


# ═══════════════════════════════════════════════════════════════
# FPS Canvas (sparkline + phase bars)
# ═══════════════════════════════════════════════════════════════

class FPSCanvas(tk.Canvas):
    """Draws a real-time FPS trend line + phase breakdown bar."""

    def __init__(self, parent, **kw):
        super().__init__(parent, height=100, bg="#16162a",
                         highlightthickness=0, **kw)
        self._fps_history: deque[float] = deque(maxlen=120)

    def update(self, fps_current: float | None,
               phase_data: tuple[float, float, float],
               stats: dict) -> None:
        if fps_current is not None:
            self._fps_history.append(fps_current)
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 50 or h < 50:
            return

        # ── FPS trend line ──
        h_fps = h - 26  # reserve 26px at bottom for phase bar
        self._draw_trend(w, h_fps)

        # ── Target line at 60 ──
        if self._fps_history:
            all_fps = list(self._fps_history)
            mn, mx = min(all_fps), max(all_fps)
            if mx == mn:
                mx = mn + 1
            margin = (mx - mn) * 0.1
            mn -= margin
            mx += margin
            target_y = h_fps - ((60 - mn) / (mx - mn)) * h_fps
            if 0 <= target_y <= h_fps:
                self.create_line(0, target_y, w, target_y,
                                 fill="#444466", dash=(4, 4), width=1)
                self.create_text(28, target_y - 7, text="60 fps",
                                 fill="#666688", font=("Consolas", 7), anchor="n")

        # ── Stats overlay (top-left) ──
        stats_text = (f"当前:{stats.get('current','--')} "
                      f"最低:{stats.get('min','--')} "
                      f"最高:{stats.get('max','--')} "
                      f"平均:{stats.get('avg','--')}")
        self.create_text(6, 4, text=stats_text, anchor="nw",
                         fill="#aaaacc", font=("Consolas", 8))

        # ── Phase bar at bottom ──
        bar_y = h - 20
        bar_h = 14
        dpct, spct, rpct = phase_data
        self.create_rectangle(2, bar_y, w - 2, bar_y + bar_h,
                              fill="#2a2a3e", outline="")
        x = 2
        for pct, color, label in [
            (dpct, "#ff9944", "数据"),
            (spct, "#44aaff", "布局"),
            (rpct, "#44ff88", "渲染"),
        ]:
            bw = max(0, (w - 4) * pct / 100)
            if bw > 0:
                self.create_rectangle(x, bar_y, x + bw, bar_y + bar_h,
                                      fill=color, outline="")
                if bw > 32:
                    self.create_text(x + bw/2, bar_y + bar_h/2,
                                     text=f"{label} {pct:.0f}%",
                                     fill="#000", font=("Consolas", 7, "bold"))
                x += bw

    def _draw_trend(self, w: int, h: int) -> None:
        if len(self._fps_history) < 2:
            return
        pts = list(self._fps_history)
        mn, mx = min(pts), max(pts)
        if mx == mn:
            mx = mn + 1
        margin = (mx - mn) * 0.1
        mn -= margin
        mx += margin
        rng = mx - mn
        coords = []
        for i, v in enumerate(pts):
            x = 2 + i / max(1, len(pts) - 1) * (w - 4)
            y = h - ((v - mn) / rng) * h
            coords.extend([x, y])
        if len(coords) >= 4:
            self.create_line(coords, fill="#80ff80", width=1.5, smooth=True)


# ═══════════════════════════════════════════════════════════════
# Main Monitor Window
# ═══════════════════════════════════════════════════════════════

class MonitorWindow:
    """Comprehensive monitoring window with switchable log viewer,
    FPS monitor, SMTC monitor, system info, and tray diagnostics."""

    def __init__(self, parent=None, log_dir: str | None = None):
        self._parent = parent
        self._log_dir = log_dir or TEMP_DIR

        # Master: use parent's root if available, else standalone Tk
        master = None
        if parent and hasattr(parent, 'root'):
            master = parent.root
        self._is_standalone = master is None
        if self._is_standalone:
            self._root = tk.Tk()
        else:
            self.window = tk.Toplevel(master)
            self._root = self.window

        self._root.title("QQMusicOverlay NG — 实时监控")
        self._root.geometry("900x680")
        self._root.minsize(700, 500)
        self._root.configure(bg="#1e1e2e")

        # Icon
        try:
            icon = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "assets", "app_icon.ico")
            if os.path.exists(icon):
                self._root.iconbitmap(icon)
        except Exception:
            pass

        # Dark theme
        self._bg = "#1e1e2e"
        self._fg = "#cccccc"
        self._accent = "#4444aa"

        # Data models
        self._fps_data = FPSData()
        self._smtc_data = SMTCData()
        self._tray_data = TrayDiagData()
        self._log_buffers: dict[str, deque[str]] = {}

        # Tailers (lazy, started per selected log)
        self._tailers: dict[str, LogTailer] = {}

        # Per-log-type text widgets
        self._log_texts: dict[str, scrolledtext.ScrolledText] = {}

        self._build_ui()
        self._start_core_tailers()
        self._poll()

    # ── UI Construction ────────────────────────────────────

    def _build_ui(self) -> None:
        # Notebook
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=self._bg, borderwidth=0)
        style.configure("TNotebook.Tab", background="#2a2a3e", foreground="#ccc",
                        padding=[14, 5], font=("Microsoft YaHei", 9))
        style.map("TNotebook.Tab", background=[("selected", self._accent)],
                  foreground=[("selected", "#fff")])

        nb = ttk.Notebook(self._root)
        nb.pack(fill="both", expand=True, padx=4, pady=(4, 0))

        # Tab 1: FPS
        self._tab_fps = ttk.Frame(nb)
        nb.add(self._tab_fps, text=" FPS 监控 ")
        self._build_fps_tab()

        # Tab 2: SMTC
        self._tab_smtc = ttk.Frame(nb)
        nb.add(self._tab_smtc, text=" SMTC 状态 ")
        self._build_smtc_tab()

        # Tab 3: Log Viewer
        self._tab_logs = ttk.Frame(nb)
        nb.add(self._tab_logs, text=" 日志查看 ")
        self._build_log_tab()

        # Tab 4: System
        self._tab_sys = ttk.Frame(nb)
        nb.add(self._tab_sys, text=" 系统状态 ")
        self._build_system_tab()

        # Tab 5: Tray Diag
        self._tab_tray = ttk.Frame(nb)
        nb.add(self._tab_tray, text=" 托盘诊断 ")
        self._build_tray_tab()

        # Status bar
        self._status_bar = tk.Label(
            self._root, text="就绪", anchor="w",
            bg="#2a2a3e", fg="#888",
            font=("Consolas", 9))
        self._status_bar.pack(side="bottom", fill="x")

    # ── FPS Tab ────────────────────────────────────────────

    def _build_fps_tab(self) -> None:
        f = tk.Frame(self._tab_fps, bg=self._bg)
        f.pack(fill="both", expand=True, padx=8, pady=8)

        # Large FPS display
        self._fps_big = tk.Label(f, text="-- fps", font=("Consolas", 42, "bold"),
                                 bg=self._bg, fg="#80ff80")
        self._fps_big.pack(pady=(4, 0))

        # Stats grid (2 rows x 5 cols)
        grid = tk.Frame(f, bg=self._bg)
        grid.pack(fill="x", pady=6)
        self._fps_stats: dict[str, tk.Label] = {}
        stat_keys = [
            ("current", "当前FPS"), ("min", "最低"), ("max", "最高"),
            ("avg", "平均"), ("1pct", "1%低"), ("frame_ms", "帧耗时(ms)"),
            ("data_ms", "数据(ms)"), ("setup_ms", "布局(ms)"),
            ("render_ms", "渲染(ms)"), ("samples", "样本数"),
        ]
        for i, (key, label) in enumerate(stat_keys):
            col = i % 5
            row = i // 5
            tk.Label(grid, text=label, bg=self._bg, fg="#888",
                     font=("Microsoft YaHei", 8)).grid(
                row=row, column=col*2, sticky="e", padx=(8, 2), pady=1)
            lbl = tk.Label(grid, text="--", bg=self._bg, fg="#fff",
                           font=("Consolas", 10, "bold"))
            lbl.grid(row=row, column=col*2+1, sticky="w", padx=(0, 12), pady=1)
            self._fps_stats[key] = lbl

        # FPS trend canvas
        self._fps_canvas = FPSCanvas(f)
        self._fps_canvas.pack(fill="both", expand=True, pady=4)

        # Staleness warning
        self._fps_stale = tk.Label(f, text="", bg=self._bg, fg="#ff8080",
                                    font=("Microsoft YaHei", 8))
        self._fps_stale.pack()

    # ── SMTC Tab ───────────────────────────────────────────

    def _build_smtc_tab(self) -> None:
        f = tk.Frame(self._tab_smtc, bg=self._bg)
        f.pack(fill="both", expand=True, padx=8, pady=8)

        # Structured display
        info_frame = tk.Frame(f, bg=self._bg)
        info_frame.pack(fill="x", pady=(0, 8))
        self._smtc_labels: dict[str, tk.Label] = {}
        smtc_fields = [
            ("状态 (status)",      "status"),
            ("播放器 (player)",    "player"),
            ("位置 (position)",    "position"),
            ("时长 (duration)",    "duration"),
            ("标题 (title)",       "title"),
            ("艺术家 (artist)",    "artist"),
            ("轮询计数 (polls)",   "poll_count"),
            ("上次会话变更",       "last_session_change"),
        ]
        for i, (label, key) in enumerate(smtc_fields):
            row = i // 2
            col = i % 2
            fld = tk.Frame(info_frame, bg=self._bg)
            fld.grid(row=row, column=col, sticky="ew", padx=8, pady=3)
            info_frame.columnconfigure(col, weight=1)
            tk.Label(fld, text=label, bg=self._bg, fg="#888",
                     font=("Microsoft YaHei", 9), anchor="w").pack(anchor="w")
            val = tk.Label(fld, text="—", bg=self._bg, fg="#fff",
                           font=("Consolas", 13, "bold"), anchor="w")
            val.pack(anchor="w")
            self._smtc_labels[key] = val

        # Raw SMTC log
        tk.Label(f, text="SMTC 日志 (最近50行)", bg=self._bg, fg="#888",
                 font=("Microsoft YaHei", 9), anchor="w").pack(anchor="w", pady=(8, 2))
        self._text_smtc_raw = scrolledtext.ScrolledText(
            f, bg="#16162a", fg="#ccc", insertbackground="white",
            font=("Consolas", 9), wrap="word", height=16)
        self._text_smtc_raw.pack(fill="both", expand=True)

    # ── Log Viewer Tab ─────────────────────────────────────

    def _build_log_tab(self) -> None:
        f = tk.Frame(self._tab_logs, bg=self._bg)
        f.pack(fill="both", expand=True, padx=8, pady=8)

        # Toolbar: dropdown + buttons
        toolbar = tk.Frame(f, bg=self._bg)
        toolbar.pack(fill="x", pady=(0, 4))

        tk.Label(toolbar, text="日志类型:", bg=self._bg, fg="#888",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 6))

        self._log_var = tk.StringVar(value="render")
        self._log_combo = ttk.Combobox(
            toolbar, textvariable=self._log_var,
            values=[name for _, name, _ in LOG_SOURCES],
            state="readonly", width=28)
        self._log_combo.pack(side="left", padx=4)
        self._log_combo.bind("<<ComboboxSelected>>", self._on_log_switch)

        # Build display name → internal name map
        self._log_name_map: dict[str, str] = {
            name: name for _, name, _ in LOG_SOURCES
        }

        tk.Button(toolbar, text="清除", command=self._clear_current_log,
                  bg="#555577", fg="white",
                  font=("Microsoft YaHei", 8)).pack(side="right", padx=4)
        tk.Button(toolbar, text="刷新", command=self._refresh_current_log,
                  bg="#555577", fg="white",
                  font=("Microsoft YaHei", 8)).pack(side="right", padx=4)

        # Log count
        self._log_count_label = tk.Label(toolbar, text="", bg=self._bg, fg="#888",
                                         font=("Consolas", 9))
        self._log_count_label.pack(side="right", padx=12)

        # Log text area (shared, rebuilt on switch)
        self._log_container = tk.Frame(f, bg=self._bg)
        self._log_container.pack(fill="both", expand=True)
        self._log_text = scrolledtext.ScrolledText(
            self._log_container, bg="#16162a", fg="#ccc",
            insertbackground="white", font=("Consolas", 9), wrap="word")
        self._log_text.pack(fill="both", expand=True)
        # Color tags
        self._log_text.tag_config("error", foreground="#ff8080")
        self._log_text.tag_config("warn", foreground="#ffff80")
        self._log_text.tag_config("info", foreground="#80c0ff")
        self._log_text.tag_config("debug", foreground="#888")
        self._log_text.tag_config("fps_line", foreground="#80ff80")

        # Init with render log
        self._current_log_name = "render"
        self._on_log_switch()

    # ── System Tab ─────────────────────────────────────────

    def _build_system_tab(self) -> None:
        f = tk.Frame(self._tab_sys, bg=self._bg)
        f.pack(fill="both", expand=True, padx=8, pady=8)

        # Heartbeat status
        hb_frame = tk.Frame(f, bg=self._bg)
        hb_frame.pack(fill="x", pady=4)
        tk.Label(hb_frame, text="应用心跳:", bg=self._bg, fg="#888",
                 font=("Microsoft YaHei", 10)).pack(side="left")
        self._hb_status = tk.Label(hb_frame, text="检测中...", bg=self._bg,
                                    fg="#fff", font=("Consolas", 12, "bold"))
        self._hb_status.pack(side="left", padx=8)

        # Uptime
        self._uptime_label = tk.Label(f, text="", bg=self._bg, fg="#aaa",
                                       font=("Consolas", 9), anchor="w")
        self._uptime_label.pack(fill="x", pady=2)

        # Info text
        self._sys_info_text = tk.Text(f, bg="#16162a", fg="#ccc",
                                       font=("Consolas", 10),
                                       state="disabled", height=22)
        self._sys_info_text.pack(fill="both", expand=True, pady=4)

        # Buttons
        btn_row = tk.Frame(f, bg=self._bg)
        btn_row.pack(fill="x", pady=4)
        tk.Button(btn_row, text="刷新系统信息", command=self._refresh_system,
                  bg="#4444aa", fg="white",
                  font=("Microsoft YaHei", 9)).pack(side="left", padx=4)
        tk.Button(btn_row, text="清除所有日志", command=self._clear_all_logs,
                  bg="#aa4444", fg="white",
                  font=("Microsoft YaHei", 9)).pack(side="left", padx=4)
        tk.Button(btn_row, text="打开日志目录", command=self._open_log_dir,
                  bg="#555577", fg="white",
                  font=("Microsoft YaHei", 9)).pack(side="left", padx=4)

    # ── Tray Diag Tab ──────────────────────────────────────

    def _build_tray_tab(self) -> None:
        f = tk.Frame(self._tab_tray, bg=self._bg)
        f.pack(fill="both", expand=True, padx=8, pady=8)

        # Status indicators
        status_frame = tk.Frame(f, bg=self._bg)
        status_frame.pack(fill="x", pady=4)

        for i, (label, key) in enumerate([
            ("核选项 (action_handler):", "action_handler"),
            ("GL队列 (gl_queue):",       "gl_queue"),
        ]):
            tk.Label(status_frame, text=label, bg=self._bg, fg="#888",
                     font=("Microsoft YaHei", 10)).grid(row=0, column=i*2, sticky="e", padx=4)
            lbl = tk.Label(status_frame, text="—", bg=self._bg, fg="#fff",
                           font=("Consolas", 11, "bold"))
            lbl.grid(row=0, column=i*2+1, sticky="w", padx=4)
            setattr(self, f"_tray_{key}_lbl", lbl)

        # Click log
        tk.Label(f, text="托盘点击记录 (最近100条)", bg=self._bg, fg="#888",
                 font=("Microsoft YaHei", 9), anchor="w").pack(
            anchor="w", pady=(12, 2))
        self._tray_click_text = scrolledtext.ScrolledText(
            f, bg="#16162a", fg="#ccc", insertbackground="white",
            font=("Consolas", 9), wrap="word", height=14)
        self._tray_click_text.pack(fill="both", expand=True)

        # Crash log
        tk.Label(f, text="处理器崩溃 (如有)", bg=self._bg, fg="#ff8080",
                 font=("Microsoft YaHei", 9), anchor="w").pack(
            anchor="w", pady=(8, 2))
        self._tray_crash_text = scrolledtext.ScrolledText(
            f, bg="#16162a", fg="#ff8080", insertbackground="white",
            font=("Consolas", 9), wrap="word", height=6)
        self._tray_crash_text.pack(fill="x")

    # ── Tailers ────────────────────────────────────────────

    def _start_core_tailers(self) -> None:
        """Start tailers for FPS, SMTC, and tray diag (always needed)."""
        for name in ("render", "smtc", "tray_diag"):
            self._ensure_tailer(name)

    def _ensure_tailer(self, name: str) -> LogTailer:
        if name in self._tailers:
            return self._tailers[name]
        if name == "tray_diag":
            path = TRAY_DIAG_FILE
        else:
            path = os.path.join(self._log_dir, f"qqoverlay_{name}.log")
        t = LogTailer(path)
        self._tailers[name] = t
        # Init buffer
        if name not in self._log_buffers:
            self._log_buffers[name] = deque(maxlen=500)
        return t

    # ── Poll Loop ──────────────────────────────────────────

    def _poll(self) -> None:
        """Main refresh loop (runs every REFRESH_MS)."""
        try:
            # ── FPS from render log ──
            render_tailer = self._ensure_tailer("render")
            for line in render_tailer.get_lines(80):
                self._fps_data.feed(line)

            # ── SMTC data ──
            smtc_tailer = self._ensure_tailer("smtc")
            for line in smtc_tailer.get_lines(30):
                self._smtc_data.feed(line)
            # Buffer SMTC raw lines
            buf = self._log_buffers.setdefault("smtc", deque(maxlen=500))
            for line in smtc_tailer.get_lines(20):
                buf.append(line)

            # ── Tray diag ──
            tray_tailer = self._ensure_tailer("tray_diag")
            for line in tray_tailer.get_lines(20):
                self._tray_data.feed(line)

            # ── Current log (for log viewer tab) ──
            if self._current_log_name not in ("render", "smtc"):
                tailer = self._ensure_tailer(self._current_log_name)
                buf = self._log_buffers.setdefault(
                    self._current_log_name, deque(maxlen=500))
                for line in tailer.get_lines(50):
                    buf.append(line)

            # ── Render log buffer (for log viewer when render selected) ──
            buf_render = self._log_buffers.setdefault("render", deque(maxlen=500))
            render_tailer = self._ensure_tailer("render")
            for line in render_tailer.get_lines(50):
                buf_render.append(line)

            # ── Update UI ──
            self._update_fps_tab()
            self._update_smtc_tab()
            self._update_log_tab()
            self._update_system_tab()
            self._update_tray_tab()
            self._update_status_bar()

        except Exception:
            pass  # Don't crash the monitor on transient errors

        self._root.after(REFRESH_MS, self._poll)

    # ── Tab Updates ────────────────────────────────────────

    def _update_fps_tab(self) -> None:
        stats = self._fps_data.stats
        fps_str = stats["current"]
        try:
            fps_val = float(fps_str)
            if fps_val >= 55:
                color = "#80ff80"
            elif fps_val >= 30:
                color = "#ffff80"
            else:
                color = "#ff8080"
        except ValueError:
            color = "#888"
        self._fps_big.config(text=f"{fps_str} fps", fg=color)

        for key, lbl in self._fps_stats.items():
            lbl.config(text=stats.get(key, "--"))

        # Check staleness
        if self._fps_data.last_ts and time.time() - self._fps_data.last_ts > 3:
            self._fps_stale.config(text="⚠ FPS 数据停滞 — 渲染循环可能已停摆")
        else:
            self._fps_stale.config(text="")

        # Canvas
        self._fps_canvas.update(self._fps_data.current,
                                self._fps_data.phase_pcts(),
                                stats)

    def _update_smtc_tab(self) -> None:
        d = self._smtc_data
        vals = {
            "status": d.status, "player": d.player,
            "position": d.position, "duration": d.duration,
            "title": d.title, "artist": d.artist,
            "poll_count": d.poll_count,
            "last_session_change": d.last_session_change,
        }
        for key, lbl in self._smtc_labels.items():
            lbl.config(text=vals.get(key, "—"))

        # Raw log
        buf = self._log_buffers.get("smtc", deque())
        self._text_smtc_raw.delete("1.0", "end")
        for line in list(buf)[-50:]:
            self._text_smtc_raw.insert("end", line + "\n")
        self._text_smtc_raw.see("end")

    def _update_log_tab(self) -> None:
        name = self._current_log_name
        buf = self._log_buffers.get(name, deque())
        lines = list(buf)
        self._log_text.delete("1.0", "end")
        for line in lines[-200:]:
            self._log_text.insert("end", line + "\n")
        # Colorize
        self._colorize_log(self._log_text, lines[-200:])
        self._log_text.see("end")
        self._log_count_label.config(text=f"{len(lines)} 行")

    def _update_system_tab(self) -> None:
        # Heartbeat
        alive, last_ts = self._check_heartbeat()
        if alive:
            self._hb_status.config(text="● 在线", fg="#80ff80")
        elif last_ts > 0:
            ago = time.time() - last_ts
            self._hb_status.config(text=f"○ 离线 ({ago:.0f}s前)", fg="#ff8080")
        else:
            self._hb_status.config(text="○ 无心跳文件", fg="#ff8080")

        # Uptime
        if last_ts > 0 and alive:
            uptime_s = time.time() - last_ts
            # Heartbeat updates every 2s, so the file timestamp isn't uptime.
            # Just show that it's alive.
            pass
        self._uptime_label.config(
            text=f"最后心跳: {datetime.datetime.fromtimestamp(last_ts).strftime('%H:%M:%S') if last_ts > 0 else '无'}")

    def _update_tray_tab(self) -> None:
        d = self._tray_data
        self._tray_action_handler_lbl.config(
            text=d.action_handler_status,
            fg="#80ff80" if "已连接" in d.action_handler_status else "#ff8080")
        self._tray_gl_queue_lbl.config(
            text=d.gl_queue_status,
            fg="#80ff80" if "已连接" in d.gl_queue_status else "#ff8080")

        # Click log
        self._tray_click_text.delete("1.0", "end")
        for click in d.clicks:
            self._tray_click_text.insert("end", click + "\n")
        self._tray_click_text.see("end")

        # Crash log
        self._tray_crash_text.delete("1.0", "end")
        for crash in d.crashes:
            self._tray_crash_text.insert("end", crash + "\n")
        self._tray_crash_text.see("end")

    def _update_status_bar(self) -> None:
        stats = self._fps_data.stats
        alive = "●" if self._check_heartbeat()[0] else "○"
        fps = stats.get("current", "--")
        self._status_bar.config(
            text=f"{alive} 应用 {'在线' if alive == '●' else '离线'} | "
                 f"FPS: {fps} | "
                 f"日志类型: {self._current_log_name} | "
                 f"刷新: {REFRESH_MS}ms")

    # ── Helpers ────────────────────────────────────────────

    def _colorize_log(self, text_widget, lines: list[str]) -> None:
        """Apply color tags to log lines in a text widget."""
        for i, line in enumerate(lines):
            line_start = f"{i + 1}.0"
            line_end = f"{i + 1}.end"
            if "ERROR" in line or "[ERROR]" in line:
                text_widget.tag_add("error", line_start, line_end)
            elif "WARN" in line or "[WARN]" in line:
                text_widget.tag_add("warn", line_start, line_end)
            elif "[INFO]" in line and "FPS" in line:
                text_widget.tag_add("fps_line", line_start, line_end)
            elif "[INFO]" in line:
                text_widget.tag_add("info", line_start, line_end)

    def _check_heartbeat(self) -> tuple[bool, float]:
        """Return (is_alive, last_timestamp)."""
        try:
            if os.path.exists(HEARTBEAT_FILE):
                with open(HEARTBEAT_FILE, "r") as f:
                    ts = float(f.read().strip())
                return (time.time() - ts < 10, ts)
        except Exception:
            pass
        return (False, 0.0)

    # ── Log Viewer Actions ─────────────────────────────────

    def _on_log_switch(self, event=None) -> None:
        """Switch the log viewer to a different log type."""
        display_name = self._log_var.get()
        self._current_log_name = display_name
        self._ensure_tailer(display_name)
        self._update_log_tab()

    def _clear_current_log(self) -> None:
        """Clear the current log buffer and text."""
        name = self._current_log_name
        self._log_buffers[name] = deque(maxlen=500)
        self._log_text.delete("1.0", "end")
        # Also clear the file
        if name != "tray_diag":
            path = os.path.join(self._log_dir, f"qqoverlay_{name}.log")
        else:
            path = TRAY_DIAG_FILE
        try:
            with open(path, "w") as f:
                f.write("")
        except Exception:
            pass

    def _refresh_current_log(self) -> None:
        """Force refresh the current log view."""
        self._update_log_tab()

    # ── System Tab Actions ─────────────────────────────────

    def _refresh_system(self) -> None:
        """Rebuild the system info display."""
        lines = []
        lines.append("═" * 55)
        lines.append(f"  QQMusicOverlay NG — 系统信息")
        lines.append(f"  时间: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
        lines.append("═" * 55)

        # Heartbeat
        alive, last_ts = self._check_heartbeat()
        lines.append(f"\n  应用状态: {'● 在线' if alive else '○ 离线'}")
        if last_ts > 0:
            lines.append(f"  最后心跳: {datetime.datetime.fromtimestamp(last_ts):%H:%M:%S}")
            lines.append(f"  心跳年龄: {time.time() - last_ts:.1f}s")

        # Log files
        lines.append(f"\n── 日志文件 ({self._log_dir}) ──")
        total_size = 0
        for fname, name, label in LOG_SOURCES:
            path = os.path.join(self._log_dir, fname)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            total_size += size
            size_str = f"{size:>10,} B" if size < 1024 else \
                       f"{size/1024:>10.1f} KB" if size < 1048576 else \
                       f"{size/1048576:>10.1f} MB"
            lines.append(f"  {fname:<35} {size_str}")
        total_str = f"{total_size:>10,} B" if total_size < 1024 else \
                    f"{total_size/1024:>10.1f} KB" if total_size < 1048576 else \
                    f"{total_size/1048576:>10.1f} MB"
        lines.append(f"  {'总计':<35} {total_str}")

        # Heartbeat file
        hb_exists = os.path.exists(HEARTBEAT_FILE)
        hb_size = os.path.getsize(HEARTBEAT_FILE) if hb_exists else 0
        lines.append(f"\n  心跳文件: {'存在' if hb_exists else '不存在'} ({hb_size} B)")

        # Tray diag file
        td_exists = os.path.exists(TRAY_DIAG_FILE)
        td_size = os.path.getsize(TRAY_DIAG_FILE) if td_exists else 0
        lines.append(f"  托盘诊断: {'存在' if td_exists else '不存在'} ({td_size} B)")

        # Constants
        lines.append(f"\n── 核心常量 ──")
        try:
            from core.constants import (
                SMTC_POLL_INTERVAL, READER_THROTTLE, FPS_DEFAULT, FPS_CAP,
                CRASH_MAX_RESTARTS, CRASH_WINDOW, WATCHDOG_INTERVAL,
                WATCHDOG_STALE,
            )
            lines.append(f"  SMTC_POLL_INTERVAL:   {SMTC_POLL_INTERVAL}s")
            lines.append(f"  READER_THROTTLE:      {READER_THROTTLE}s")
            lines.append(f"  FPS_DEFAULT / CAP:    {FPS_DEFAULT} / {FPS_CAP}")
            lines.append(f"  WATCHDOG_INTERVAL:    {WATCHDOG_INTERVAL}s")
            lines.append(f"  WATCHDOG_STALE:       {WATCHDOG_STALE}s")
            lines.append(f"  CRASH_MAX_RESTARTS:   {CRASH_MAX_RESTARTS}")
            lines.append(f"  CRASH_WINDOW:         {CRASH_WINDOW}s")
        except ImportError:
            lines.append("  (无法导入 constants)")

        # Version
        try:
            from version import get_version, get_build_stamp
            lines.append(f"\n  版本: {get_version()} (build {get_build_stamp()})")
        except ImportError:
            pass

        self._sys_info_text.config(state="normal")
        self._sys_info_text.delete("1.0", "end")
        self._sys_info_text.insert("1.0", "\n".join(lines))
        self._sys_info_text.config(state="disabled")

    def _clear_all_logs(self) -> None:
        """Clear all log files and buffers."""
        for fname, name, _ in LOG_SOURCES:
            path = os.path.join(self._log_dir, fname)
            try:
                with open(path, "w") as f:
                    f.write("")
            except Exception:
                pass
            self._log_buffers[name] = deque(maxlen=500)

        # Also clear tray diag
        try:
            with open(TRAY_DIAG_FILE, "w") as f:
                f.write("")
        except Exception:
            pass

        self._smtc_data = SMTCData()
        self._tray_data = TrayDiagData()
        self._log_text.delete("1.0", "end")
        self._status_bar.config(text="所有日志已清除")

    def _open_log_dir(self) -> None:
        """Open the log directory in Explorer."""
        try:
            os.startfile(self._log_dir)
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────

    def run(self) -> None:
        """Run standalone."""
        self._refresh_system()
        self._root.mainloop()

    def close(self) -> None:
        """Stop all tailers and close."""
        for t in self._tailers.values():
            t.stop()
        try:
            self._root.destroy()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# Standalone entry point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Add project root to path so optional imports (version, constants) resolve
    _proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _proj not in sys.path:
        sys.path.insert(0, _proj)
    MonitorWindow().run()
