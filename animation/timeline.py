"""Animation timeline — orchestrates sequences and parallel groups of tweens.

The timeline replaces the old 7-state animation state machine.
Animations are declarative: describe what properties change, in what order,
with what easing. The timeline handles timing and chaining.

Usage:
    timeline = AnimationTimeline()

    # Fade in a node over 0.4s
    timeline.to(node, "opacity", 1.0, 0.4, "ease-out")

    # Sequence: fade out → change data → fade in
    timeline.sequence(
        timeline.to(node, "opacity", 0.0, 0.3),
        timeline.call(lambda: load_new_data()),
        timeline.to(node, "opacity", 1.0, 0.4),
    )

    # Per-frame:
    timeline.update(dt)
"""
import time
from typing import Callable, Any
from animation.tween import Tween
from animation.easing import get_easing
from core.logs import get_logger

_tl_log = get_logger("timeline")


class Animation:
    """Base class for animation primitives.

    An Animation is something that can be started, updated, and queried for completion.
    """

    def start(self) -> None:
        raise NotImplementedError

    def update(self, dt: float) -> None:
        raise NotImplementedError

    @property
    def done(self) -> bool:
        raise NotImplementedError

    def finish(self) -> None:
        """Force animation to completion."""
        raise NotImplementedError


class TweenAnim(Animation):
    """Wrapper for a single Tween."""

    def __init__(self, tween: Tween):
        self.tween = tween

    def start(self) -> None:
        self.tween.start()

    def update(self, dt: float) -> None:
        self.tween.update(dt)

    @property
    def done(self) -> bool:
        return self.tween.done

    def finish(self) -> None:
        self.tween.finish()


class CallAnim(Animation):
    """Animation that calls a function (instant)."""

    def __init__(self, fn: Callable, delay: float = 0.0):
        self.fn = fn
        self.delay = delay
        self._done = False
        self._elapsed = 0.0
        self._called = False

    def start(self) -> None:
        self._done = False
        self._elapsed = 0.0
        self._called = False

    def update(self, dt: float) -> None:
        if self._done:
            return
        self._elapsed += dt
        if self._elapsed >= self.delay and not self._called:
            self._called = True
            try:
                self.fn()
            finally:
                self._done = True  # ★ Always advance, even if fn() throws

    @property
    def done(self) -> bool:
        return self._done

    def finish(self) -> None:
        if not self._called:
            self.fn()
            self._called = True
        self._done = True


class Sequence(Animation):
    """Play animations one after another.

    Each animation starts when the previous one finishes.
    """

    def __init__(self, *animations: Animation):
        self.animations = list(animations)
        self._current_idx = 0
        self._done = False

    def start(self) -> None:
        self._current_idx = 0
        self._done = False
        if self.animations:
            self.animations[0].start()

    def update(self, dt: float) -> None:
        if self._done:
            return
        if self._current_idx >= len(self.animations):
            self._done = True
            return

        current = self.animations[self._current_idx]
        current.update(dt)
        if current.done:
            self._current_idx += 1
            if self._current_idx >= len(self.animations):
                self._done = True
            else:
                self.animations[self._current_idx].start()

    @property
    def done(self) -> bool:
        return self._done

    def finish(self) -> None:
        for i in range(self._current_idx, len(self.animations)):
            self.animations[i].finish()
        self._current_idx = len(self.animations)
        self._done = True


class Parallel(Animation):
    """Play multiple animations simultaneously.

    Done when all animations are done.
    """

    def __init__(self, *animations: Animation):
        self.animations = list(animations)
        self._done = False

    def start(self) -> None:
        self._done = False
        for anim in self.animations:
            anim.start()

    def update(self, dt: float) -> None:
        if self._done:
            return
        all_done = True
        for anim in self.animations:
            if not anim.done:
                anim.update(dt)
                if not anim.done:
                    all_done = False
        if all_done:
            self._done = True

    @property
    def done(self) -> bool:
        return self._done

    def finish(self) -> None:
        for anim in self.animations:
            anim.finish()
        self._done = True


class Wait(Animation):
    """Wait for a duration."""

    def __init__(self, duration: float):
        self.duration = duration
        self._elapsed = 0.0
        self._done = False

    def start(self) -> None:
        self._elapsed = 0.0
        self._done = False

    def update(self, dt: float) -> None:
        if self._done:
            return
        self._elapsed += dt
        if self._elapsed >= self.duration:
            self._done = True

    @property
    def done(self) -> bool:
        return self._done

    def finish(self) -> None:
        self._elapsed = self.duration
        self._done = True


