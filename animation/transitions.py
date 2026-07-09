"""Pre-built transition sequences for common overlay animations.

ALL transitions use window-level alpha (engine.alpha) for fade-in/fade-out.
This fades the ENTIRE overlay to/from transparent — showing the desktop through
— rather than blending elements toward the background color (which looked like
"turning black").

Song change (3-phase):
  Phase 1: fade_out — window alpha → 0, old content dissolves to transparent
  Phase 2: blank   — load new data, minimum 150ms pause, wait for cover
  Phase 3: fade_in — window alpha → target, new content materializes

Song end / Auto-hide:
  window alpha → 0 (fade to transparent) → hide

All durations and easing are read from config.
"""
from typing import Callable
from animation.timeline import AnimationTimeline, Animation


class TransitionFactory:
    """Creates common animation sequences for the overlay.

    Each method returns an Animation that can be passed to Timeline.play().
    Uses engine.alpha (window-level transparency) for all fades.
    """

    def __init__(self, config: dict, compositor=None, engine=None):
        self.config = config
        self.compositor = compositor
        self.engine = engine
        anim_cfg = config.get("animation", {})
        self.fade_in_ms = anim_cfg.get("fade_in_ms", 800) / 1000.0
        self.fade_out_ms = anim_cfg.get("fade_out_ms", 800) / 1000.0
        self.target_alpha = config.get("window", {}).get("opacity", 0.85)
        self.fps = anim_cfg.get("fps", 60)
        self.loading_timeout = anim_cfg.get("loading_timeout", 8)

    # ================================================================
    # Appear / Disappear (window-alpha based)
    # ================================================================

    def appear(self) -> Animation:
        """Fade the window from transparent to configured opacity.
        Nodes render at full opacity; the window itself materializes.
        """
        tl = AnimationTimeline()
        return tl.to(self.engine, "alpha", self.target_alpha,
                     self.fade_in_ms, "ease-out", from_value=0.0)

    def disappear(self) -> Animation:
        """Fade the window from current opacity to fully transparent.
        The overlay dissolves to show the desktop behind it.
        """
        tl = AnimationTimeline()
        return tl.to(self.engine, "alpha", 0.0,
                     self.fade_out_ms, "ease-in")

    # ================================================================
    # Song change: fade out → blank → fade in
    # ================================================================

    def song_change(
        self,
        on_load: Callable,
        cover_ready: Callable | None = None,
        on_phase3_start: Callable | None = None,
    ) -> Animation:
        """Clean three-phase song change using window alpha:

        Phase 1: fade_out   — window alpha → 0. Old content dissolves to
                              transparent (user sees desktop through overlay).
        Phase 2: blank      — load new data, wait for cover. Window stays at
                              alpha=0 (fully transparent, desktop visible).
        Phase 3: fade_in    — window alpha → target. New content materializes.

        A minimum 150ms blank pause ensures a visible gap between songs even
        when the cover loads instantly (e.g., from cache).

        on_phase3_start is called after cover_ready returns True and before
        the fade-in begins. Use it to freeze a data snapshot for Phase 3.
        """
        tl = AnimationTimeline()
        steps = []

        # Phase 1: Fade entire window to transparent
        steps.append(tl.to(self.engine, "alpha", 0.0,
                          self.fade_out_ms, "ease-in"))

        # Phase 2: Load new data + blank pause + wait for cover
        steps.append(tl.call(on_load))
        if cover_ready:
            # Minimum 150ms visible blank pause BETWEEN songs.
            # Parallel waits for BOTH the minimum pause AND the cover-ready
            # condition — whichever takes longer determines the blank duration.
            steps.append(tl.parallel(
                tl.wait(0.15),       # minimum blank period
                tl.wait_until(cover_ready, timeout=self.loading_timeout),
            ))
        else:
            steps.append(tl.wait(0.15))

        # ★ Phase 3 entry point — freeze data snapshot BEFORE fade-in starts
        if on_phase3_start:
            steps.append(tl.call(on_phase3_start))

        # Phase 3: Fade entire window back to configured opacity
        steps.append(tl.to(self.engine, "alpha", self.target_alpha,
                          self.fade_in_ms, "ease-out", from_value=0.0))

        return tl.sequence(*steps)

    # ================================================================
    # Song end: fade to transparent then hide
    # ================================================================

    def song_end(self) -> Animation:
        """Song ended — fade the entire overlay to transparent."""
        tl = AnimationTimeline()
        return tl.to(self.engine, "alpha", 0.0, self.fade_out_ms, "ease-in")

    # ================================================================
    # Source switch: dim → app-icon bridge → brighten
    # ================================================================

    def source_switch(
        self,
        on_load: Callable,
        cover_ready: Callable | None = None,
        on_phase3_start: Callable | None = None,
    ) -> Animation:
        """Source-change transition (QQ Music ↔ NetEase).

        Unlike song_change which fades the window to fully transparent
        (user sees desktop), source_switch dims to a low alpha so the
        app-icon placeholder remains visible as a bridge between the
        two sources.  This prevents a "harsh cut" feeling when the
        music source changes and no cover data is immediately available.

        Phase 1: dim       — window alpha → 0.12 (old cover dims out)
        Phase 2: bridge    — load new data + show app-icon placeholder,
                             minimum 300ms pause, wait for cover
        Phase 3: brighten  — window alpha → target (new cover materializes)
        """
        tl = AnimationTimeline()
        steps = []

        # Phase 1: Dim window (NOT fully transparent — app icon stays visible)
        steps.append(tl.to(self.engine, "alpha", 0.12,
                          self.fade_out_ms, "ease-in"))

        # Phase 2: Load new data + bridge pause + wait for cover
        steps.append(tl.call(on_load))
        if cover_ready:
            steps.append(tl.parallel(
                tl.wait(0.30),       # minimum bridge period
                tl.wait_until(cover_ready, timeout=self.loading_timeout),
            ))
        else:
            steps.append(tl.wait(0.30))

        # Phase 3 entry — freeze new data snapshot
        if on_phase3_start:
            steps.append(tl.call(on_phase3_start))

        # Phase 3: Brighten window back to configured opacity
        steps.append(tl.to(self.engine, "alpha", self.target_alpha,
                          self.fade_in_ms, "ease-out", from_value=0.12))

        return tl.sequence(*steps)

    # ================================================================
    # Progress indicator fade in (delayed after song start)
    # ================================================================

    def progress_fade_in(self, progress_node) -> Animation:
        """Fade in the progress indicator (element-level opacity).
        This is the only element-level fade — progress materializes
        independently within the already-visible window.
        """
        from scene.node import SceneNode
        tl = AnimationTimeline()
        return tl.to(progress_node, "opacity", 1.0, 0.5, "ease-out",
                     from_value=0.0)
