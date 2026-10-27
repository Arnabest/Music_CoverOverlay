"""Playback State Detector — comprehensive music ecosystem state machine.

Detects and tracks the complete state of the music playback ecosystem,
handling all permutations of:
  - App startup order (overlay first, player first, simultaneous)
  - Play/pause/stop transitions
  - Song changes
  - SMTC session contention (Chrome stealing focus)
  - Player window presence/absence

Architecture:
  Sources → Detector → State + Transition events
  Sources: SMTC engine, Reader, Window detection
  State:   One of 8 MusicEcoState values
  Events:  State transition callbacks for consumers to react

Design Requirements (from 18 bugs):
  A. Guard release is explicit, not status-based
  B. Wall-clock needs proof of SMTC liveness
  F. SMTC position threshold must filter residuals (0.5s)
  L. SMTC focus loss defense in depth
  M. Status string normalization (PascalCase → UPPER)
"""
import time
from enum import Enum, auto
from typing import Optional, Callable


# ═══════════════════════════════════════════════════════════════════
# State enum
# ═══════════════════════════════════════════════════════════════════

class MusicEcoState(Enum):
    """Complete music ecosystem state.

    These 8 states cover ALL observed permutations of:
    - Player process (running/not)
    - Player window (present/not)
    - SMTC session (absent/opened/playing/paused)
    - Position data (advancing/stuck/none)
    """
    NO_PLAYER = auto()         # Music player not running
    PLAYER_STARTING = auto()   # Player process found, window/SMTC not yet ready
    SONG_OPENED = auto()       # Song loaded but not playing (SMTC: Opened)
    PLAYING_LIVE = auto()      # Playing with SMTC position advancing
    PLAYING_STUCK = auto()     # Playing but SMTC position frozen (liveness check fired)
    PAUSED_LIVE = auto()       # Paused, SMTC reporting position correctly
    SONG_CHANGING = auto()     # Between songs (SMTC may report old or transitional data)
    SESSION_LOST = auto()      # SMTC session temporarily lost (Chrome/other app stole it)


# ═══════════════════════════════════════════════════════════════════
# Transition reason (for logging/diagnostics)
# ═══════════════════════════════════════════════════════════════════

class TransitionReason(Enum):
    PLAYER_STARTED = auto()       # Music player process detected
    PLAYER_CLOSED = auto()        # Music player process gone
    WINDOW_READY = auto()         # Player window found
    SMTC_OPENED = auto()          # SMTC session: Opened
    SMTC_PLAYING = auto()         # SMTC session: Playing
    SMTC_PAUSED = auto()          # SMTC session: Paused
    SMTC_LOST = auto()            # SMTC session disappeared
    SMTC_RECOVERED = auto()       # SMTC session returned
    POSITION_ADVANCING = auto()   # SMTC position confirmed advancing
    POSITION_STUCK = auto()       # SMTC position frozen >N seconds
    SONG_STARTED = auto()         # New song detected
    SONG_ENDED = auto()           # Song ended / changing


# ═══════════════════════════════════════════════════════════════════
# Detector
# ═══════════════════════════════════════════════════════════════════

