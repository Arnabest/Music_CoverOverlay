"""QQMusicOverlay NG — 独立实时性能监控工具

用法:
    python monitor.py              # 实时监控
    python monitor.py --once       # 打印最近数据后退出
    python monitor.py --watch      # 逐行流式输出

依赖: 无外部依赖 (仅 Python 标准库)
"""

import sys, os, time, re, io
from collections import deque
from datetime import datetime

# ★ Force UTF-8 output on Windows
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

TEMP = os.environ.get("TEMP", ".")
LOG_FILES = {
    "render": os.path.join(TEMP, "qqoverlay_render.log"),
    "smtc":   os.path.join(TEMP, "qqoverlay_smtc.log"),
    "reader": os.path.join(TEMP, "qqoverlay_reader.log"),
    "errors": os.path.join(TEMP, "qqoverlay_errors.log"),
}

# ── ANSI colors ────────────────────────────────────────────────────────────────

class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    RED = "\033[91m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
    BLUE = "\033[94m"; MAGENTA = "\033[95m"; CYAN = "\033[96m"
    WHITE = "\033[97m"; GRAY = "\033[90m"

    @staticmethod
    def fps_color(fps):
        if fps >= 55: return C.GREEN
        elif fps >= 30: return C.YELLOW
        return C.RED

    @staticmethod
    def ms_color(ms):
        if ms <= 16: return C.GREEN
        elif ms <= 33: return C.YELLOW
        return C.RED


# ── Regex patterns (new [timestamp] [subsystem] [level] format) ─────────────────

FPS_RE = re.compile(
    r"\[render\].*FPS\s+([\d.]+)\s*\|\s*frame=([\d.]+)ms"
    r"\s*\|\s*data=([\d.]+)ms\s+setup=([\d.]+)ms\s+r\+ulw=([\d.]+)ms"
)

SMTC_RE = re.compile(r"\[smtc\]")
READER_RE = re.compile(r"\[reader\]")
ERROR_RE = re.compile(r"\[error\]|Traceback|Error|error|FAIL|CRASH", re.IGNORECASE)
ENGINE_START_RE = re.compile(r"\[render\].*(?:FPS|Window at|GL Engine)")
HEARTBEAT_RE = re.compile(r"\[render\].*frame=(\d+)\s+dt=")


