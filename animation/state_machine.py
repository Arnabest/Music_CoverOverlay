"""Overlay State Machine — centralized animation state management.

Replaces the ad-hoc _frozen / _frozen_snapshot / _visible flag system with
explicit states and guarded transitions. Guarantees return to a valid rendering
state after any animation path completes.

Has zero dependencies on SMTC, reader, render, or UI systems. Only depends on
the animation timeline for playback orchestration.

States:
    HIDDEN         — Window not shown, no data polling
    SHOWING        — Appear animation playing (first show or manual show)
    VISIBLE        — Window shown, normal data polling active
    TRANSITIONING  — Song-change transition (fade_out → load → fade_in)
    HIDING         — Auto-hide or manual hide (fade_out → hide)
    ENDING         — Song ended (fade_out → hide)

Events:
    appear         — Start appear animation (HIDDEN → SHOWING)
    song_change    — Start song-change transition (VISIBLE → TRANSITIONING)
    song_end       — Song ended (VISIBLE → ENDING)
    auto_hide      — Auto-hide timer expired (VISIBLE → HIDING)
    manual_show    — Tray/context menu "Show" (HIDDEN → SHOWING)
    manual_hide    — Tray/context menu "Hide" (any → HIDDEN)
    completed      — Current animation finished (SHOWING|TRANSITIONING|HIDING|ENDING → VISIBLE|HIDDEN)
    error          — Any exception during transition (any → HIDDEN with cleanup)
"""
import time
import enum
from typing import Callable, Optional
from core.logs import get_logger

_asm_log = get_logger("state_machine")


class State(enum.Enum):
    HIDDEN = "hidden"
    SHOWING = "showing"
    VISIBLE = "visible"
    TRANSITIONING = "transitioning"
    HIDING = "hiding"
    ENDING = "ending"


# Valid transitions: (current_state, event) → new_state
_TRANSITIONS = {
    # appear / manual_show
    (State.HIDDEN, "appear"): State.SHOWING,
    (State.HIDDEN, "manual_show"): State.SHOWING,
    # song_change
    (State.VISIBLE, "song_change"): State.TRANSITIONING,
    (State.SHOWING, "song_change"): State.TRANSITIONING,
    (State.HIDING, "song_change"): State.TRANSITIONING,
    (State.ENDING, "song_change"): State.TRANSITIONING,
    (State.TRANSITIONING, "song_change"): State.TRANSITIONING,  # re-enter
    # song_end
    (State.VISIBLE, "song_end"): State.ENDING,
    # auto_hide
    (State.VISIBLE, "auto_hide"): State.HIDING,
    # manual_hide
    (State.VISIBLE, "manual_hide"): State.HIDDEN,
    (State.SHOWING, "manual_hide"): State.HIDDEN,
    (State.TRANSITIONING, "manual_hide"): State.HIDDEN,
    (State.HIDING, "manual_hide"): State.HIDDEN,
    (State.ENDING, "manual_hide"): State.HIDDEN,
    # completed
    (State.SHOWING, "completed"): State.VISIBLE,
    (State.TRANSITIONING, "completed"): State.VISIBLE,
    (State.HIDING, "completed"): State.HIDDEN,
    (State.ENDING, "completed"): State.HIDDEN,
    # error (any → HIDDEN)
    (State.VISIBLE, "error"): State.HIDDEN,
    (State.SHOWING, "error"): State.HIDDEN,
    (State.TRANSITIONING, "error"): State.HIDDEN,
    (State.HIDING, "error"): State.HIDDEN,
    (State.ENDING, "error"): State.HIDDEN,
}

# States where data polling (reader, progress) should be skipped
_TRANSITIONING_STATES = frozenset({
    State.SHOWING, State.TRANSITIONING, State.HIDING, State.ENDING,
})


