"""Standalone test monitor GUI — independent tkinter window for real-time debugging.

Usage:
    python debug/monitor_window.py

Reads from the separated log files in %TEMP% and displays real-time:
- FPS Monitor:  current FPS, frame timing, per-phase breakdown, trend sparkline
- SMTC Status:   position, duration, player, thumbnail, poll count
- Reader Log:    song changes, cover candidates, search results
- Error Log:     all errors/crashes with timestamps
- System Info:   process status, heartbeat, constants
"""
import os, sys, time, re, json, queue, threading, datetime
import tkinter as tk
from tkinter import ttk, scrolledtext

TEMP_DIR = os.environ.get("TEMP", ".")
LOG_DIR = TEMP_DIR  # Log files location
HEARTBEAT_FILE = os.path.join(TEMP_DIR, "qqoverlay_heartbeat.txt")
REFRESH_MS = 500  # Refresh interval for live data


# ============================================================
# Log Tailer (thread-safe)
# ============================================================

class LogTailer:
    """Tails a log file and pushes new lines to a queue."""

    def __init__(self, filepath: str):
        self._path = filepath
        self._queue = queue.Queue()
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
                            self._queue.put(line.strip())
                        self._pos = f.tell()
            except Exception:
                pass
            time.sleep(0.25)

    def stop(self) -> None:
        self._running = False

    def get_lines(self, max_lines: int = 100) -> list[str]:
        """Drain all pending lines."""
        lines = []
        try:
            while True:
                lines.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return lines


# ============================================================
# FPS Parser
# ============================================================

FPS_RE = re.compile(
    r'\[[\d:.]+] \[render] \[INFO] FPS ([\d.]+) \| frame=([\d.]+)ms '
    r'\| data=([\d.]+)ms setup=([\d.]+)ms r\+ulw=([\d.]+)ms '
    r'\| (\d+) frames in ([\d.]+)s')


class FPSMonitor:
    """Tracks FPS history and statistics."""

    def __init__(self):
        self.fps_samples: list[float] = []
        self.frame_times: list[float] = []
        self.data_times: list[float] = []
        self.setup_times: list[float] = []
        self.render_times: list[float] = []
        self.last_update = 0.0

    def feed_line(self, line: str) -> None:
        m = FPS_RE.search(line)
        if m:
            self.fps_samples.append(float(m.group(1)))
            self.frame_times.append(float(m.group(2)))
            self.data_times.append(float(m.group(3)))
            self.setup_times.append(float(m.group(4)))
            self.render_times.append(float(m.group(5)))
            self.last_update = time.time()

    def stats(self) -> dict:
        if not self.fps_samples:
            return {"current": "N/A", "min": "N/A", "max": "N/A", "avg": "N/A"}
        recent = self.fps_samples[-30:]
        return {
            "current": f"{self.fps_samples[-1]:.1f}",
            "min": f"{min(recent):.1f}",
            "max": f"{max(recent):.1f}",
            "avg": f"{sum(recent)/len(recent):.1f}",
            "frame_ms": f"{self.frame_times[-1]:.1f}" if self.frame_times else "N/A",
            "count": len(self.fps_samples),
        }

    def sparkline(self, width: int = 40) -> str:
        if len(self.fps_samples) < 2:
            return ""
        recent = self.fps_samples[-width:]
        mn, mx = min(recent), max(recent)
        if mx == mn:
            mx = mn + 1
        chars = " ▁▂▃▄▅▆▇█"
        return "".join(chars[min(8, int((v - mn) / (mx - mn) * 8))] for v in recent)


# ============================================================
# Main Monitor Window
# ============================================================

