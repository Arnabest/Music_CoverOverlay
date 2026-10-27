"""SMTC Engine — isolated background polling for Windows System Media Transport Controls.

Runs on its own daemon thread with its own timer. Completely independent of
the render loop, animation system, or UI. No external code changes can affect
SMTC polling, duration detection, or cover thumbnail retrieval.

Usage:
    from core.smtc_engine import SmtcEngine

    # On main thread, before starting:
    SmtcEngine.init_manager_on_main_thread()

    # Create and start:
    smtc = SmtcEngine(poll_interval=0.5)
    smtc.start()

    # Read current state (thread-safe):
    pos = smtc.position
    dur = smtc.duration
    thumb = smtc.thumbnail

    # Stop on shutdown:
    smtc.stop()
"""
import os, threading, io, time
from typing import Optional
from PIL import Image
from core.logs import get_logger

_smtc_logger = get_logger("smtc")


def _smtc_log(msg: str) -> None:
    """Backward-compat wrapper — use _smtc_logger.info() directly in new code."""
    _smtc_logger.info(msg)


# ============================================================
# Recognized players and browser blacklist
# ============================================================

_RECOGNIZED_PLAYERS = (
    "qqmusic",
    "netease",
    "cloudmusic",
    "spotify",
    "apple music",
    "tidal",
)

_BROWSERS = (
    "chrome", "edge", "msedge", "firefox",
    "internet explorer", "microsoft edge",
    "opera", "brave", "vivaldi",
)


# ============================================================
# SmtcEngine
# ============================================================