class OverlayStateMachine:
    """Centralized animation state for the overlay window.

    Usage:
        sm = OverlayStateMachine()

        # Register callbacks
        sm.on_enter(State.VISIBLE, lambda: set_timer())
        sm.on_exit(State.VISIBLE, lambda: clear_timer())

        # Trigger transitions
        sm.transition("appear")         # HIDDEN → SHOWING
        sm.transition("completed")      # SHOWING → VISIBLE
        sm.transition("song_change", snapshot=old_data)
        sm.transition("error")          # any → HIDDEN with cleanup
    """

    def __init__(self):
        self._state: State = State.HIDDEN
        self._context: dict = {}        # snapshot, timers, etc.
        self._enter_cb: dict[State, list[Callable]] = {}
        self._exit_cb: dict[State, list[Callable]] = {}
        self._transition_cb: dict[str, Callable] = {}  # event → handler
        self._last_state: Optional[State] = None
        self._state_start: float = 0.0

    # ================================================================
    # Properties
    # ================================================================

    @property
    def state(self) -> State:
        return self._state

    @property
    def context(self) -> dict:
        return self._context

    @property
    def is_stable(self) -> bool:
        """True when in a stable state (no transition in progress)."""
        return self._state in (State.VISIBLE, State.HIDDEN)

    def is_transitioning(self) -> bool:
        """True when state requires skipping data updates."""
        return self._state in _TRANSITIONING_STATES

    # ================================================================
    # Callback registration
    # ================================================================

    def on_enter(self, state: State, cb: Callable) -> None:
        """Register a callback to run when entering a state."""
        self._enter_cb.setdefault(state, []).append(cb)

    def on_exit(self, state: State, cb: Callable) -> None:
        """Register a callback to run when exiting a state."""
        self._exit_cb.setdefault(state, []).append(cb)

    def on_transition(self, event: str, cb: Callable) -> None:
        """Register a handler for a specific transition event.

        The handler receives the new state and context as kwargs.
        It should return the animation to play (or None).
        """
        self._transition_cb[event] = cb

    # ================================================================
    # Transition
    # ================================================================

    def transition(self, event: str, **kwargs) -> State:
        """Execute an atomic state transition.

        Args:
            event: Transition event name (e.g. 'appear', 'song_change', 'completed')
            **kwargs: Context data for the transition (e.g. snapshot=old_data)

        Returns:
            The new state after transition.

        On any exception during the transition, forces state to HIDDEN
        with full cleanup to guarantee recovery.
        """
        old_state = self._state
        key = (old_state, event)

        # Look up target state
        new_state = _TRANSITIONS.get(key)
        if new_state is None:
            # Invalid transition — log and stay
            return old_state

        try:
            # 1. Run exit callbacks for old state
            for cb in self._exit_cb.get(old_state, []):
                try:
                    cb()
                except Exception as e:
                    _asm_log.warn(f"exit cb {old_state.value}→{event}: {e}")

            # 2. Update context
            self._context.update(kwargs)
            if event != "error":
                self._last_state = old_state

            # 3. Apply state change
            self._state = new_state
            self._state_start = time.time()

            # 4. Run enter callbacks for new state
            for cb in self._enter_cb.get(new_state, []):
                try:
                    cb()
                except Exception as e:
                    _asm_log.warn(f"enter cb {new_state.value}: {e}")

            # 5. Run transition handler
            handler = self._transition_cb.get(event)
            if handler:
                handler(new_state, **self._context)

            return new_state

        except Exception as e:
            # ★ Error recovery: force to HIDDEN with full cleanup
            _asm_log.error(f"transition {old_state.value}→{event} crashed: {e}", exc_info=True)
            self._force_hidden()
            raise

    def force(self, target: State, **kwargs) -> State:
        """Force state to a specific value (bypasses transition table).

        Use with caution — only for recovery paths.
        """
        old_state = self._state
        try:
            for cb in self._exit_cb.get(old_state, []):
                try: cb()
                except Exception as e:
                    _asm_log.warn(f"force exit cb {old_state.value}: {e}")
        except Exception as e:
            _asm_log.warn(f"force exit cbs crashed: {e}")

        self._context.update(kwargs)
        self._state = target
        self._state_start = time.time()

        try:
            for cb in self._enter_cb.get(target, []):
                try: cb()
                except Exception as e:
                    _asm_log.warn(f"force enter cb {target.value}: {e}")
        except Exception as e:
            _asm_log.warn(f"force enter cbs crashed: {e}")

        return target

    # ================================================================
    # Recovery
    # ================================================================

    def _force_hidden(self) -> None:
        """Force state to HIDDEN with minimal cleanup. Does NOT raise."""
        self._state = State.HIDDEN
        self._context.clear()
        self._state_start = time.time()