class PlaybackStateDetector:
    """Detects music playback ecosystem state from multiple sources.

    Usage:
        detector = PlaybackStateDetector()
        detector.set_on_transition(lambda old, new, reason: ...)

        # Called every frame:
        detector.update(
            smtc_status=smtc.playback_status,
            smtc_pos=smtc.position,
            smtc_player=smtc.player,
            is_player_running=reader.is_player_running(),
            is_window_found=reader.window_found,
            song_name=reader.song_name,
            is_smtc_advancing=progress._smtc_live,
            is_smtc_stuck=progress._smtc_stuck,
        )
        state = detector.state
    """

    def __init__(self):
        self.state: MusicEcoState = MusicEcoState.NO_PLAYER
        self._prev_state: MusicEcoState = MusicEcoState.NO_PLAYER
        self._state_entered: float = time.time()  # when current state was entered
        self._transition_count: int = 0

        # Hysteresis: don't flap between states
        self._same_state_frames: int = 0
        self._pending_state: Optional[MusicEcoState] = None
        self._pending_reason: Optional[TransitionReason] = None
        self._hysteresis_frames: int = 3  # confirm state for N frames before transition

        # Callbacks
        self._on_transition: Optional[Callable] = None

        # Song tracking
        self._last_song: str = ""
        self._song_stable_frames: int = 0

        # SMTC tracking (internal — no dependency on progress.py)
        self._last_smtc_pos: float = -1.0
        self._smtc_advancing: bool = False
        # ★ Time-based stuck/absence thresholds (frame counts scaled with fps,
        # which made "10s" become 5s at 120 fps).
        self._smtc_last_move_time: float = time.time()
        self._smtc_absent_since: float = 0.0
        self._smtc_stuck_after_s: float = 10.0    # SMTC frozen → PLAYING_STUCK
        self._smtc_absent_after_s: float = 0.5    # no status → SESSION_LOST

        # Player tracking
        self._player_was_running: bool = False

        # Diagnostic
        self._diag_logged: int = 0

    # ── Public API ────────────────────────────────────────────────

    def set_on_transition(self, callback: Callable[[MusicEcoState, MusicEcoState, TransitionReason], None]):
        """Register a callback for state transitions: fn(old_state, new_state, reason)."""
        self._on_transition = callback

    @property
    def time_in_state(self) -> float:
        """Seconds spent in current state."""
        return time.time() - self._state_entered

    @property
    def is_playing(self) -> bool:
        """True if music is actively playing (live or recovering)."""
        return self.state in (MusicEcoState.PLAYING_LIVE, MusicEcoState.PLAYING_STUCK)

    @property
    def is_playback_active(self) -> bool:
        """True if any playback-related state (song is loaded or playing)."""
        return self.state in (
            MusicEcoState.SONG_OPENED, MusicEcoState.PLAYING_LIVE,
            MusicEcoState.PLAYING_STUCK, MusicEcoState.PAUSED_LIVE,
            MusicEcoState.SONG_CHANGING,
        )

    def update(self, *,
               smtc_status: str = "",
               smtc_pos: float = 0.0,
               smtc_player: str = "",
               is_player_running: bool = False,
               song_name: str = "",
               ) -> MusicEcoState:
        """Evaluate current ecosystem state from available sources.

        Called every frame (~60fps). Returns the current state.
        Uses only public APIs — no dependency on progress.py internals.
        """
        status = (smtc_status or "").upper()
        now = time.time()

        # Track SMTC position advancement internally (time-based)
        if abs(smtc_pos - self._last_smtc_pos) > 0.3:
            self._smtc_advancing = True
            self._smtc_last_move_time = now
        self._last_smtc_pos = smtc_pos
        is_advancing = self._smtc_advancing
        is_stuck = (smtc_pos > 0
                    and now - self._smtc_last_move_time > self._smtc_stuck_after_s)

        candidate = self._evaluate(
            status=status,
            smtc_pos=smtc_pos,
            is_running=is_player_running,
            song_name=song_name,
            is_advancing=is_advancing,
            is_stuck=is_stuck,
        )
        reason = self._last_eval_reason

        # Hysteresis: require N consecutive frames before committing
        if candidate == self._pending_state:
            self._same_state_frames += 1
        else:
            self._pending_state = candidate
            self._pending_reason = reason
            self._same_state_frames = 1

        if self._same_state_frames >= self._hysteresis_frames:
            if self._pending_state != self.state:
                self._transition(self._pending_state, self._pending_reason)
            self._same_state_frames = 0
            self._pending_state = None

        # Track song stability
        if song_name and song_name == self._last_song:
            self._song_stable_frames += 1
        elif song_name:
            self._last_song = song_name
            self._song_stable_frames = 1

        # Track SMTC absence (time-based)
        if status in ("", "NO_SESSION"):
            if self._smtc_absent_since == 0.0:
                self._smtc_absent_since = now
        else:
            self._smtc_absent_since = 0.0

        return self.state

    # ── Internal evaluation ──────────────────────────────────────

    def _evaluate(self, **s) -> MusicEcoState:
        """Single-frame evaluation of ecosystem state."""
        status = s["status"]
        is_running = s["is_running"]
        is_advancing = s["is_advancing"]
        is_stuck = s["is_stuck"]
        song = s["song_name"]

        # ── Layer 1: Player presence ──
        if not is_running:
            self._last_eval_reason = TransitionReason.PLAYER_CLOSED
            return MusicEcoState.NO_PLAYER

        # ── Layer 2: SMTC session state ──
        if status in ("", "NO_SESSION"):
            if self._smtc_absent_since != 0.0 and (
                    time.time() - self._smtc_absent_since > self._smtc_absent_after_s):
                self._last_eval_reason = TransitionReason.SMTC_LOST
                return MusicEcoState.SESSION_LOST
            self._last_eval_reason = TransitionReason.SMTC_LOST
            return self.state

        if status == "OPENED":
            self._last_eval_reason = TransitionReason.SMTC_OPENED
            return MusicEcoState.SONG_OPENED

        # ── Layer 3: Playback state ──
        if status == "PAUSED":
            self._last_eval_reason = TransitionReason.SMTC_PAUSED
            return MusicEcoState.PAUSED_LIVE

        if status == "PLAYING":
            if song and self._song_stable_frames < 5:
                self._last_eval_reason = TransitionReason.SONG_STARTED
                return MusicEcoState.SONG_CHANGING
            if is_stuck:
                self._last_eval_reason = TransitionReason.POSITION_STUCK
                return MusicEcoState.PLAYING_STUCK
            self._last_eval_reason = TransitionReason.SMTC_PLAYING
            return MusicEcoState.PLAYING_LIVE

        self._last_eval_reason = TransitionReason.PLAYER_STARTED
        return MusicEcoState.PLAYER_STARTING

    # ── Transition handling ──────────────────────────────────────

    def _transition(self, new_state: MusicEcoState, reason: TransitionReason):
        old = self.state
        self._prev_state = old
        self.state = new_state
        self._state_entered = time.time()
        self._transition_count += 1

        # Diagnostic log (first 20 transitions)
        if self._diag_logged < 20:
            self._diag_logged += 1
            try:
                from core.logs import get_logger
                get_logger("render").info(
                    f"ECO_STATE #{self._diag_logged}: "
                    f"{old.name} → {new_state.name} "
                    f"reason={reason.name} "
                    f"elapsed={self.time_in_state:.1f}s")
            except Exception:
                pass

        # Notify callback
        if self._on_transition:
            try:
                self._on_transition(old, new_state, reason)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════