class WaitUntil(Animation):
    """Wait until a condition function returns True, or timeout.

    The condition is polled every frame. Useful for waiting on
    async data (SMTC cover, web API results) before fading in.
    """

    def __init__(self, condition, timeout: float = 3.0):
        self.condition = condition   # Callable[[], bool]
        self.timeout = timeout
        self._elapsed = 0.0
        self._done = False

    def start(self) -> None:
        self._elapsed = 0.0
        self._done = False
        # Check immediately — condition might already be met
        try:
            if self.condition():
                self._done = True
        except Exception as e:
            _tl_log.warn(f"WaitUntil condition check failed: {e}")
            # ★ If condition throws, fall through to poll-based timeout.
            # _done stays False; update() will handle the timeout safely.

    def update(self, dt: float) -> None:
        if self._done:
            return
        self._elapsed += dt
        # ★ Check timeout FIRST (outside try block) so a throwing
        # condition can never stall the animation indefinitely.
        if self._elapsed >= self.timeout:
            self._done = True
            return
        try:
            if self.condition():
                self._done = True
        except Exception as e:
            _tl_log.warn(f"WaitUntil condition poll failed: {e}")

    @property
    def done(self) -> bool:
        return self._done

    def finish(self) -> None:
        self._elapsed = self.timeout
        self._done = True


class AnimationTimeline:
    """Orchestrates animation playback.

    The timeline manages a root animation (which may be a Sequence or Parallel).
    Call update(dt) each frame to advance all active tweens.
    """

    def __init__(self):
        self._root: Animation | None = None
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing and not (self._root and self._root.done)

    def play(self, animation: Animation | None = None) -> None:
        """Start playing an animation (or resume the current one).

        If a new animation is provided, any currently-playing animation
        is first stopped (without triggering side effects like CallAnim
        finish callbacks) to prevent orphaned transitions.
        """
        if animation is not None:
            # ★ Stop current animation WITHOUT triggering side effects.
            # finish() calls callbacks which can mutate _frozen/_visible state
            # at unexpected times. We just abandon the old root and start fresh.
            self._playing = False
            self._root = animation
        if self._root:
            self._root.start()
            self._playing = True

    def stop(self) -> None:
        """Stop all animations without triggering side-effect callbacks.

        Unlike the old behavior (which called finish() on the root, triggering
        CallAnim closures), this just abandons the root. Use this for
        interruption scenarios where you don't want the old animation's
        callbacks to fire.
        """
        self._playing = False
        self._root = None

    def finish(self) -> None:
        """Complete all animations AND trigger any pending callbacks.

        Use this ONLY when you WANT side effects (e.g., _load_new_song, _hide)
        to fire as part of completing the current animation sequence.
        """
        if self._root:
            self._root.finish()
        self._playing = False

    def update(self, dt: float) -> None:
        """Advance all running animations by dt seconds."""
        if not self._playing or not self._root:
            return
        self._root.update(dt)
        if self._root.done:
            self._playing = False

    # ---- Factory methods (fluent API) ----

    def to(
        self,
        obj: Any,
        attr: str,
        to_value: float,
        duration: float = 0.4,
        easing: str = "ease-out",
        from_value: float | None = None,
        delay: float = 0.0,
    ) -> TweenAnim:
        """Create a single tween animation."""
        tween = Tween(obj, attr, to_value, duration, easing, from_value, delay)
        return TweenAnim(tween)

    def sequence(self, *animations: Animation) -> Sequence:
        """Create a sequential animation group."""
        return Sequence(*animations)

    def parallel(self, *animations: Animation) -> Parallel:
        """Create a parallel animation group."""
        return Parallel(*animations)

    def call(self, fn: Callable, delay: float = 0.0) -> CallAnim:
        """Create a callback animation."""
        return CallAnim(fn, delay)

    def wait(self, duration: float) -> Wait:
        """Create a wait animation."""
        return Wait(duration)

    def wait_until(self, condition, timeout: float = 3.0) -> WaitUntil:
        """Create a conditional wait — completes when condition() returns True or timeout."""
        return WaitUntil(condition, timeout)
