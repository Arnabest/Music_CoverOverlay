"""Progress Tracker — SMTC + wall-clock merge.

Short songs (≤7:09): SMTC covers entire song. Just use s_pos directly.
Long songs (>7:09): SMTC timeline wraps. Wall-clock takes over after cycle wrap.
"""
import time
from typing import Optional


def format_time(seconds: float) -> str:
    if seconds <= 0:
        return "0:00"
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


class ProgressTracker:
    """Tracks playback position by merging SMTC data with wall-clock fallback."""

    def __init__(self):
        self.position: float = 0.0
        self.duration: float = 0.0
        self.progress: float = 0.0
        self.is_paused: bool = False
        self.no_timeline: bool = False

        self._smtc: Optional['SmtcEngine'] = None  # type: ignore

        # Guard: has SMTC delivered valid data?
        self._has_data: bool = False
        # Wall-clock origin
        self._song_start: float = 0.0
        # Previous s_pos (for cycle-wrap detection)
        self._prev: float = -1.0
        # Wall-clock lock (cycle-wrap handling)
        self._locked: bool = False
        self._lock_pos: float = 0.0   # SMTC position at lock
        self._lock_wall: float = 0.0  # wall-clock at lock
        self._lock_at: float = 0.0    # wall-clock timestamp of lock (Bug #168)
        # Pause/resume
        self._was_paused: bool = False
        self._paused_at: float = 0.0
        # Liveness (SMTC stuck detection)
        self._last_change_wall: float = 0.0
        self._last_smtc: float = -1.0
        # ★ Bug #171: fresh-reset guard — suppress record_max_position
        # and cycle-wrap detection until SMTC confirms new song data
        self._fresh_reset: bool = False
        # ★ Bug #181: stuck-position detection — auto-reset when SMTC
        # session bounces (Chrome↔QQMusic) and position freezes for hours
        self._stuck_pos: float = 0.0      # last position value for change detection
        self._stuck_since: float = 0.0     # wall clock when position last changed

    def set_smtc_engine(self, engine) -> None:
        self._smtc = engine

    def reset(self):
        self.position = 0.0
        self.duration = 0.0
        self.progress = 0.0
        self.is_paused = False
        self._has_data = False
        self._song_start = time.time()
        self._prev = -1.0
        self._locked = False
        self._lock_pos = 0.0
        self._lock_wall = 0.0
        self._lock_at = 0.0
        self._was_paused = False
        self._paused_at = 0.0
        self._last_change_wall = 0.0
        self._last_smtc = -1.0
        self._fresh_reset = True  # ★ Bug #171: guard against stale-SMTC pollution
        # ★ Bug #181: reset stuck detection on manual reset
        self._stuck_pos = 0.0
        self._stuck_since = time.time()

    def update(self, reader_duration: float = 0.0, song: str = "", artist: str = ""):
        try:
            smtc = self._smtc
            if smtc is None:
                return

            s_pos = smtc.position
            s_dur = smtc.duration
            m_dur = smtc.media_duration
            status = (smtc.playback_status or "").upper()
            player = smtc.player or ""
            _wall = time.time() - self._song_start

            is_playing = (status == "PLAYING")
            is_paused = (status == "PAUSED")
            is_opened = (status == "OPENED")
            is_no_session = (status in ("", "NO_SESSION"))
            is_netease = (player == "netease")

            self.is_paused = is_paused or is_opened or is_no_session
            if is_opened or is_no_session:
                s_pos = 0.0
            self.no_timeline = (
                is_netease and status in ("PLAYING", "PAUSED")
                and s_pos < 0.5 and s_dur < 1 and m_dur < 1
            )

            # ── Guard release ──────────────────────────────────────
            if not self._has_data:
                if (s_pos > 0.5 or is_paused or is_opened
                        or (is_netease and is_playing)
                        or (reader_duration > 0 and is_playing)):
                    self._has_data = True
                    if s_pos > 0.5:
                        self._last_smtc = s_pos
                        self._last_change_wall = _wall

            if not self._has_data:
                self.position = 0.0
                self._prev = s_pos
                return

            # ── Pause/resume ───────────────────────────────────────
            if is_paused and not self._was_paused:
                self._was_paused = True
                self._paused_at = self.position
            elif not is_paused and self._was_paused:
                self._was_paused = False
                if self._paused_at > 0.0:
                    self._song_start = time.time() - self._paused_at
                    _wall = time.time() - self._song_start
                    self._last_change_wall = _wall
                    self._last_smtc = s_pos if s_pos > 0 else self._paused_at
                self._paused_at = 0.0

            # ── Bug #171 fresh-reset gate ──────────────────────────
            # After progress.reset(), SMTC may still report stale old-song
            # data (s_pos ~250, s_dur ~257) for a few polling cycles.
            # Using those values would:
            #   1. Pollute record_max_position() for the NEW song
            #   2. Trigger false cycle-wrap detection (position drops from
            #      250 → 0 looks like SMTC wrap)
            #   3. Lock the wall clock at the old song's position
            #
            # Fix: hold position at 0 until s_pos confirms the new song
            # by dropping near 0.  No record_max_position, no locking.
            if self._fresh_reset:
                if 0 < s_pos < 5.0:
                    self._fresh_reset = False  # SMTC confirmed new song
                    self._prev = -1.0  # ★ clear stale _prev to avoid false cycle-wrap
                    # Fall through to normal position tracking below
                elif self._has_data and (is_paused or is_opened or is_no_session):
                    self._fresh_reset = False  # guard released via non-position means
                    self._prev = -1.0
                else:
                    # ★ Stale SMTC data from old song or no data yet.
                    # Keep position at 0 — do NOT use potentially stale s_pos.
                    self.position = 0.0
                    self._prev = s_pos
                    # Skip record_max_position, skip duration resolution
                    # (s_dur may be old song's duration → would display wrong total)
                    # Let next frame pick up correct data after SMTC catches up.

            if not self._fresh_reset:
                # ── Position tracking (normal path) ─────────────────
                prev = self._prev

                if is_paused:
                    if self._paused_at > 0:
                        self.position = self._paused_at
                    if s_pos > self.position:
                        self.position = s_pos

                elif is_no_session:
                    pass  # hold position

                elif self._is_short(reader_duration, m_dur, s_dur, song, artist):
                    # ★ Short song: SMTC never wraps. Simple direct tracking.
                    self.position = s_pos if s_pos > 0 else self.position
                    self._song_start = time.time() - max(s_pos, 0)
                    self._prev = s_pos

                else:
                    # ★ Long song: full machinery for cycle-wrap handling
                    smtc_active = s_pos > 0.5

                    if s_pos < 1.0 and _wall > 5:
                        smtc_active = False

                    if smtc_active and s_pos > 0:
                        if abs(s_pos - self._last_smtc) > 0.3:
                            self._last_change_wall = _wall
                            self._last_smtc = s_pos
                        elif self._last_smtc < 0:
                            self._last_change_wall = _wall
                            self._last_smtc = s_pos
                        elif _wall - self._last_change_wall > 10:
                            smtc_active = False

                    if self._locked:
                        self.position = max(0.0, self._lock_pos + (_wall - self._lock_wall))
                        if prev > 0 and abs(s_pos - prev) > 5:
                            if s_pos < 2.0:
                                pass  # cycle recovery
                            elif s_pos < 420:
                                # ★ Bug #168: guard against false unlock
                                if _wall - self._lock_at < 3.0:
                                    pass
                                else:
                                    self._locked = False
                                    self.position = s_pos
                            else:
                                self._lock_pos = s_pos
                                self._lock_wall = _wall
                                self.position = s_pos
                    elif smtc_active:
                        self.position = s_pos
                        self._song_start = time.time() - s_pos
                        if prev > 0 and s_pos < 2.0:
                            self._locked = True
                            self._lock_pos = prev
                            self._lock_wall = _wall
                            self._lock_at = _wall
                    else:
                        self.position = _wall
                        if prev > 0 and s_pos < 2.0 and _wall > 10:
                            self._locked = True
                            self._lock_pos = prev
                            self._lock_wall = _wall
                            self._lock_at = _wall

                    # Prev preservation
                    if prev < 0:
                        self._prev = s_pos
                    elif not (prev > 0 and s_pos < 2.0):
                        self._prev = s_pos

            # ── Record SMTC position ───────────────────────────────
            # ★ Bug #171: skip during fresh_reset to avoid polluting
            # the features DB for the new song with stale old-song data
            if s_pos > 0.5 and song and not self._fresh_reset:
                try:
                    from core.smtc_features import record_max_position
                    record_max_position(song, artist, s_pos)
                except Exception:
                    pass

            # ★ Duration library: record confirmed SMTC media duration
            # when available.  This is media file metadata (not timeline),
            # immune to cycle-wrap and stale-SMTC pollution.
            if m_dur >= 60 and song and not self._fresh_reset:
                try:
                    from core.smtc_features import record_confirmed_duration
                    record_confirmed_duration(song, artist, m_dur)
                except Exception:
                    pass

            # ── Duration ───────────────────────────────────────────
            dur = self._resolve_duration(s_dur, m_dur, reader_duration, song, artist)
            if dur > 0:
                self.duration = dur

            if (self.duration > 0 and self.position > self.duration * 1.05
                    and not self.no_timeline):
                self.duration = max(self.position * 1.15, self.duration + 30)

            if self.duration > 0:
                self.progress = min(1.0, self.position / self.duration)

        except Exception:
            import traceback
            traceback.print_exc()

        # ── Auto-reset stuck position (Bug #181 fix 8) ──────────────
        # When SMTC session bounces between sources (Chrome ↔ QQ Music),
        # the progress tracker can enter a permanent freeze:
        #   - Chrome session:  is_no_session → pass (hold position)
        #   - QQ Music return: is_paused with s_pos≈0 → hold at _paused_at
        # Neither path advances or resets → position frozen for hours.
        #
        # Detection: track wall-clock time since position last changed.
        # If stuck > 5s AND SMTC reports near-zero position while paused
        # or no_session, the song has likely changed — auto-reset.
        _now = time.time()
        if abs(self.position - self._stuck_pos) > 0.5:
            self._stuck_pos = self.position
            self._stuck_since = _now
        elif (self.position > 5.0         # tracking non-trivial position
              and _now - self._stuck_since > 5.0  # stuck > 5 seconds
              and (is_no_session or (is_paused and s_pos < 1.0))):  # SMTC suggests new song
            _reason = "no_session" if is_no_session else f"paused_s_pos={s_pos:.1f}"
            _stuck_sec = _now - self._stuck_since
            # ★ Log via BOTH loggers for reliability
            try:
                from core.logs import get_logger
                get_logger("progress").warning(
                    f"PROG_AUTO_RESET: pos={self.position:.1f}s stuck for "
                    f"{_stuck_sec:.0f}s, {_reason} — resetting")
                get_logger("window").warning(
                    f"PROG_AUTO_RESET: pos={self.position:.1f}s stuck for "
                    f"{_stuck_sec:.0f}s, {_reason}")
            except Exception:
                pass
            self.reset()
            self._stuck_pos = 0.0
            self._stuck_since = _now

    # ── Helpers ──────────────────────────────────────────────────

    def _is_short(self, rdur: float, mdur: float, s_dur: float,
                  song: str, artist: str) -> bool:
        if self._locked or self.position > 420:
            return False
        if song and self._learned(song, artist, 420):
            return False
        # SMTC media duration (WinRT metadata, most reliable when available)
        if mdur >= 420:
            return False
        # SMTC timeline end_time: QQ Music caps at ~429s per cycle.
        # If the timeline itself claims >= 420s, the song is at least that long.
        if s_dur >= 420:
            return False
        # Reader duration (QQ Music window interval, cached)
        if rdur >= 420:
            return False
        return True

    def _resolve_duration(self, s_dur: float, m_dur: float,
                          rdur: float, song: str, artist: str) -> float:
        is_long = not self._is_short(rdur, m_dur, s_dur, song, artist)
        dur = 0.0

        if not is_long:
            for src in [s_dur, self._learned(song, artist, 60), m_dur, rdur]:
                if src >= 60:
                    dur = src
                    break
        else:
            for src in [self._learned(song, artist, 120),
                        self._features(song, artist, 120),
                        m_dur, rdur, s_dur]:
                if src >= 60:
                    dur = src
                    break
            if not dur and s_dur >= 60:
                ref = max(m_dur, rdur)
                if ref >= 60 and ref > s_dur * 1.2:
                    dur = ref
                elif self.position > s_dur * 1.05:
                    dur = ref if ref >= 60 else 0
                elif s_dur < 180 and ref >= 60 and ref > 180:
                    dur = ref
                else:
                    dur = s_dur

        if not dur and not self.no_timeline:
            dur = max(self.position * 1.15, 60) if self.position > 0 else 60
        return dur

    @staticmethod
    def _learned(song: str, artist: str, min_val: int = 0) -> float:
        try:
            from core.duration import get_learned
            v = get_learned(song, artist) or 0
            return v if v >= min_val else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _features(song: str, artist: str, min_val: int = 0) -> float:
        try:
            from core.smtc_features import get_known_duration
            v = get_known_duration(song, artist) or 0
            return v if v >= min_val else 0.0
        except Exception:
            return 0.0