class SmtcEngine:
    """Self-contained SMTC polling engine running on its own daemon thread.

    Polls Windows SystemMediaTransportControls every ~500ms on a dedicated
    MTA thread, completely independent of the render loop, UI, or animation.

    Thread safety: all state reads/writes are protected by _lock.
    Thumbnail property returns a copy so callers never hold references
    to mutable internal state.
    """

    # Class-level SMTC manager singleton (initialized on main thread for
    # Python 3.14 WinRT COM GIL safety).
    _mgr = None  # type: ignore

    def __init__(self, poll_interval: float = 0.5):
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._state: dict = {
            "pos": 0.0,
            "dur": 0.0,
            "media_dur": 0.0,
            "thumb": None,
            "album_title": "",
            "playback_status": "",
            "title": "",
            "artist": "",
            "player": "",
            "timeline_updated_at": 0.0,
        }
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._force_poll = threading.Event()
        # Per-instance poll diagnostics
        self._last_dur_logged: dict = {}
        self._tl_zero_logged = True
        self._tl_err_last = ""
        self._last_thumb_title = ""
        self._last_player = ""
        self._was_none = False
        self._boost_until = 0.0
        # Detection priority: ordered list of player names
        self._priority: list[str] = ["qqmusic", "netease"]
        # ★ Cache media_dur failures per player — skip WinRT call if
        # known to fail (e.g., QQ Music never exposes duration).
        # Reduces hundreds of futile WinRT property gets per hour.
        self._media_dur_failed: dict[str, float] = {}
        # ★ Per-session change-event subscriptions (timeline/media/playback).
        # Events wake the polling thread immediately instead of waiting up
        # to poll_interval for the next blind tick.
        self._tl_token = None
        self._media_token = None
        self._pb_token = None
        self._subscribed_session = None

    def set_priority(self, priority: list[str]) -> None:
        """Set the detection priority order. First = highest priority.

        Args:
            priority: e.g. ["qqmusic", "netease"] or ["netease", "qqmusic"]
        """
        self._priority = list(priority)

    # ================================================================
    # Lifecycle
    # ================================================================

    @staticmethod
    def init_manager_on_main_thread():
        """Pre-initialize the WinRT SMTC manager on the calling thread.

        MUST be called from the main thread before start(). On Python 3.14,
        WinRT COM initialization on thread-pool threads causes GIL crashes.
        Creating the manager on the main thread avoids this.
        """
        if SmtcEngine._mgr is not None:
            return
        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as SM)
            SmtcEngine._mgr = SM.request_async().get()
            _smtc_log("SMTC manager initialized on main thread")
        except Exception as e:
            _smtc_log(f"SMTC manager init FAILED: {e}")

    def start(self) -> None:
        """Start the background polling daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True,
                                        name="smtc-engine")
        self._thread.start()
        _smtc_log("SmtcEngine started")

    def stop(self, timeout: float = 0.5) -> None:
        """Signal the polling thread to stop and wait for it to join."""
        self._running = False
        self._force_poll.set()  # wake the thread if sleeping
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)  # polls every 0.5s, wakes immediately
        _smtc_log("SmtcEngine stopped")

    def force_poll(self) -> None:
        """Request an immediate poll (wakes the polling thread).

        Also enables boost mode: polls at 100ms for the next 3 cycles
        to capture song-change data quickly.
        """
        self._boost_until = time.time() + 2.0  # boost for 2 seconds
        self._force_poll.set()

    # ================================================================
    # Read-only properties (thread-safe)
    # ================================================================

    @property
    def position(self) -> float:
        with self._lock:
            return self._state["pos"]

    @property
    def duration(self) -> float:
        """SMTC timeline end_time duration."""
        with self._lock:
            return self._state["dur"]

    @property
    def media_duration(self) -> float:
        """SMTC media_props.duration (independent of timeline)."""
        with self._lock:
            return self._state["media_dur"]

    @property
    def thumbnail(self) -> Optional[Image.Image]:
        """SMTC thumbnail as PIL Image, or None. Returns a copy for safety."""
        with self._lock:
            t = self._state["thumb"]
            return t.copy() if t is not None else None

    @property
    def title(self) -> str:
        with self._lock:
            return self._state["title"]

    @property
    def artist(self) -> str:
        with self._lock:
            return self._state["artist"]

    @property
    def album_title(self) -> str:
        with self._lock:
            return self._state["album_title"]

    @property
    def playback_status(self) -> str:
        with self._lock:
            return self._state["playback_status"]

    @property
    def player(self) -> str:
        with self._lock:
            return self._state["player"]

    @property
    def timeline_updated_at(self) -> float:
        """Epoch seconds of the player's last SMTC timeline update.

        0.0 when unknown or implausible (clock skew).  Consumers use the
        snapshot age (now − this) to extrapolate the true playback position
        instead of treating the polled position as current.
        """
        with self._lock:
            return self._state["timeline_updated_at"]

    @property
    def player_has_timeline(self) -> bool:
        """Whether the current SMTC player provides timeline position/duration.

        NetEase Cloud Music does NOT expose timeline data through SMTC.
        QQ Music does. Returns True if player is unknown (assume capable).
        """
        p = self.player
        if p == "netease":
            return False
        # qqmusic and unknown players are assumed to provide timeline
        return True

    def get_state(self) -> dict:
        """Return a safe snapshot of all SMTC data (copies thumbnail)."""
        with self._lock:
            s = dict(self._state)
            t = s.get("thumb")
            s["thumb"] = t.copy() if t is not None else None
            return s

    # ================================================================
    # Polling loop (runs on daemon thread)
    # ================================================================

    def _poll_loop(self) -> None:
        """Main polling loop — runs on the daemon thread."""
        import ctypes as _ct
        # ★ COINIT_MULTITHREADED (0x0). If thread is already STA (0x2),
        # this returns RPC_E_CHANGED_MODE (0x80010106) — we log and
        # continue. WinRT will try RoInitialize internally.
        _hr = _ct.windll.ole32.CoInitializeEx(0, 0x0)
        if _hr == 0:
            _smtc_log("COM MTA initialized on daemon thread")
        elif _hr & 0xFFFFFFFF == 0x80010106:  # RPC_E_CHANGED_MODE
            _smtc_log("COM already initialized on daemon thread (not MTA) "
                     f"— continuing, HRESULT=0x{_hr&0xFFFFFFFF:08X}")

        self._session_change_token = None
        _poll_count = 0
        while self._running:
            try:
                self._poll_once()
                _poll_count += 1

                # Subscribe to session change events after first successful poll
                if (_poll_count == 1 and self._session_change_token is None
                        and SmtcEngine._mgr is not None):
                    try:
                        self._session_change_token = \
                            SmtcEngine._mgr.add_current_session_changed(
                                self._on_session_changed)
                        _smtc_log("subscribed to SMTC session change events")
                    except Exception as e:
                        _smtc_log(f"session change subscription failed: {e}")

                # Heartbeat pulse every ~60s
                if _poll_count % 120 == 1:
                    _smtc_log(f"engine pulse #{_poll_count}: alive, "
                             f"pos={self._state['pos']:.1f}s "
                             f"title={self._state.get('title','')!r}")
            except Exception as e:
                _smtc_log(f"poll_loop error: {e}")
            # ★ Boost mode: 100ms polling for 2s after force_poll()
            _interval = 0.1 if time.time() < self._boost_until else self._poll_interval
            self._force_poll.wait(_interval)
            self._force_poll.clear()

        # Cleanup event subscriptions
        if self._session_change_token is not None:
            try:
                mgr = SmtcEngine._mgr
                if mgr is not None:
                    mgr.remove_current_session_changed(self._session_change_token)
            except Exception:
                pass
        self._unsubscribe_session_events()

    def _on_session_changed(self, sender, args) -> None:
        """Callback: Windows SMTC current session changed.

        Fires when user switches from QQ Music → Chrome or vice versa.
        Wakes the polling thread immediately to capture the new session data.
        """
        try:
            cur = sender.get_current_session() if hasattr(sender, 'get_current_session') else None
            if cur is None and SmtcEngine._mgr is not None:
                cur = SmtcEngine._mgr.get_current_session()
            aid = cur.source_app_user_model_id if cur else "None"
            _smtc_log(f"session changed → {aid}")
        except Exception as e:
            _smtc_logger.warn(f"session changed callback error: {e}")
        # Wake polling thread for immediate re-poll
        self._force_poll.set()

    def _subscribe_session_events(self, session) -> None:
        """Subscribe to the current session's change events (idempotent).

        Timeline/media/playback change events wake the polling thread so
        seeks, song changes and pause transitions are seen immediately
        instead of up to poll_interval later.
        """
        if session is self._subscribed_session:
            return
        self._unsubscribe_session_events()
        try:
            self._tl_token = session.add_timeline_properties_changed(self._on_session_event)
            self._media_token = session.add_media_properties_changed(self._on_session_event)
            self._pb_token = session.add_playback_info_changed(self._on_session_event)
            self._subscribed_session = session
            _smtc_log("subscribed to session events (timeline/media/playback)")
        except Exception as e:
            _smtc_log(f"session event subscription failed: {e}")

    def _unsubscribe_session_events(self) -> None:
        try:
            s = self._subscribed_session
            if s is not None:
                if self._tl_token is not None:
                    s.remove_timeline_properties_changed(self._tl_token)
                if self._media_token is not None:
                    s.remove_media_properties_changed(self._media_token)
                if self._pb_token is not None:
                    s.remove_playback_info_changed(self._pb_token)
        except Exception:
            pass
        self._tl_token = self._media_token = self._pb_token = None
        self._subscribed_session = None

    def _on_session_event(self, sender, args) -> None:
        """WinRT threadpool callback: any session change → wake poll loop."""
        try:
            self._force_poll.set()
        except Exception:
            pass

    def _poll_once(self) -> None:
        """Single SMTC poll — all WinRT interaction isolated here.

        Only this method (running on the daemon thread) touches WinRT.
        The rest of the application only reads thread-safe properties.
        """
        mgr = SmtcEngine._mgr
        if mgr is None:
            # Lazy init on daemon thread as fallback.
            # ★ CoInitializeEx(0,0x0)=MTA was called at _poll_loop entry,
            # but may have failed if thread was already STA. Try with
            # explicit WinRT RoInitialize.
            try:
                import ctypes as _ct
                from winrt.windows.media.control import (
                    GlobalSystemMediaTransportControlsSessionManager as SM)
                SmtcEngine._mgr = SM.request_async().get()
                mgr = SmtcEngine._mgr
                _smtc_log("SMTC manager initialized on daemon thread (fallback)")
            except Exception as e:
                _smtc_log(f"SMTC lazy init FAILED: {e}")
                return
        if mgr is None:
            return

        session, player = self._find_session(mgr)
        if session is None:
            # ★ Clear position when no recognized SMTC session — prevents stale
            # values from being used when another app (browser, Spotify) takes
            # over the SMTC focus.
            with self._lock:
                self._state["pos"] = 0.0
                self._state["playback_status"] = "NO_SESSION"
            self._unsubscribe_session_events()

            # Rate-limited diagnostic — check ALL recognized players
            _diag = getattr(self, '_no_session_count', 0) + 1
            self._no_session_count = _diag
            if _diag <= 1 or _diag % 120 == 0:  # first + every ~60s
                try:
                    from core.window_detect import _is_qqmusic_running, _is_netease_running
                    _qq = _is_qqmusic_running()
                    _ne = _is_netease_running()
                except Exception:
                    _qq = "unknown"
                    _ne = "unknown"
                _running = []
                if _qq is True:
                    _running.append("QQMusic")
                if _ne is True:
                    _running.append("NetEase")
                if _running:
                    _all_sessions = []
                    try:
                        for s in mgr.get_sessions():
                            _all_sessions.append(s.source_app_user_model_id or "?")
                    except Exception:
                        _all_sessions = ["<enumeration error>"]
                    _smtc_log(f"{', '.join(_running)} IS running but SMTC session "
                             f"not found ({_diag} polls) — "
                             f"all sessions: {_all_sessions}")
                else:
                    _smtc_log(f"No recognized music player running "
                             f"({_diag} polls) — start QQ Music or NetEase")
            return

        # ★ Session recovered — log reconnection
        if getattr(self, '_no_session_count', 0) > 0:
            _player_label = {"qqmusic": "QQ Music", "netease": "NetEase"}.get(
                player, player or "unknown")
            _smtc_log(f"{_player_label} SMTC session RECOVERED after "
                     f"{self._no_session_count} polls — resuming tracking")
        self._no_session_count = 0
        self._subscribe_session_events(session)

        # Playback info
        try:
            pi = session.get_playback_info()
            ps = pi.playback_status if pi else None
            pst = str(ps.name) if ps is not None else ""
        except Exception:
            pst = ""

        # Timeline properties
        tl_updated = 0.0
        try:
            props = session.get_timeline_properties()
            pos = props.position.total_seconds() if props.position else 0.0
            dur = props.end_time.total_seconds() if props.end_time else 0.0
            # ★ Snapshot timestamp — the position is the value at the player's
            # LAST timeline update, not "now".  Consumers extrapolate with the
            # snapshot age; reject implausible values (clock skew).
            try:
                _dt = props.last_updated_time
                if _dt is not None:
                    if _dt.tzinfo is None:
                        import datetime as _dmod
                        _dt = _dt.replace(tzinfo=_dmod.timezone.utc)
                    _age = time.time() - _dt.timestamp()
                    if -5.0 <= _age <= 3600.0:
                        tl_updated = _dt.timestamp()
            except Exception:
                tl_updated = 0.0
            if self._tl_zero_logged and dur < 1 and pos < 1:
                self._tl_zero_logged = False
                _smtc_log(f"timeline props exist but pos={pos:.1f}s dur={dur:.1f}s "
                         f"— SMTC not providing playback position")
        except Exception as e:
            pos, dur = 0.0, 0.0
            err_str = str(e)[:120]
            if err_str != self._tl_err_last:
                self._tl_err_last = err_str
                _smtc_log(f"get_timeline_properties EXCEPTION: {e}")

        # Media properties (title, artist, album, thumbnail, duration)
        try:
            media_props = session.try_get_media_properties_async().get()
            if media_props:
                title = str(media_props.title or "")
                artist = str(media_props.artist or "")
                with self._lock:
                    self._state["album_title"] = str(media_props.album_title or "")
                    self._state["title"] = title
                    self._state["artist"] = artist
                    # ★ Skip duration poll if known to fail for this player.
                    # QQ Music never exposes media_props.duration — the
                    # WinRT property get throws AttributeError every time.
                    # Caching this avoids hundreds of futile calls/hour.
                    _last_fail = self._media_dur_failed.get(player, 0)
                    if time.time() - _last_fail > 300:  # retry every 5 min
                        try:
                            md = media_props.duration
                            if md is not None:
                                self._state["media_dur"] = md.total_seconds()
                                self._media_dur_failed.pop(player, None)
                        except Exception as e:
                            self._media_dur_failed[player] = time.time()
                            _smtc_logger.rate_limited(
                                "media_dur_error", 300, "warn",
                                f"media_props.duration unavailable "
                                f"(player={player!r}, title={title[:30]!r}): {e}")

                # Thumbnail
                if media_props.thumbnail:
                    try:
                        stream = media_props.thumbnail.open_read_async().get()
                        if stream and stream.size > 0:
                            from winrt.windows.storage.streams import DataReader
                            reader = DataReader(stream.get_input_stream_at(0))
                            reader.load_async(stream.size).get()
                            buffer = bytearray(stream.size)
                            reader.read_bytes(buffer)
                            reader.close()
                            stream.close()
                            with self._lock:
                                self._state["thumb"] = Image.open(
                                    io.BytesIO(bytes(buffer))).convert("RGBA")
                            if title != self._last_thumb_title:
                                self._last_thumb_title = title
                                _smtc_log(f"thumbnail OK "
                                         f"size={self._state['thumb'].size} "
                                         f"title={title!r} artist={artist!r}")
                        else:
                            with self._lock:
                                self._state["thumb"] = None
                    except Exception as e:
                        _smtc_log(f"thumbnail READ ERROR: {e}")
                        with self._lock:
                            self._state["thumb"] = None
                else:
                    with self._lock:
                        self._state["thumb"] = None
            else:
                _smtc_log("media_props is None")
        except Exception as e:
            _smtc_log(f"media_props error: {e}")

        # Update position/duration/status
        with self._lock:
            self._state.update({
                "pos": pos, "dur": dur,
                "playback_status": pst, "player": player,
                "timeline_updated_at": tl_updated,
            })

        # ★ Snapshot-age diagnostic — reveals the player's timeline update
        # cadence and validates the extrapolation input in the logs.
        if tl_updated > 0 and pst == "PLAYING":
            _age = time.time() - tl_updated
            _smtc_logger.rate_limited(
                "tl_age", 60.0, "INFO",
                f"timeline snapshot age={_age:.2f}s (pos={pos:.1f}s)")

        # Duration change diagnostic
        player_key = player or "__none__"
        key = (int(dur), int(self._state.get("media_dur", 0)))
        prev = self._last_dur_logged.get(player_key)
        if key != prev:
            self._last_dur_logged[player_key] = key
            _smtc_log(f"durations: timeline_dur={dur:.0f}s "
                     f"media_dur={self._state.get('media_dur', 0):.0f}s "
                     f"pos={pos:.1f}s status={pst} "
                     f"title={self._state.get('title', '')!r}")

    # ================================================================
    # Session discovery
    # ================================================================

    def _find_session(self, mgr):
        """Find recognized music player SMTC session by priority order.

        Pass 1: enumerate all sessions via get_sessions(), group by player.
        Pass 2: fallback to get_current_session().

        Uses self._priority to determine which player to prefer
        when multiple sessions are available simultaneously.

        Returns (session, player_name) or (None, None).
        Player name is one of: "qqmusic", "netease".
        """
        # Pass 1: enumerate all sessions, collect by player
        sessions = []
        found: dict[str, object] = {}
        try:
            sessions = list(mgr.get_sessions())
            for s in sessions:
                aid = (s.source_app_user_model_id or "").lower()
                if any(b in aid for b in _BROWSERS):
                    continue
                if "qqmusic" in aid:
                    found.setdefault("qqmusic", s)
                elif "netease" in aid or "cloudmusic" in aid:
                    found.setdefault("netease", s)
        except ImportError:
            if not getattr(self, '_warned_collections', False):
                self._warned_collections = True
                _smtc_log("get_sessions() unavailable — falling back to current_session")
        except Exception as e:
            _smtc_logger.warn(f"get_sessions() enumeration error: {e}")

        # ★ Try players in priority order (user-configured)
        for player in self._priority:
            s = found.get(player)
            if s is not None:
                if player != self._last_player:
                    self._last_player = player
                    _smtc_log(f"found {player} session (priority={self._priority})")
                return s, player

        # Log non-recognized SMTC sessions
        _foreign_names = []
        for s in sessions:
            aid = (s.source_app_user_model_id or "").lower()
            if ("qqmusic" not in aid and "netease" not in aid
                    and "cloudmusic" not in aid
                    and not any(b in aid for b in _BROWSERS)):
                name = aid.split(".")[-1] if "." in aid else aid
                if name:
                    _foreign_names.append(name)
        if _foreign_names:
            _names_str = ", ".join(set(_foreign_names))
            if _names_str != getattr(self, '_last_foreign_names', ""):
                self._last_foreign_names = _names_str
                _smtc_log(f"non-recognized SMTC sessions: {_names_str}")

        # Pass 2: try current_session as fallback
        try:
            cur = mgr.get_current_session()
            if cur is not None:
                aid = (cur.source_app_user_model_id or "").lower()
                for player in self._priority:
                    if player == "qqmusic" and "qqmusic" in aid:
                        return cur, "qqmusic"
                    if player == "netease" and ("netease" in aid or "cloudmusic" in aid):
                        return cur, "netease"
        except Exception as e:
            _smtc_logger.warn(f"get_current_session() fallback error: {e}")

        return None, None