# Startup order detection
# ═══════════════════════════════════════════════════════════════════

class StartupScenario(Enum):
    """Which app started first?"""
    UNKNOWN = auto()
    OVERLAY_FIRST = auto()       # Overlay started before music player
    PLAYER_FIRST = auto()        # Music player was already running
    SIMULTANEOUS = auto()        # Both started at same time (<2s apart)


def detect_startup_scenario(
        overlay_start_time: float,
        player_first_seen_time: Optional[float] = None,
) -> StartupScenario:
    """Detect the startup order scenario.

    Args:
        overlay_start_time: time.time() when overlay was launched
        player_first_seen_time: time.time() when player was first detected

    Returns:
        StartupScenario indicating startup order
    """
    if player_first_seen_time is None:
        return StartupScenario.OVERLAY_FIRST

    delta = abs(overlay_start_time - player_first_seen_time)

    if delta < 2.0:
        return StartupScenario.SIMULTANEOUS

    if player_first_seen_time < overlay_start_time:
        return StartupScenario.PLAYER_FIRST

    return StartupScenario.OVERLAY_FIRST


# ═══════════════════════════════════════════════════════════════════
# Preloaded scenario characteristics
# ═══════════════════════════════════════════════════════════════════

# Known SMTC behavior patterns per scenario
SCENARIO_CHARACTERISTICS = {
    # Overlay starts first, waits for player
    StartupScenario.OVERLAY_FIRST: {
        "expected_smtc_sequence": ["NO_SESSION", "OPENED", "PLAYING"],
        "initial_position": 0.0,
        "guard_delay": "0-3s (waiting for API or s_pos>0.5)",
        "risk": "Guard freeze until SMTC delivers",
    },
    # Player already playing when overlay starts
    StartupScenario.PLAYER_FIRST: {
        "expected_smtc_sequence": ["PLAYING" or "PAUSED"],
        "initial_position": "current playback position",
        "guard_delay": "0s (s_pos already >0.5 or PAUSED)",
        "risk": "Stale prev → false wall-lock on song change",
    },
    # Both start together
    StartupScenario.SIMULTANEOUS: {
        "expected_smtc_sequence": ["variable"],
        "initial_position": "may be 0 or current position",
        "guard_delay": "0-3s (depends on timing)",
        "risk": "Race between SMTC init and first poll",
    },
}

# Play/pause transition characteristics
PLAYBACK_TRANSITIONS = {
    # Playing → Pause
    ("PLAYING_LIVE", "PAUSED_LIVE"): {
        "position_behavior": "frozen at pause point",
        "smTC_behavior": "position may drift slightly",
        "cover_spin": "decelerate to stop",
        "duration_impact": "none (duration already resolved)",
        "bugs_addressed": ["#12"],
    },
    # Pause → Playing (Resume)
    ("PAUSED_LIVE", "PLAYING_LIVE"): {
        "position_behavior": "resume from paused position",
        "smTC_behavior": "may briefly report old position or OPENED",
        "cover_spin": "accelerate from stop",
        "duration_impact": "none",
        "bugs_addressed": ["#166", "#167", "#12"],
    },
    # Playing → Song change → Playing
    ("PLAYING_LIVE", "SONG_CHANGING"): {
        "position_behavior": "frozen at end-of-song position",
        "smTC_behavior": "may carry old song position briefly",
        "cover_spin": "decelerate for fade-out",
        "duration_impact": "reset for new song",
        "bugs_addressed": ["#170", "#140"],
    },
    # Song change → New song playing
    ("SONG_CHANGING", "PLAYING_LIVE"): {
        "position_behavior": "reset to 0 or new song position",
        "smTC_behavior": "position transitions from old→new song",
        "cover_spin": "accelerate for fade-in",
        "duration_impact": "new duration chain invoked",
        "bugs_addressed": ["#170", "#166"],
    },
    # SMTC lost → recovered
    ("SESSION_LOST", "PLAYING_LIVE"): {
        "position_behavior": "wall-clock during gap, sync on recovery",
        "smTC_behavior": "session re-establishment",
        "cover_spin": "may pause briefly",
        "duration_impact": "none",
        "bugs_addressed": ["#171", "#169"],
    },
}