class MonitorWindow:
    def __init__(self):
        self._root = tk.Tk()
        self._root.title("QQMusicOverlay NG — 调试监控")
        self._root.geometry("720x560")
        self._root.configure(bg="#1a1a2e")
        self._root.minsize(600, 400)

        # Log tailers
        self._tailers: dict[str, LogTailer] = {}
        self._fps = FPSMonitor()
        self._smtc_lines: list[str] = []
        self._reader_lines: list[str] = []
        self._error_lines: list[str] = []

        self._build_ui()
        self._start_tailers()
        self._poll()

    def _build_ui(self) -> None:
        # Notebook (tabs)
        nb = ttk.Notebook(self._root)
        nb.pack(fill="both", expand=True, padx=4, pady=4)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#1a1a2e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#2a2a3e", foreground="#ccc", padding=[12, 4])
        style.map("TNotebook.Tab", background=[("selected", "#4444aa")])

        # Tab 1: FPS
        self._tab_fps = ttk.Frame(nb)
        nb.add(self._tab_fps, text="FPS 监控")
        self._build_fps_tab()

        # Tab 2: SMTC
        self._tab_smtc = ttk.Frame(nb)
        nb.add(self._tab_smtc, text="SMTC 状态")
        self._build_log_tab(self._tab_smtc, "smtc")

        # Tab 3: Reader
        self._tab_reader = ttk.Frame(nb)
        nb.add(self._tab_reader, text="Reader 日志")
        self._build_log_tab(self._tab_reader, "reader")

        # Tab 4: Errors
        self._tab_errors = ttk.Frame(nb)
        nb.add(self._tab_errors, text="错误")
        self._build_log_tab(self._tab_errors, "errors")

        # Tab 5: System
        self._tab_system = ttk.Frame(nb)
        nb.add(self._tab_system, text="系统")
        self._build_system_tab()

        # Status bar
        self._status = tk.Label(self._root, text="就绪", anchor="w",
                               bg="#2a2a3e", fg="#888", font=("Consolas", 9))
        self._status.pack(side="bottom", fill="x")

    def _build_fps_tab(self) -> None:
        f = ttk.Frame(self._tab_fps)
        f.pack(fill="both", expand=True, padx=8, pady=8)

        # Current FPS (large)
        self._fps_label = tk.Label(f, text="-- fps", font=("Consolas", 36, "bold"),
                                  bg="#1a1a2e", fg="#80ff80")
        self._fps_label.pack(pady=(0, 4))

        # Stats grid
        grid = ttk.Frame(f)
        grid.pack(fill="x", pady=4)
        self._stat_labels: dict[str, tk.Label] = {}
        for i, (key, label) in enumerate([
            ("current", "当前"), ("min", "最低"), ("max", "最高"),
            ("avg", "平均"), ("frame_ms", "帧耗时"), ("count", "样本数"),
        ]):
            tk.Label(grid, text=label, bg="#1a1a2e", fg="#888",
                    font=("Microsoft YaHei", 9)).grid(row=i // 3, column=(i % 3) * 2, sticky="w", padx=4)
            lbl = tk.Label(grid, text="--", bg="#1a1a2e", fg="#fff",
                          font=("Consolas", 11, "bold"))
            lbl.grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=4)
            self._stat_labels[key] = lbl

        # Sparkline
        self._sparkline_label = tk.Label(f, text="", bg="#1a1a2e", fg="#80ff80",
                                        font=("Consolas", 8))
        self._sparkline_label.pack(pady=(8, 4))

        # Phase breakdown bars
        self._phase_frame = ttk.Frame(f)
        self._phase_frame.pack(fill="x", pady=4)
        self._phase_canvas = tk.Canvas(self._phase_frame, height=20, bg="#1a1a2e",
                                      highlightthickness=0)
        self._phase_canvas.pack(fill="x")

    def _build_log_tab(self, parent: ttk.Frame, name: str) -> None:
        text = scrolledtext.ScrolledText(parent, bg="#1a1a2e", fg="#ccc",
                                        insertbackground="white",
                                        font=("Consolas", 9), wrap="word")
        text.pack(fill="both", expand=True, padx=4, pady=4)
        setattr(self, f"_text_{name}", text)

    def _build_system_tab(self) -> None:
        f = ttk.Frame(self._tab_system)
        f.pack(fill="both", expand=True, padx=8, pady=8)

        self._sys_text = tk.Text(f, bg="#1a1a2e", fg="#ccc",
                                font=("Consolas", 10), height=20,
                                state="disabled")
        self._sys_text.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(f)
        btn_frame.pack(fill="x", pady=4)
        tk.Button(btn_frame, text="刷新系统信息", command=self._refresh_system,
                bg="#4444aa", fg="white", font=("Microsoft YaHei", 9)).pack(side="left", padx=4)
        tk.Button(btn_frame, text="清除所有日志", command=self._clear_logs,
                bg="#aa4444", fg="white", font=("Microsoft YaHei", 9)).pack(side="left", padx=4)

    def _start_tailers(self) -> None:
        for name in ["render", "smtc", "reader", "window"]:
            path = os.path.join(LOG_DIR, f"qqoverlay_{name}.log")
            self._tailers[name] = LogTailer(path)
        # Error log is optional
        err_path = os.path.join(LOG_DIR, "qqoverlay_errors.log")
        self._tailers["errors"] = LogTailer(err_path)

    def _poll(self) -> None:
        """Periodic refresh."""
        try:
            # Process render log → FPS
            for line in self._tailers.get("render", LogTailer("")).get_lines(50):
                self._fps.feed_line(line)

            # Process SMTC log
            smtc_lines = self._tailers.get("smtc", LogTailer("")).get_lines(20)
            self._smtc_lines.extend(smtc_lines)
            self._smtc_lines = self._smtc_lines[-50:]

            # Process reader log
            reader_lines = self._tailers.get("reader", LogTailer("")).get_lines(20)
            self._reader_lines.extend(reader_lines)
            self._reader_lines = self._reader_lines[-50:]

            # Process error log
            err_lines = self._tailers.get("errors", LogTailer("")).get_lines(10)
            self._error_lines.extend(err_lines)
            self._error_lines = self._error_lines[-30:]

            # Update UI
            self._update_fps_tab()
            self._update_log_display("smtc", self._smtc_lines)
            self._update_log_display("reader", self._reader_lines)
            self._update_log_display("errors", self._error_lines)

            # Status
            stats = self._fps.stats()
            alive = "● 运行中" if self._check_heartbeat() else "○ 无心跳"
            self._status.config(text=f"{alive} | FPS: {stats['current']} | "
                                f"SMTC: {len(self._smtc_lines)}行 | "
                                f"错误: {len(self._error_lines)}条")

        except Exception:
            pass

        self._root.after(REFRESH_MS, self._poll)

    def _update_fps_tab(self) -> None:
        stats = self._fps.stats()
        fps = stats["current"]
        try:
            fps_val = float(fps)
            color = "#80ff80" if fps_val >= 55 else "#ffff80" if fps_val >= 30 else "#ff8080"
        except ValueError:
            color = "#888"
        self._fps_label.config(text=f"{fps} fps", fg=color)

        for key, lbl in self._stat_labels.items():
            lbl.config(text=stats.get(key, "--"))

        self._sparkline_label.config(text=self._fps.sparkline(50))

    def _update_log_display(self, name: str, lines: list[str]) -> None:
        text = getattr(self, f"_text_{name}", None)
        if text is None:
            return
        text.delete("1.0", "end")
        for line in lines[-100:]:
            # Color errors red
            if "ERROR" in line or "error" in line.lower():
                text.insert("end", line + "\n", "error")
            elif "WARN" in line:
                text.insert("end", line + "\n", "warn")
            else:
                text.insert("end", line + "\n")
        text.see("end")
        text.tag_config("error", foreground="#ff8080")
        text.tag_config("warn", foreground="#ffff80")

    def _check_heartbeat(self) -> bool:
        try:
            if os.path.exists(HEARTBEAT_FILE):
                with open(HEARTBEAT_FILE, "r") as f:
                    ts = float(f.read().strip())
                return time.time() - ts < 10
        except Exception:
            pass
        return False

    def _refresh_system(self) -> None:
        info_lines = ["=== QQMusicOverlay NG 系统信息 ===\n"]
        info_lines.append(f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Heartbeat
        hb = "在线" if self._check_heartbeat() else "离线/无心跳"
        info_lines.append(f"应用状态: {hb}")

        # Log files
        info_lines.append("\n--- 日志文件 ---")
        for name in ["render", "smtc", "reader", "window", "errors"]:
            path = os.path.join(LOG_DIR, f"qqoverlay_{name}.log")
            size = os.path.getsize(path) if os.path.exists(path) else 0
            info_lines.append(f"  qqoverlay_{name}.log: {size:,} bytes")

        # Heartbeat file
        hb_path = HEARTBEAT_FILE
        if os.path.exists(hb_path):
            with open(hb_path) as f:
                info_lines.append(f"\n心跳文件: {f.read().strip()}")

        # Constants
        info_lines.append("\n--- 核心常量 ---")
        try:
            from core.constants import (
                SMTC_POLL_INTERVAL, READER_THROTTLE, FPS_DEFAULT, FPS_CAP,
                CRASH_MAX_RESTARTS, CRASH_WINDOW, WATCHDOG_INTERVAL,
            )
            info_lines.append(f"  SMTC_POLL_INTERVAL: {SMTC_POLL_INTERVAL}s")
            info_lines.append(f"  READER_THROTTLE: {READER_THROTTLE}s")
            info_lines.append(f"  FPS_DEFAULT: {FPS_DEFAULT}")
            info_lines.append(f"  FPS_CAP: {FPS_CAP}")
            info_lines.append(f"  CRASH_MAX_RESTARTS: {CRASH_MAX_RESTARTS}")
            info_lines.append(f"  WATCHDOG_INTERVAL: {WATCHDOG_INTERVAL}s")
        except ImportError:
            info_lines.append("  (无法导入 constants)")

        self._sys_text.config(state="normal")
        self._sys_text.delete("1.0", "end")
        self._sys_text.insert("1.0", "\n".join(info_lines))
        self._sys_text.config(state="disabled")

    def _clear_logs(self) -> None:
        for name in ["render", "smtc", "reader", "window", "errors"]:
            path = os.path.join(LOG_DIR, f"qqoverlay_{name}.log")
            try:
                with open(path, "w") as f:
                    f.write("")
            except Exception:
                pass
        self._smtc_lines.clear()
        self._reader_lines.clear()
        self._error_lines.clear()
        self._status.config(text="日志已清除")

    def run(self) -> None:
        self._root.mainloop()


if __name__ == "__main__":
    MonitorWindow().run()