class MonitorState:
    def __init__(self):
        self.backend = "GL"
        self.fps_current = 0.0
        self.fps_min = float("inf")
        self.fps_max = 0.0
        self.fps_history: deque[float] = deque(maxlen=300)
        self.frame_ms = 0.0
        self.frame_ms_min = float("inf")
        self.frame_ms_max = 0.0
        self.g_data = self.g_setup = self.g_render = 0.0
        self.smtc_polls = 0
        self.errors = 0
        self.heartbeat_frame = 0
        self.reader_events = 0
        self.line_count = 0
        self.last_update = time.time()

    def update_from_line(self, line: str) -> str | None:
        self.line_count += 1

        # ── FPS (render log) ──
        m = FPS_RE.search(line)
        if m:
            fps = float(m.group(1))
            ms = float(m.group(2))
            self._update_fps(fps, ms)
            self.g_data = float(m.group(3))
            self.g_setup = float(m.group(4))
            self.g_render = float(m.group(5))
            return "fps"

        # ── Heartbeat ──
        m = HEARTBEAT_RE.search(line)
        if m:
            self.heartbeat_frame = int(m.group(1))

        # ── Engine start ──
        if ENGINE_START_RE.search(line):
            return "info"

        # ── SMTC (smtc log) ──
        if SMTC_RE.search(line):
            self.smtc_polls += 1

        # ── Reader (reader log) ──
        if READER_RE.search(line):
            self.reader_events += 1

        # ── Errors ──
        if ERROR_RE.search(line):
            self.errors += 1
            return "error"

        return None

    def _update_fps(self, fps: float, ms: float):
        self.fps_current = fps
        self.frame_ms = ms
        self.fps_min = min(self.fps_min, fps)
        self.fps_max = max(self.fps_max, fps)
        self.frame_ms_min = min(self.frame_ms_min, ms)
        self.frame_ms_max = max(self.frame_ms_max, ms)
        self.fps_history.append(fps)
        self.last_update = time.time()

    @property
    def fps_avg(self) -> float:
        if not self.fps_history: return 0.0
        return sum(self.fps_history) / len(self.fps_history)

    @property
    def fps_1pct(self) -> float:
        if len(self.fps_history) < 10:
            return self.fps_min if self.fps_min != float("inf") else 0.0
        sorted_fps = sorted(self.fps_history)
        idx = max(0, len(sorted_fps) // 100)
        return sorted_fps[idx]


# ── Multi-file tailer ──────────────────────────────────────────────────────────

class MultiLogTailer:
    """Poll multiple log files for new lines."""

    def __init__(self, paths: dict[str, str]):
        self._files: dict[str, dict] = {}
        for name, path in paths.items():
            self._files[name] = {"path": path, "pos": 0, "inode": None}

    def read_new_lines(self) -> list[tuple[str, str]]:
        """Return [(source_name, line), ...] for all new lines."""
        results = []
        for name, finfo in self._files.items():
            path = finfo["path"]
            if not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
                sz = stat.st_size
                ino = stat.st_ino if hasattr(stat, 'st_ino') else sz

                if finfo["inode"] is not None and ino != finfo["inode"]:
                    finfo["pos"] = 0
                finfo["inode"] = ino
                if sz < finfo["pos"]:
                    finfo["pos"] = 0
                if sz == finfo["pos"]:
                    continue

                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(finfo["pos"])
                    data = f.read()
                    finfo["pos"] = f.tell()

                for line in data.splitlines():
                    if line.strip():
                        results.append((name, line))
            except Exception:
                pass
        return results


# ── Display ────────────────────────────────────────────────────────────────────

def clear_screen():
    sys.stdout.write("\033[2J\033[H")

def format_bar(value, max_val, width=12, color_fn=None):
    ratio = min(1.0, value / max_val) if max_val > 0 else 0
    filled = int(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    if color_fn:
        return f"{color_fn(value)}{bar}{C.RESET}"
    return bar

def render_dashboard(state: MonitorState, recent_errors: list[str]):
    lines = []
    now = datetime.now().strftime("%H:%M:%S")

    lines.append(f"{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════════╗{C.RESET}")
    lines.append(f"{C.BOLD}{C.CYAN}║{C.RESET}  {C.BOLD}QQMusicOverlay NG — 实时性能监控{C.RESET}          {now}  {C.BOLD}{C.CYAN}║{C.RESET}")
    lines.append(f"{C.BOLD}{C.CYAN}╚══════════════════════════════════════════════════════════╝{C.RESET}")
    lines.append("")

    fps = state.fps_current
    ms = state.frame_ms

    lines.append(f"  {C.BOLD}FPS:{C.RESET}        {C.fps_color(fps)}{fps:6.1f}{C.RESET}  "
                 f"{C.DIM}│ min:{C.RESET} {C.fps_color(state.fps_min)}{state.fps_min:5.1f}{C.RESET}  "
                 f"{C.DIM}│ max:{C.RESET} {C.fps_color(state.fps_max)}{state.fps_max:5.1f}{C.RESET}  "
                 f"{C.DIM}│ avg:{C.RESET} {C.fps_color(state.fps_avg)}{state.fps_avg:5.1f}{C.RESET}  "
                 f"{C.DIM}│ 1%low:{C.RESET} {C.fps_color(state.fps_1pct)}{state.fps_1pct:5.1f}{C.RESET}")

    lines.append(f"  {C.BOLD}帧耗时:{C.RESET}    {C.ms_color(ms)}{ms:6.1f}ms{C.RESET}  "
                 f"{C.DIM}│ min:{C.RESET} {state.frame_ms_min:5.1f}ms  "
                 f"{C.DIM}│ max:{C.RESET} {state.frame_ms_max:5.1f}ms")

    lines.append(f"  {C.BOLD}后端:{C.RESET}      {C.MAGENTA}{state.backend}{C.RESET}  "
                 f"{C.DIM}│ 行数:{C.RESET} {state.line_count}  "
                 f"{C.DIM}│ 心跳帧:{C.RESET} {state.heartbeat_frame}")

    lines.append("")

    # GL phase timing
    total_g = state.g_data + state.g_setup + state.g_render
    max_g = max(total_g, 16.7)
    lines.append(f"  {C.BOLD}逐阶段耗时 (GL):{C.RESET}")
    for label, val, color in [
        ("数据获取", state.g_data, C.GRAY),
        ("渲染设置", state.g_setup, C.CYAN),
        ("GL+ULW", state.g_render, C.MAGENTA),
    ]:
        bar = format_bar(val, max_g, 20, lambda _v, c=color: c)
        lines.append(f"    {label:<10} {bar} {val:5.1f}ms")

    lines.append("")

    # Status
    lines.append(f"  {C.BOLD}SMTC轮询:{C.RESET}  {state.smtc_polls:5d}次  "
                 f"{C.DIM}│{C.RESET}  "
                 f"{C.BOLD}Reader事件:{C.RESET} {state.reader_events:5d}次  "
                 f"{C.DIM}│{C.RESET}  "
                 f"{C.BOLD}错误:{C.RESET} {C.RED if state.errors else C.GREEN}"
                 f"{state.errors}{C.RESET}")

    # FPS sparkline
    if len(state.fps_history) > 5:
        spark = _sparkline(state.fps_history, width=50)
        lines.append(f"\n  {C.DIM}FPS 趋势:{C.RESET} {spark}")

    # Recent errors
    if recent_errors:
        lines.append(f"\n  {C.BOLD}{C.RED}── 最近的错误 ──{C.RESET}")
        for err in recent_errors[-5:]:
            short = err[:120] + "..." if len(err) > 120 else err
            lines.append(f"  {C.RED}{short}{C.RESET}")

    # Footer
    age = time.time() - state.last_update
    if age > 5:
        lines.append(f"\n  {C.YELLOW}⚠ {age:.0f}s 无 FPS 数据 — 应用是否在运行?{C.RESET}")

    lines.append(f"\n  {C.DIM}Ctrl+C 退出 │ 监控: render/smtc/reader/errors 日志{C.RESET}")

    return "\n".join(lines)


def _sparkline(values: deque, width=50) -> str:
    if len(values) < 2:
        return ""
    vmin = min(values); vmax = max(values)
    if vmax == vmin: vmax = vmin + 1
    chars = " ._-=+*#@"
    step = max(1, len(values) // width)
    sampled = [values[i] for i in range(0, len(values), step)][-width:]
    result = ""
    for v in sampled:
        idx = int((v - vmin) / (vmax - vmin) * (len(chars) - 1))
        idx = max(0, min(len(chars) - 1, idx))
        result += f"{C.fps_color(v)}{chars[idx]}{C.RESET}"
    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    once = "--once" in sys.argv
    watch = "--watch" in sys.argv

    if once:
        # Print latest FPS and errors
        for name in ("render", "errors"):
            path = LOG_FILES[name]
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    for line in reversed(content.splitlines()):
                        if "FPS" in line and name == "render":
                            print(line.strip())
                            break
                    for line in content.splitlines()[-10:]:
                        if ERROR_RE.search(line):
                            print(f"{C.RED}{line.strip()[:150]}{C.RESET}")
                except Exception as e:
                    print(f"读取 {name} 失败: {e}")
        return

    state = MonitorState()
    tailer = MultiLogTailer(LOG_FILES)
    recent_errors: list[str] = []

    if watch:
        print(f"  流式监控 (--watch)  日志: {list(LOG_FILES.values())}")
        print(f"  {'FPS':>6s}  {'帧耗时':>7s}  "
              f"{'data':>6s}  {'setup':>6s}  {'r+ulw':>6s}  "
              f"{'SMTC':>5s}  {'Err':>3s}")
        print(f"  {'-'*6}  {'-'*7}  "
              f"{'-'*6}  {'-'*6}  {'-'*6}  "
              f"{'-'*5}  {'-'*3}")

        try:
            while True:
                new_lines = tailer.read_new_lines()
                for src, line in new_lines:
                    result = state.update_from_line(line)
                    if result == "fps":
                        print(f"  {state.fps_current:5.1f}fps  "
                              f"{state.frame_ms:5.1f}ms  "
                              f"{state.g_data:5.1f}ms  {state.g_setup:5.1f}ms  {state.g_render:5.1f}ms  "
                              f"{state.smtc_polls:5d}  {state.errors:3d}")
                time.sleep(0.25)
        except KeyboardInterrupt:
            print(f"\n{C.GREEN}监控已停止。{C.RESET}")
        return

    # Interactive dashboard
    clear_screen()
    print(f"{C.DIM}正在启动监控... (日志目录: {TEMP}){C.RESET}")
    if not any(os.path.exists(p) for p in LOG_FILES.values()):
        print(f"{C.YELLOW}警告: 日志文件尚不存在 — 请先启动 QQMusicOverlay NG{C.RESET}")

    try:
        while True:
            new_lines = tailer.read_new_lines()
            updated = False
            for src, line in new_lines:
                result = state.update_from_line(line)
                if result == "fps":
                    updated = True
                elif result == "error":
                    recent_errors.append(f"[{src}] {line.strip()}")
                    if len(recent_errors) > 20:
                        recent_errors.pop(0)
                    updated = True

            if updated or not getattr(main, '_first_render', False):
                main._first_render = True  # type: ignore
                clear_screen()
                sys.stdout.write(render_dashboard(state, recent_errors))
                sys.stdout.flush()

            time.sleep(0.25)

    except KeyboardInterrupt:
        clear_screen()
        print(f"{C.GREEN}监控已停止。{C.RESET}")
        print(f"  后端: {state.backend}")
        print(f"  FPS: {state.fps_current:.1f} (avg: {state.fps_avg:.1f}, "
              f"min: {state.fps_min:.1f}, max: {state.fps_max:.1f})")
        print(f"  帧耗时: {state.frame_ms:.1f}ms")
        print(f"  错误数: {state.errors}")
        print(f"  处理行数: {state.line_count}")


if __name__ == "__main__":
    main()
