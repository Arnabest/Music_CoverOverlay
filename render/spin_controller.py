"""Shared cover spin state — used by both PIL Compositor and GL Painter.

Eliminates ~45 lines of duplicated rotation state management across the
two render paths. Both Compositor and Painter delegate to a SpinController
instance for: rotation tracking, song-change deceleration, and pause/resume.

Usage:
    self._spin = SpinController()
    self._spin.reset_rotation()
    self._spin.start_song_end_spin()
    dt = self._spin.update(now, speed, is_paused)  # returns rotation delta
"""


class SpinController:
    """Encapsulates cover rotation animation state.

    Shared between Compositor (PIL) and Painter (GL) — eliminates
    duplicated _cover_rotation, _last_cover_time, _rotation_start_time,
    _song_ending, _song_end_start, _is_paused, and the reset/deceleration
    logic that was copy-pasted verbatim in both files.
    """

    def __init__(self):
        self._cover_rotation: float = 0.0
        self._last_cover_time: float = 0.0
        self._rotation_start_time: float = 0.0
        self._song_ending: bool = False
        self._song_end_start: float | None = None
        self._is_paused: bool = False

    # ================================================================
    # Lifecycle
    # ================================================================

    def reset_rotation(self) -> None:
        """Reset cover spin for new song (smooth acceleration restart)."""
        self._cover_rotation = 0.0
        self._last_cover_time = 0.0
        self._rotation_start_time = 0.0
        self._song_ending = False
        self._song_end_start = None

    def start_song_end_spin(self) -> None:
        """Begin deceleration for song change.

        If already paused (not spinning), skip — cover is already stopped.
        """
        if self._is_paused:
            self._cover_rotation = 0.0
            self._song_ending = False
            return
        self._song_ending = True
        self._song_end_start = None

    # ================================================================
    # Spin update — called every frame by both render paths
    # ================================================================

    def update(self, now: float, speed_dps: float) -> float:
        """Compute rotation delta for this frame.

        Returns the rotation change in degrees for this frame.
        Handles acceleration from 0, deceleration on song end,
        and pause/resume curves.

        Args:
            now: Current time in seconds (monotonic).
            speed_dps: Target spin speed in degrees per second.
        """
        # Paused: no delta
        if self._is_paused:
            return 0.0

        # First frame after reset — seed timing
        if self._last_cover_time <= 0.0:
            self._last_cover_time = now
        if self._rotation_start_time <= 0.0:
            self._rotation_start_time = now

        # Clamp dt to avoid jumps after lag spikes
        dt = min(now - self._last_cover_time, 0.1)
        self._last_cover_time = now

        # Compute spin factor: deceleration curve on song end,
        # acceleration curve on restart, otherwise 1.0 (full speed).
        factor = 1.0
        if self._song_ending:
            if self._song_end_start is None:
                self._song_end_start = now
            elapsed = now - self._song_end_start
            # Quadratic deceleration: 1 → 0 over 2.5s
            duration = 2.5
            if elapsed >= duration:
                factor = 0.0
                self._song_ending = False
            else:
                factor = (1.0 - elapsed / duration) ** 2
        elif self._rotation_start_time > 0.0:
            # Quadratic acceleration: 0 → 1 over 2.5s after reset
            elapsed = now - self._rotation_start_time
            duration = 2.5
            if elapsed >= duration:
                self._rotation_start_time = 0.0  # lock to full speed
            else:
                factor = (elapsed / duration) ** 2

        delta = speed_dps * dt * factor
        self._cover_rotation = (self._cover_rotation + delta) % 360.0
        return delta

    # ================================================================
    # Accessors
    # ================================================================

    @property
    def rotation(self) -> float:
        return self._cover_rotation

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @is_paused.setter
    def is_paused(self, value: bool) -> None:
        self._is_paused = value

    @property
    def is_song_ending(self) -> bool:
        return self._song_ending
