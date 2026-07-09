"""Local HTTP stream server for OBS capture — zero external dependencies.

Serves the latest rendered frame as PNG on localhost:8765.
OBS captures it via Browser Source → http://localhost:8765/
"""
import os
import threading
import io
import time
import http.server
from core.logs import get_logger

_log = get_logger("stream")


class _StreamHandler(http.server.BaseHTTPRequestHandler):
    """Serves: / → HTML with auto-refreshing image, /frame → raw PNG bytes."""

    def do_GET(self):
        if self.path.startswith("/frame"):
            self._serve_frame()
        else:
            self._serve_page()

    def _serve_frame(self):
        data = self.server.latest_frame
        if not data:
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _serve_page(self):
        html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>QQM</title>
<style>body{margin:0;padding:0;background:#1a1a2e;overflow:hidden}
img{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
max-width:100vw;max-height:100vh}
#status{position:fixed;bottom:0;left:0;color:#888;font:12px monospace;padding:4px}</style>
</head><body>
<img id="f" src="/frame" onload="document.getElementById('status').textContent='OK '+new Date().toLocaleTimeString()" onerror="this.src='/frame?'+Math.random()">
<div id="status">loading...</div>
<script>setInterval(function(){var i=document.getElementById('f');
i.src='/frame?'+Date.now()},100);</script></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, *args):
        pass


class StreamServer:
    """Lightweight HTTP server + file writer for OBS capture."""

    def __init__(self, port: int = 8765):
        self._port = port
        self._server = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self.latest_frame: bytes | None = None
        self._file_path = os.path.join(os.environ.get("TEMP", "."), "qqoverlay_stream.png")

    def start(self) -> bool:
        if self._running:
            return True
        # Always write to temp file — OBS Image Source can read this
        try:
            open(self._file_path, "wb").close()
        except Exception:
            pass
        try:
            self._server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", self._port), _StreamHandler)
            self._server.latest_frame = None
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            self._running = True
            _log.info(f"stream server started on http://127.0.0.1:{self._port}/")
            _log.info(f"stream file: {self._file_path}")
            return True
        except OSError as e:
            _log.warn(f"stream server failed to start: {e}")
            return False

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
        self._running = False

    def update_frame(self, img_bytes: bytes) -> None:
        with self._lock:
            if self._server:
                self._server.latest_frame = img_bytes
            # Write to temp file for OBS Image Source
            try:
                with open(self._file_path, "wb") as f:
                    f.write(img_bytes)
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._running

    @property
    def file_path(self) -> str:
        return self._file_path
