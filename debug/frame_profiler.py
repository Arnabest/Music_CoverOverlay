"""Frame-time profiler — detects and logs intermittent stuttering.

Injects into GLRenderEngine._on_draw() to track per-frame timing with
microsecond precision. Logs frames that exceed the target budget and
correlates spike patterns with potential root causes.

Output: %TEMP%/qqoverlay_frame_profile.log
"""
import time
import os
import json
from collections import deque
from typing import Optional


class FrameProfiler:
    """High-precision frame time tracker for detecting intermittent stutter.

    Usage:
        profiler = FrameProfiler(target_fps=60)
        # In render loop:
        with profiler.frame():
            ... render code ...

    Spikes are logged immediately. A summary is written every 10 seconds.
    """

    def __init__(self, target_fps: int = 60, spike_threshold_ms: float = 20.0):
        self._target_fps = target_fps
        self._budget_ms = 1000.0 / target_fps  # 16.67ms for 60fps
        self._spike_threshold_ms = spike_threshold_ms  # frames above this get logged
        self._severe_threshold_ms = spike_threshold_ms * 2.5  # >50ms = severe

        # Ring buffer of recent frame times for pattern detection
        self._recent: deque = deque(maxlen=300)  # 5 seconds at 60fps

        # Phase timing
        self._phase_data_ms = 0.0
        self._phase_setup_ms = 0.0
        self._phase_read_ms = 0.0
        self._phase_ulw_ms = 0.0

        # Counters
        self._frame_count = 0
        self._spike_count = 0
        self._severe_count = 0
        self._max_frame_ms = 0.0
        self._sum_frame_ms = 0.0
        self._window_start = time.time()

        # Log path
        self._log_path = os.path.join(
            os.environ.get("TEMP", "."), "qqoverlay_frame_profile.log"
        )

        # Start marker
        self._write(f"=== Frame Profiler START target={target_fps}fps "
                    f"budget={self._budget_ms:.1f}ms "
                    f"spike_threshold={self._spike_threshold_ms:.0f}ms ===\n")

    def _write(self, msg: str) -> None:
        """Append a line to the profile log."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.time():.3f} {msg}\n")
        except Exception:
            pass

    def record_frame(self, total_ms: float, data_ms: float = 0.0,
                     setup_ms: float = 0.0, read_ms: float = 0.0,
                     ulw_ms: float = 0.0) -> None:
        """Record one frame's timing. Called from render loop."""
        self._frame_count += 1
        self._recent.append(total_ms)
        self._sum_frame_ms += total_ms
        if total_ms > self._max_frame_ms:
            self._max_frame_ms = total_ms

        # Phase tracking for diagnostics
        self._phase_data_ms += data_ms
        self._phase_setup_ms += setup_ms
        self._phase_read_ms += read_ms
        self._phase_ulw_ms += ulw_ms

        # Log spikes immediately
        if total_ms >= self._spike_threshold_ms:
            self._spike_count += 1
            severity = "SEVERE" if total_ms >= self._severe_threshold_ms else "SPIKE"
            if total_ms >= self._severe_threshold_ms:
                self._severe_count += 1

            # Find phase responsible
            total_measured = data_ms + setup_ms + read_ms + ulw_ms
            if total_measured > 0 and total_measured < total_ms * 1.5:
                # Phases are reasonably accurate
                phases = [
                    ("data", data_ms), ("setup", setup_ms),
                    ("read", read_ms), ("ULW", ulw_ms),
                ]
                phases.sort(key=lambda x: -x[1])
                worst_phase, worst_ms = phases[0]
                phase_info = f" worst_phase={worst_phase}({worst_ms:.1f}ms)"
            else:
                # Unaccounted time — likely GC pause, GIL contention, or system I/O
                unaccounted = total_ms - total_measured
                phase_info = f" UNACCOUNTED={unaccounted:.1f}ms (GC/GIL/IO?)"

            self._write(
                f"{severity} frame={self._frame_count} "
                f"total={total_ms:.1f}ms "
                f"data={data_ms:.1f}ms setup={setup_ms:.1f}ms "
                f"read={read_ms:.1f}ms ulw={ulw_ms:.1f}ms"
                f"{phase_info}"
            )

        # 10-second summary
        now = time.time()
        if now - self._window_start >= 10.0:
            self._write_summary(now)

    def _write_summary(self, now: float) -> None:
        """Write a 10-second summary and reset counters."""
        elapsed = now - self._window_start
        n = max(1, self._frame_count)
        avg_ms = self._sum_frame_ms / n
        avg_fps = n / elapsed if elapsed > 0 else 0
        pct_spike = self._spike_count / n * 100 if n > 0 else 0

        self._write(
            f"SUMMARY {elapsed:.1f}s | {n} frames | "
            f"avg={avg_ms:.1f}ms ({avg_fps:.1f}fps) | "
            f"max={self._max_frame_ms:.1f}ms | "
            f"spikes={self._spike_count} ({pct_spike:.1f}%) "
            f"severe={self._severe_count} | "
            f"phases: data={self._phase_data_ms/n:.1f}ms "
            f"setup={self._phase_setup_ms/n:.1f}ms "
            f"read={self._phase_read_ms/n:.1f}ms "
            f"ulw={self._phase_ulw_ms/n:.1f}ms"
        )

        # Pattern detection on recent frames
        self._detect_patterns(n, avg_ms)

        # Reset
        self._frame_count = 0
        self._spike_count = 0
        self._severe_count = 0
        self._max_frame_ms = 0.0
        self._sum_frame_ms = 0.0
        self._window_start = now
        self._phase_data_ms = 0.0
        self._phase_setup_ms = 0.0
        self._phase_read_ms = 0.0
        self._phase_ulw_ms = 0.0

    def _detect_patterns(self, n: int, avg_ms: float) -> None:
        """Detect stutter patterns from the recent frame ring buffer."""
        if len(self._recent) < 10:
            return

        frames = list(self._recent)

        # Pattern 1: Periodic spike — same interval suggests timer/IO contention
        spike_indices = [i for i, t in enumerate(frames) if t >= self._spike_threshold_ms]
        if len(spike_indices) >= 3:
            gaps = [spike_indices[i+1] - spike_indices[i]
                    for i in range(len(spike_indices) - 1)]
            if gaps:
                avg_gap = sum(gaps) / len(gaps)
                min_gap, max_gap = min(gaps), max(gaps)
                if max_gap - min_gap <= max(2, avg_gap * 0.3):
                    # Regular spacing → periodic IO or timer contention
                    period_s = avg_gap / (n / 10.0)  # convert to seconds
                    self._write(
                        f"PATTERN: periodic spikes every ~{period_s:.1f}s "
                        f"({len(spike_indices)} spikes, gap {avg_gap:.0f}±{max_gap-min_gap:.0f} frames)"
                    )

        # Pattern 2: Burst of spikes — suggests GC or background thread contention
        burst_count = 0
        max_burst = 0
        in_burst = False
        for t in frames:
            if t >= self._spike_threshold_ms:
                if not in_burst:
                    in_burst = True
                    burst_count += 1
                max_burst = max(max_burst, 1)
            else:
                if in_burst:
                    in_burst = False
        if burst_count >= 3:
            self._write(f"PATTERN: {burst_count} spike bursts detected — possible GC or thread contention")

        # Pattern 3: uni-modal vs multi-modal frame time distribution
        sorted_times = sorted(frames)
        p50 = sorted_times[len(sorted_times) // 2]
        p95 = sorted_times[int(len(sorted_times) * 0.95)]
        p99 = sorted_times[int(len(sorted_times) * 0.99)]
        if p99 > avg_ms * 3:
            self._write(
                f"DISTRIBUTION: p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms "
                f"avg={avg_ms:.1f}ms — heavy tail (p99/p50={p99/p50:.1f}x)"
            )


# Convenience: shared profiler for the GL engine
_shared: Optional[FrameProfiler] = None


def get_profiler(target_fps: int = 60) -> FrameProfiler:
    global _shared
    if _shared is None:
        _shared = FrameProfiler(target_fps=target_fps)
    return _shared
