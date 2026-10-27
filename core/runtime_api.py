"""Local runtime API for QQMusicOverlayNG.

Exposes the current overlay music state on localhost for trusted local tools
such as Desktop Agent, and mirrors the same payload into a temp JSON file.
"""
from __future__ import annotations

import http.server
import json
import os
import threading
import time
from copy import deepcopy
from urllib.parse import urlparse

from core.atomic_write import atomic_write_json
from core.logs import get_logger

_api_log = get_logger("runtime_api")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("QQMUSIC_OVERLAY_RUNTIME_PORT", "8766"))
STATE_FILE = os.path.join(os.environ.get("TEMP", "."), "qqoverlay_music_state.json")


class _RuntimeApiHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/health", "/healthz"):
            self._send_json({"ok": True, "server": "QQMusicOverlayNG runtime_api"})
            return
        if parsed.path in ("/", "/state", "/music"):
            snapshot = self.server.owner.snapshot()
            self._send_json(snapshot)
            return
        self.send_response(404)
        self.end_headers()

    def _send_json(self, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class OverlayRuntimeApiServer:
    """Lightweight localhost API + JSON mirror for current overlay state."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        state_file: str = STATE_FILE,
        write_file: bool = True,
        min_write_interval: float = 0.25,
    ):
        self._host = host
        self._port = port
        self._state_file = state_file
        self._write_file = write_file
        self._min_write_interval = max(0.05, float(min_write_interval))
        self._server = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._state: dict = {
            "schema": 1,
            "app": "QQMusicOverlayNG",
            "updated_at_epoch": 0.0,
            "title": "",
            "artist": "",
            "position_sec": 0.0,
            "duration_sec": 0.0,
        }
        self._last_file_write = 0.0
        self._last_serialized = ""

    def start(self) -> bool:
        if self._running:
            return True
        try:
            self._server = http.server.ThreadingHTTPServer((self._host, self._port), _RuntimeApiHandler)
            self._server.owner = self
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="runtime_api")
            self._thread.start()
            self._running = True
            _api_log.info(f"runtime api started on {self.state_url}")
            return True
        except OSError as exc:
            _api_log.warn(f"runtime api failed to start on {self._host}:{self._port}: {exc}")
            self._server = None
            self._thread = None
            self._running = False
            return False

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
        self._server = None
        self._running = False

    def update(self, payload: dict) -> None:
        state = deepcopy(payload or {})
        now = time.time()
        state.setdefault("schema", 1)
        state.setdefault("app", "QQMusicOverlayNG")
        state.setdefault("source", "runtime_api")
        state.setdefault("updated_at_epoch", now)
        state.setdefault("state_url", self.state_url)
        serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._state = state
        if not self._write_file:
            return
        # ★ Real rate limit on the file mirror: ANY write within the min
        # interval is skipped (position changes every frame, so comparing
        # content alone never throttled — the old double condition checked
        # `serialized == last` twice and passed changed payloads straight
        # through).  In-memory state above is always current regardless.
        if (now - self._last_file_write) < self._min_write_interval:
            return
        try:
            atomic_write_json(self._state_file, state, ensure_ascii=False, indent=2)
            self._last_file_write = now
            self._last_serialized = serialized
        except Exception as exc:
            _api_log.warn(f"runtime api json mirror failed: {exc}")

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._state)

    @property
    def state_url(self) -> str:
        return f"http://{self._host}:{self._port}/state"

    @property
    def state_file(self) -> str:
        return self._state_file

    @property
    def available(self) -> bool:
        return self._running
