"""Centralized logging with per-subsystem log file separation.

Usage:
    from core.logs import get_logger
    log = get_logger("smtc")
    log.info("SMTC poll started")
    log.warn("thumbnail timeout")
    log.error("COM initialization failed", exc_info=True)
"""
import os, threading, datetime, traceback
from core.constants import LOG_BASE_DIR

# Log files per subsystem
LOG_FILES = {
    "smtc":    "qqoverlay_smtc.log",
    "reader":  "qqoverlay_reader.log",
    "window":  "qqoverlay_window.log",
    "render":  "qqoverlay_render.log",   # FPS, heartbeat, GL diag, painter
    "engine":  "qqoverlay_engine.log",   # PIL engine startup/exceptions
    "compositor": "qqoverlay_compositor.log",
    "settings":"qqoverlay_settings.log",
    "system":  "qqoverlay_system.log",   # main.py, startup, shutdown, crashes
    "error":   "qqoverlay_errors.log",   # cross-cutting error log
}


class Logger:
    """Thread-safe per-subsystem logger with periodic auto-trim."""

    _TRIM_CHECK_INTERVAL = 100  # check size every N writes
    _MAX_SIZE = 512 * 1024      # 512 KB per file

    def __init__(self, name: str, filepath: str):
        self._name = name
        self._path = filepath
        self._lock = threading.Lock()
        self._last_rates: dict[str, float] = {}
        self._write_count = 0

    def _maybe_trim(self) -> None:
        """Trim log if it exceeds MAX_SIZE. Called every N writes."""
        self._write_count += 1
        if self._write_count % self._TRIM_CHECK_INTERVAL != 0:
            return
        try:
            if os.path.getsize(self._path) > self._MAX_SIZE:
                with open(self._path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                keep = max(1, len(lines) // 2)  # keep last half
                with open(self._path, "w", encoding="utf-8") as f:
                    f.write(f"[trimmed {datetime.datetime.now():%H:%M:%S} — "
                            f"keeping {keep}/{len(lines)} lines]\n")
                    f.writelines(lines[-keep:])
        except Exception:
            pass

    def _write(self, level: str, msg: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] [{self._name}] [{level}] {msg}\n"
        with self._lock:
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
                self._maybe_trim()
            except Exception:
                pass

    def debug(self, msg: str) -> None:
        self._write("DEBUG", msg)

    def info(self, msg: str) -> None:
        self._write("INFO", msg)

    def warn(self, msg: str, exc_info: bool = False) -> None:
        if exc_info:
            msg = f"{msg} | {traceback.format_exc().strip().replace(chr(10), ' | ')}"
        self._write("WARN", msg)

    def error(self, msg: str, exc_info: bool = False) -> None:
        if exc_info:
            msg = f"{msg} | {traceback.format_exc().strip().replace(chr(10), ' | ')}"
        self._write("ERROR", msg)

    def rate_limited(self, key: str, interval: float, level: str, msg: str) -> bool:
        """Log at most once per `interval` seconds for the given key.
        Returns True if the message was logged.
        """
        now = datetime.datetime.now().timestamp()
        last = self._last_rates.get(key, 0.0)
        if now - last >= interval:
            self._last_rates[key] = now
            self._write(level, msg)
            return True
        return False


# Module-level logger registry
_loggers: dict[str, Logger] = {}
_registry_lock = threading.Lock()


def get_logger(name: str) -> Logger:
    """Get or create a logger for the named subsystem."""
    if name not in _loggers:
        with _registry_lock:
            if name not in _loggers:
                filename = LOG_FILES.get(name, f"qqoverlay_{name}.log")
                filepath = os.path.join(LOG_BASE_DIR, filename)
                _loggers[name] = Logger(name, filepath)
    return _loggers[name]


def clear_all_logs() -> None:
    """Truncate all log files. Use with caution."""
    for name, logger in _loggers.items():
        try:
            with open(logger._path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass


def trim_logs(max_lines: int = 500) -> None:
    """Check all log files and truncate any exceeding per-logger MAX_SIZE.

    Kept for backward compatibility — the Logger._maybe_trim() method
    now handles trimming automatically during normal operation.
    """
    if max_lines <= 0:
        return
    for name in LOG_FILES:
        filepath = os.path.join(LOG_BASE_DIR, LOG_FILES[name])
        try:
            if os.path.exists(filepath) and os.path.getsize(filepath) > Logger._MAX_SIZE:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                if len(lines) > max_lines:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(f"[truncated at {datetime.datetime.now():%H:%M:%S}]\n")
                        f.writelines(lines[-max_lines:])
        except Exception:
            pass
