"""Animation tween — interpolates a property from current value to target.

A Tween tracks:
- Target object + attribute name
- Start value, end value, current progress
- Duration, elapsed time
- Easing function
"""
import time
from typing import Any, Callable
from animation.easing import get_easing


class Tween:
    """Single-property animation tween.

    Animates `obj.attr` from its current value (or `from_value`) to `to_value`
    over `duration` seconds using `easing` function.

    Usage:
        tween = Tween(node, "opacity", to_value=1.0, duration=0.4, easing="ease-out")
        tween.start()
        while not tween.done:
            dt = clock.tick()
            tween.update(dt)
    """

    def __init__(
        self,
        obj: Any,
        attr: str,
        to_value: float,
        duration: float = 0.4,
        easing: str = "ease-out",
        from_value: float | None = None,
        delay: float = 0.0,
    ):
        self.obj = obj
        self.attr = attr
        self.to_value = float(to_value)
        self.duration = max(0.001, duration)
        self.easing_fn: Callable = get_easing(easing)
        self.delay = delay

        self.from_value = float(from_value) if from_value is not None else None
        self._start_value: float = 0.0
        self._elapsed: float = 0.0
        self._started: bool = False
        self._done: bool = False
        self._delay_elapsed: float = 0.0

    @property
    def done(self) -> bool:
        return self._done

    @property
    def progress(self) -> float:
        """0.0 to 1.0 — how much of the tween is complete."""
        if self.duration <= 0:
            return 1.0
        return min(1.0, self._elapsed / self.duration)

    @property
    def current_value(self) -> float:
        """The interpolated value at current progress."""
        t = self.progress
        et = self.easing_fn(t)
        return self._start_value + (self.to_value - self._start_value) * et

    def start(self) -> None:
        """Begin the tween. Snaps current value if from_value not specified."""
        if self.from_value is not None:
            self._start_value = self.from_value
        else:
            self._start_value = float(getattr(self.obj, self.attr, 0.0))
        self._elapsed = 0.0
        self._delay_elapsed = 0.0
        self._started = True
        self._done = False
        # Apply start value immediately
        setattr(self.obj, self.attr, self._start_value)

    def update(self, dt: float) -> None:
        """Advance the tween by dt seconds."""
        if self._done or not self._started:
            return

        # Handle delay
        if self._delay_elapsed < self.delay:
            self._delay_elapsed += dt
            return

        self._elapsed += dt
        if self._elapsed >= self.duration:
            self._elapsed = self.duration
            self._done = True

        setattr(self.obj, self.attr, self.current_value)

    def finish(self) -> None:
        """Force the tween to completion."""
        self._elapsed = self.duration
        self._done = True
        setattr(self.obj, self.attr, self.to_value)

    def reset(self) -> None:
        """Reset the tween to initial state."""
        self._elapsed = 0.0
        self._delay_elapsed = 0.0
        self._started = False
        self._done = False
