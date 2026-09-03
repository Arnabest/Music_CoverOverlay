"""Overlay window manager — integrates render engine + scene + data.

This is the main coordinator that replaces the old QQMusicOverlay(Tk) class.
Thin layer: delegates rendering to engine, animation to timeline, data to reader.
"""
import time
import queue
import os
import datetime
from typing import Optional, Callable

from config import save_config
from core.reader import QQMusicReader
from core.progress import ProgressTracker
from core.duration import record_position, get_learned, save_snapshot, clear_snapshot
from scene.graph import SceneGraph
from animation.timeline import AnimationTimeline
from animation.transitions import TransitionFactory
from animation.state_machine import OverlayStateMachine, State
from render.compositor import Compositor
from render.engine import RenderEngine
from core.logs import get_logger

_window_logger = get_logger("window")


def _trace(msg: str) -> None:
    """Backward-compat wrapper."""
    _window_logger.info(msg)


class OverlayWindow:
    """Manages the desktop overlay window and its lifecycle.

    Coordinates:
    - Scene graph (layout)
    - Animation timeline (transitions)
    - Render engine (pyglet + OpenGL)
    - Data reader (QQ Music polling)
    - Progress tracker (SMTC + wall-clock)
    - System tray commands (via queue)
    """

    @property
    def root(self):
        """Tk root window - exposed so SettingsWindow can parent its Toplevel."""
        return getattr(self.engine, 'root', None)

    def __init__(self, config: dict, reader: QQMusicReader, status: dict | None = None,
                 smtc_engine=None, runtime_api=None):
        self.config = config
        self.reader = reader
        self._status = status or {}
        self._runtime_api = runtime_api

        # Scene
        self.scene = SceneGraph()
        self.scene.load_from_config(config.get("scene", {}))

        # Rendering (must be created before transitions — they depend on compositor/engine)
        self.compositor = Compositor(config)
        renderer = config.get("animation", {}).get("renderer", "pil")
        if renderer == "opengl":
            from render.gl_engine import GLRenderEngine
            self.engine = GLRenderEngine(config, self.scene, self.compositor)
        else:
            self.engine = RenderEngine(config, self.scene, self.compositor)

        # Animation (depends on compositor + engine for window/progress alpha)
        self.timeline = AnimationTimeline()
        self.transitions = TransitionFactory(config, compositor=self.compositor, engine=self.engine)

        # Progress — reads from SmtcEngine, never triggers polling
        self.progress = ProgressTracker()
        # Playback state detector — independent of progress.py internals
        from core.playback_detector import PlaybackStateDetector
        self._eco = PlaybackStateDetector()
        self._smtc_engine = smtc_engine
        if smtc_engine:
            self.progress.set_smtc_engine(smtc_engine)
            self.reader.set_smtc_engine(smtc_engine)

        # ====== Animation State Machine ======
        self._state = OverlayStateMachine()

        # ★ Active data buffer — the single source of truth for what the
        # renderer should display.  During stable states (VISIBLE, HIDDEN)
        # it is None and _provide_data() returns live reader data.  During
        # transitions it holds frozen song metadata (cover, title, artist,
        # lyrics) so the renderer never sees stale or mixed data.
        # Progress (position/duration/is_paused) is ALWAYS live.
        self._active_data: dict | None = None

        # ★ Bug #181 render-level gate: force progress=0 from the moment
        # a song change is detected until the transition completes and the
        # state enters VISIBLE.  This is the ONLY reliable defense against
        # the race between _on_draw (which calls _provide_data) and
        # _on_frame_update (which calls progress.reset).  No matter what
        # order pyglet fires events in, the renderer will never see a
        # non-zero progress value while this flag is set.
        self._force_progress_zero: bool = False

        # State enter/exit callbacks
        self._state.on_enter(State.HIDDEN, self._on_enter_hidden)
        self._state.on_enter(State.VISIBLE, self._on_enter_visible)
        self._state.on_exit(State.VISIBLE, self._on_exit_visible)

        # Transition handlers
        self._state.on_transition("appear", self._handle_appear)
        self._state.on_transition("manual_show", self._handle_manual_show)
        self._state.on_transition("manual_hide", self._handle_manual_hide)
        self._state.on_transition("song_change", self._handle_song_change)
        self._state.on_transition("song_end", self._handle_song_end)
        self._state.on_transition("auto_hide", self._handle_auto_hide)
        self._state.on_transition("completed", self._handle_completed)
        self._state.on_transition("error", self._handle_error)

        # Wire engine callbacks
        self.engine.set_data_provider(self._provide_data)
        self.engine.set_reader(reader)
        self.engine.set_callbacks(
            update=self._on_frame_update,
            settings=self._on_open_settings,
            exit=self._on_exit,
            show=lambda: self._state.transition("manual_show"),
            hide=lambda: self._state.transition("manual_hide"),
            topmost_toggle=self._on_topmost_toggle,
            clickthrough_toggle=self._on_clickthrough_toggle,
            element_toggle=self._on_element_toggle,
            tray_refresh=None,  # wired later via set_tray_refresh()
            tray_cmd=self._handle_tray_command,  # ★ Direct gl_queue bypass
        )

        # Tray command queue (set by main.py)
        self._tray_queue: Optional[queue.Queue] = None
        self._settings_callback: Optional[Callable] = None
        self._tray_refresh: Optional[Callable] = None
        # ★ Suppress auto-show after manual hide (user explicitly hid window).
        # Cleared on manual_show, appear, or new song detection.
        self._suppress_auto_show: bool = False
        self._tray_refresh: Optional[Callable] = None

        # Config
        self._stay_forever = config.get("animation", {}).get("stay_forever", False)
        self._stay_seconds = config.get("animation", {}).get("stay_seconds", 8)

    # ================================================================
    # Lifecycle
    # ================================================================

    def run(self) -> None:
        """Start the render loop (blocking). Window starts hidden,
        first song detection triggers appear animation via state machine."""
        self.engine.run()

    def close(self) -> None:
        """Close the window and clean up."""
        # ★ Destroy settings window BEFORE root.destroy(). When exiting via
        # right-click menu, root.destroy() is called from inside a tk_popup
        # callback; Tk's cleanup order doesn't cascade to Toplevels in
        # that path. Explicitly destroying the settings window first avoids
        # it being left orphaned on screen.
        _sw = getattr(self, '_settings_window', None)
        if _sw is not None and _sw.window is not None:
            try:
                _sw.window.destroy()
            except Exception:
                pass
            self._settings_window = None
        self._state.transition("error")  # Force HIDDEN with cleanup
        self.engine.stop()
        self.engine.cleanup()
        save_config(self.config)

    # ================================================================
    # Callbacks
    # ================================================================

    def set_tray_queue(self, q: queue.Queue) -> None:
        self._tray_queue = q

    def set_settings_callback(self, cb: Callable) -> None:
        self._settings_callback = cb

    def set_tray_refresh(self, cb: Callable) -> None:
        self._tray_refresh = cb
        # Update engine callback now that tray_refresh is available
        self.engine.set_callbacks(tray_refresh=cb)

    def _publish_runtime_state(self, now: float | None = None) -> None:
        runtime_api = self._runtime_api
        if runtime_api is None:
            return
        now = time.time() if now is None else now
        last_pub = getattr(self, "_last_runtime_state_pub", 0.0)
        if now - last_pub < 0.25:
            return
        try:
            player = self._smtc_engine.player if self._smtc_engine else ""
        except Exception:
            player = ""
        try:
            playback_status = self._smtc_engine.playback_status if self._smtc_engine else ""
        except Exception:
            playback_status = ""
        payload = {
            "schema": 1,
            "app": "QQMusicOverlayNG",
            "source": "runtime_api",
            "updated_at_epoch": now,
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "title": self.reader.song_name or "",
            "artist": self.reader.artist_name or "",
            "album": "",
            "player": player or "",
            "playback_status": playback_status or "",
            "overlay_state": self._state.state.value,
            "is_paused": bool(self.progress.is_paused),
            "playing": bool(self.reader.is_playing),
            "position_sec": float(self.progress.position or 0.0),
            "duration_sec": float(self.progress.duration or 0.0),
            "progress": (
                float(self.progress.position or 0.0) / float(self.progress.duration or 0.0)
                if float(self.progress.duration or 0.0) > 0
                else 0.0
            ),
            "no_timeline": bool(self.progress.no_timeline),
            "always_on_top": bool(self.config.get("window", {}).get("always_on_top", True)),
            "click_through": bool(self.config.get("window", {}).get("click_through", False)),
        }
        runtime_api.update(payload)
        self._last_runtime_state_pub = now


    # ================================================================
    # Per-frame update (called by engine before render)
    # ================================================================

    def _on_frame_update(self, dt: float) -> None:
        """Called every frame (~60fps) before rendering."""
        # Process tray commands (always, even during transitions)
        self._process_tray_queue()

        # ★ Bug #181 watchdog: log progress every ~5s (300 frames) at INFO
        # so freeze is always detectable regardless of DEBUG level.
        _wd = getattr(self, '_watchdog_ct', 0) + 1
        self._watchdog_ct = _wd
        if _wd % 300 == 1:
            _window_logger.info(
                f"WATCHDOG: pos={self.progress.position:.1f}s "
                f"dur={self.progress.duration:.1f}s "
                f"paused={self.progress.is_paused} "
                f"force_zero={self._force_progress_zero} "
                f"fresh={self.progress._fresh_reset} "
                f"nodata={self.progress.no_timeline} "
                f"state={self._state.state.value}")

        # ★ Prompt user when SMTC source lacks capabilities
        _suppress = self.config.get("behavior", {}).get(
            "suppress_missing_warnings", False)
        if not getattr(self, '_startup_missing_prompted', False):
            # Determine which capabilities the current SMTC player lacks
            missing = []
            try:
                _s = self._smtc_engine
                _player = _s.player if _s else ""
            except Exception:
                _player = ""
            if self.progress.no_timeline:
                missing.append("实时播放进度 / 时长")
            # Currently only NetEase has known limitations
            # Future: add lyrics/cover checks per player

            if missing and not _suppress:
                self._startup_missing_prompted = True
                self._no_timeline_force_hide = True  # default: hide
                lines = "当前播放源不支持以下功能：\n\n"
                for i, m in enumerate(missing, 1):
                    lines += f"  - {m}\n"
                lines += "\n是否自动关闭相关显示选项？\n\n"
                lines += "  是 — 自动隐藏（推荐）\n"
                lines += "  否 — 保持显示\n\n"
                lines += "提示：设置 → 来源 → 不再提示 可永久关闭"

                import ctypes as _ct
                import threading as _thr
                def _prompt_missing():
                    try:
                        r = _ct.windll.user32.MessageBoxW(
                            0, lines,
                            "Music CoverOverlay — 功能缺失",
                            0x40 | 0x4)
                        self._no_timeline_force_hide = (r == 6)
                        if self._no_timeline_force_hide:
                            self._startup_disable_all = True
                    except Exception:
                        self._no_timeline_force_hide = True
                        self._startup_disable_all = True
                _thr.Thread(target=_prompt_missing, daemon=True).start()

        # Apply startup disable to scene config (runs on main thread after prompt)
        if getattr(self, '_startup_disable_all', False):
            self._startup_disable_all = False
            modified = False
            for node in self.config.get("scene", {}).get("nodes", []):
                nid = node.get("id", "")
                if self.progress.no_timeline and nid == "progress":
                    node["mode"] = "off"
                    node.setdefault("time_text", {})["visible"] = False
                    modified = True
                if self.progress.no_timeline and nid == "progress_time":
                    node["visible"] = False
                    modified = True
            if modified:
                self.scene.load_from_config(self.config.get("scene", {}))
                self.scene.resolve_layout(self.engine.width, self.engine.height)
                _window_logger.debug("startup: disabled elements for missing capabilities")

        # ★ Source-change tracking: detect player switches (QQ Music ↔ NetEase etc.)
        # When the source changes while visible, trigger a full song_change transition
        # so the user sees a smooth fade_out → load → fade_in instead of elements
        # instantly appearing/disappearing.
        _sm = self._smtc_engine
        _cur_player = _sm.player if _sm else ""
        _prev_player = getattr(self, '_last_smtc_player', "")
        if _cur_player != _prev_player:
            self._last_smtc_player = _cur_player
            _window_logger.debug(
                f"source change: {_prev_player!r} → {_cur_player!r}")
            # Set flag so _load_new_song() applies source-specific scene changes
            self._source_just_changed = True
            # Update NetEase timeline visibility flag
            if _cur_player == "netease":
                self._timeline_hidden_for_netease = True
            elif _cur_player == "qqmusic":
                self._timeline_hidden_for_netease = False
                self._no_timeline_force_hide = False
                self._no_timeline_prompted = False

        # Update animation timeline
        if self.timeline.is_playing:
            self.timeline.update(dt)

        # Detect timeline completion — fire 'completed' event
        if self._state.is_transitioning() and not self.timeline.is_playing:
            self._state.transition("completed")

        # Skip data updates during transitions
        if self._state.is_transitioning():
            return

        # Only poll data in stable states (VISIBLE or HIDDEN)
        st = self._state.state
        if st not in (State.VISIBLE, State.HIDDEN):
            return

        # ★ Capture current display state BEFORE reader poll.
        old_data = self._provide_data() if st == State.VISIBLE else None

        # Throttled reader polling
        now = time.time()
        last_poll = getattr(self, '_last_reader_poll', 0.0)
        _prev_song = self.reader.song_name  # ★ save before update for startup detection
        if now - last_poll >= 0.1:
            song_changed = self.reader.update()
            self.reader.update_api_results()
            self._last_reader_poll = now
        else:
            song_changed = False
        is_playing = self.reader.is_playing

        # Update progress
        self.progress.update(
            self.reader.duration_seconds or 0,
            self.reader.song_name,
            self.reader.artist_name)

        # Update playback state detector (public APIs only)
        if self._smtc_engine:
            self._eco.update(
                smtc_status=self._smtc_engine.playback_status,
                smtc_pos=self._smtc_engine.position,
                smtc_player=self._smtc_engine.player,
                is_player_running=(is_playing or self.reader.is_playing),
                song_name=self.reader.song_name,
            )
        self._publish_runtime_state(now)

        # ★ Auto-show on startup when stay_forever is enabled.
        # Suppressed when user manually hid the window via tray menu.
        if (st == State.HIDDEN and self._stay_forever
                and not getattr(self, '_startup_shown', False)
                and not self._suppress_auto_show):
            self._startup_shown = True
            self._auto_show_no_fade = True
            self._state.transition("manual_show")

        # Handle song change OR source change (VISIBLE → TRANSITIONING, or HIDDEN → SHOWING)
        # ★ Source change: when the music source switches (QQ Music ↔ NetEase),
        # trigger transition regardless of is_playing — the user is actively
        # switching between apps.  Without this, a source change during pause
        # silently updates cover/song data without animation, causing a harsh cut.
        # The source_switch transition uses a dimmed window (NOT fully transparent)
        # so the app icon is visible as a bridge between the two sources.
        _source_changed = getattr(self, '_source_just_changed', False)
        if _source_changed:
            self._source_just_changed = False
        if song_changed:
            # ★ Prevent rapid-fire song changes from false-positive
            # detection (SMTC flicker after pause/resume, garbled
            # encoding, etc.).  Each transition calls progress.reset()
            # which sets position=0 — a false positive during playback
            # causes perceived "progress stuck at 00:00".
            _now = time.time()
            _last_sc = getattr(self, '_last_song_change_time', 0.0)
            if _now - _last_sc < 2.0:
                _window_logger.debug(
                    f"suppressed rapid song_change "
                    f"({_now - _last_sc:.1f}s since last, "
                    f"song={self.reader.song_name!r})")
                song_changed = False  # consume the flag without acting

        # ★ Bug #181: reset progress IMMEDIATELY when song change is
        # confirmed, BEFORE any transition handler runs.  progress.update()
        # at line 304 may have already set stale SMTC data (old song's
        # near-end position) because _fresh_reset was False.  This reset
        # guarantees progress is 0/0 by the time _provide_data() renders
        # the first frame of the appear/song_change transition.
        if song_changed:
            self.progress.reset()
            # ★ Startup bypass: when the app launches while music is already
            # playing mid-song, there is no "old song" whose stale SMTC data
            # needs guarding against.  The _fresh_reset guard expects s_pos
            # near 0 to confirm SMTC has switched to the new song, but if the
            # song has been playing for >5 seconds, s_pos is already >5 and
            # the guard NEVER releases — progress would stuck at 0 forever.
            # Clear _fresh_reset when the previous song name was empty (app
            # just launched, no prior song context).
            if not _prev_song:
                self.progress._fresh_reset = False
            self._force_progress_zero = True  # ★ render-level gate (Bug #181)

        if song_changed:
            # ★ Song changes always trigger transitions — do NOT gate on
            # is_playing.  At song boundaries (end of track → next song),
            # is_playing can briefly be False right when song_changed is
            # detected.  If the transition is skipped, song_changed is
            # consumed by reader.update() and never fires again → cover
            # silently changes (app icon placeholder flashes).
            #
            # ★ New song clears manual-hide suppression — a genuinely
            # different song should be shown.
            self._suppress_auto_show = False
            self._last_song_change_time = _now
            if st == State.HIDDEN:
                # ★ Do NOT gate on is_playing — at song boundaries
                # (end of track → next song), is_playing can briefly
                # be False right when song_changed fires.  If the
                # transition is skipped, song_changed is consumed by
                # reader.update() and never fires again.
                _trace(f"triggering appear transition (song={song_changed})")
                self._state.transition("appear")
            elif st == State.VISIBLE:
                _trace(f"triggering song_change: {self.reader.song_name}")
                # ★ Flag quick song-change: within 1.5s of entering VISIBLE,
                # the user JUST saw new info fade in.  Fading it right back
                # out creates a perceived "flash-back".  _handle_song_change
                # skips Phase 1 (fade_out) for quick changes.
                _visible_age = time.time() - self._state.context.get('_visible_since', 0)
                self._quick_song_change = (_visible_age < 1.5)
                self._state.transition("song_change", snapshot=old_data)
        elif _source_changed:
            # ★ Source change — trigger even without is_playing.
            # Uses source_switch: dim→app-icon-bridge→brighten instead of
            # song_change: fade-to-transparent→load→fade-in.
            # Suppressed if user manually hid the window.
            if st == State.HIDDEN:
                if not self._suppress_auto_show:
                    _trace(f"triggering appear transition (source={_source_changed})")
                    self._state.transition("appear")
            elif st == State.VISIBLE:
                _trace(f"triggering source_switch: player={_cur_player!r}")
                self._source_switch_active = True  # ★ signal for _handle_song_change
                self._state.transition("song_change", snapshot=old_data)

        # ★ If auto-show completed but song data exists, restore all elements
        # (handles race where song was detected during auto-show transition).
        # Only runs once — clear _startup_shown after restore to prevent
        # overriding user's manual visibility settings in the settings UI.
        if (st == State.VISIBLE
                and getattr(self, '_startup_shown', False)
                and bool(self.reader.song_name)):
            self._startup_shown = False  # ★ one-shot: don't repeat every frame
            _needs_restore = False
            for _nid in ("title", "artist", "lyrics", "progress", "progress_time"):
                _node = self.scene.get(_nid)
                if _node and not _node.visible:
                    if getattr(self, '_timeline_hidden_for_netease', False):
                        if _nid in ("progress", "progress_time"):
                            continue  # NetEase: keep hidden
                    _node.visible = True
                    _needs_restore = True
            if _needs_restore:
                _cover = self.scene.get("cover")
                if _cover:
                    _cover.data.setdefault("spin", {})["enabled"] = True
                self.scene.resolve_layout(self.engine.width, self.engine.height)
                self.scene.mark_dirty()

        # ★ Grace period after manual show — don't auto-hide while user
        # is looking at the overlay.  Suppresses both song_end and auto_hide.
        _in_grace = time.time() < getattr(self, '_manual_show_grace_until', 0)

        # Handle song end (VISIBLE → ENDING)
        # ★ When stay_forever is ON, keep overlay visible even if
        # source disappears — user explicitly wants persistent display.
        if (st == State.VISIBLE and not is_playing
                and self.progress.position > 0
                and not self._stay_forever
                and not _in_grace):
            self._state.transition("song_end")

        # Auto-hide check (VISIBLE → HIDING)
        if (st == State.VISIBLE and not self._stay_forever
                and not self.timeline.is_playing
                and not _in_grace):
            elapsed = time.time() - self._state.context.get('_visible_since', 0)
            if elapsed > self._stay_seconds:
                self._state.transition("auto_hide")

        # Periodic snapshot save
        if int(time.time()) % 2 == 0 and self.progress.position > 5:
            save_snapshot(
                self.reader.song_name,
                self.reader.artist_name,
                self.progress.position,
            )

    # ================================================================
    # State Machine Callbacks
    # ================================================================

    def _on_enter_hidden(self) -> None:
        """State HIDDEN: window is hidden, no rendering, no data polling."""
        self._active_data = None
        self.engine.hide()

    def _on_enter_visible(self) -> None:
        """State VISIBLE: window is shown, normal data polling active."""
        self._state.context['_visible_since'] = time.time()
        # ★ Clear _active_data — stable state uses live reader data.
        self._active_data = None
        # ★ Clear progress-zero gate (Bug #181) — transition is complete,
        # live progress is now safe to display.
        self._force_progress_zero = False
        # ★ Grace period after entering VISIBLE — prevents song_end from
        # firing immediately if SMTC/window detection hasn't stabilized yet
        self._manual_show_grace_until = time.time() + 2.0

    def _on_exit_visible(self) -> None:
        """Leaving VISIBLE — record position + actual duration."""
        if self.progress.position > 0:
            record_position(
                self.reader.song_name, self.reader.artist_name,
                self.progress.position,
                duration=self.progress.duration)
            # ★ Also record to SMTC features for long-song detection
            try:
                from core.smtc_features import record_max_position
                record_max_position(
                    self.reader.song_name, self.reader.artist_name,
                    self.progress.position)
            except Exception:
                pass

    # ================================================================
    # Transition Handlers
    # ================================================================

    def _handle_appear(self, new_state: State, **ctx) -> None:
        """HIDDEN → SHOWING: first song detected, show with cover-aware appear."""
        _trace(f"handle_appear: cover_ready={self.reader.has_cover_ready()} "
               f"song={self.reader.song_name}")
        # ★ Bug #181: reset progress BEFORE the appear transition.
        # Without this, _provide_data() falls through to the live path
        # (no _active_data set during appear) and returns stale progress
        # from the old song (near-end → 90% full arc).  The appear
        # transition is always data-driven — a NEW song was detected
        # while hidden — so resetting progress to 0 is always correct.
        # (_handle_manual_show is user-triggered and does NOT reset.)
        self.progress.reset()
        # Restore all elements + spin (may be hidden/disabled from auto-show).
        # ★ Respect _timeline_hidden_for_netease.
        for _nid in ("title", "artist", "lyrics", "progress", "progress_time"):
            _node = self.scene.get(_nid)
            if _node:
                if getattr(self, '_timeline_hidden_for_netease', False):
                    if _nid in ("progress", "progress_time"):
                        continue
                    _node.visible = True
                else:
                    _node.visible = True
        _cv = self.scene.get("cover")
        if _cv: _cv.data.setdefault("spin", {})["enabled"] = True
        self.compositor.clear_caches()
        self.compositor.reset_progress_alpha()
        self.compositor.reset_rotation()
        self.engine.show()
        self.engine.alpha = 0.0

        # ★ Bug #181 fix 9: release the progress-zero gate BEFORE the
        # fade-in begins, not after VISIBLE entry.  wait_until(cover_ready)
        # can take 3-4 seconds during which the window is transparent
        # (alpha=0).  Holding _force_progress_zero throughout makes the
        # user see 0:00 → jump-to-real during the fade-in.  Clearing the
        # gate here lets the progress tracker show the actual song position
        # (which may be several seconds in) as the cover fades in.
        anim = self.timeline.sequence(
            self.timeline.wait_until(
                lambda: self.reader.has_cover_ready(), timeout=self.transitions.loading_timeout),
            self.timeline.call(lambda: setattr(self, '_force_progress_zero', False)),
            self.transitions.appear(),
        )
        self.timeline.play(anim)

    def _handle_manual_show(self, new_state: State, **ctx) -> None:
        """HIDDEN → SHOWING: tray/context menu show, fade in immediately."""
        _trace("handle_manual_show")
        self.compositor.clear_caches()
        self.compositor.reset_progress_alpha()
        self.engine.show()
        self._manual_show_grace_until = time.time() + 5.0

        # ★ If no music data, hide non-cover elements and disable spin.
        # When music data IS available, respect _timeline_hidden_for_netease
        # (NetEase has no SMTC timeline — progress/time should stay hidden).
        _has_data = bool(self.reader.song_name)
        _netease_hide = getattr(self, '_timeline_hidden_for_netease', False)
        for _nid in ("title", "artist", "lyrics", "progress", "progress_time"):
            _node = self.scene.get(_nid)
            if _node:
                if _netease_hide and _nid in ("progress", "progress_time"):
                    _node.visible = False  # NetEase: keep hidden
                else:
                    _node.visible = _has_data
        if not _has_data:
            _cover = self.scene.get("cover")
            if _cover:
                _cover.data.setdefault("spin", {})["enabled"] = False

        # ★ Startup auto-show: skip fade, use full alpha immediately
        if getattr(self, '_auto_show_no_fade', False):
            self._auto_show_no_fade = False
            self.engine.alpha = self.config.get("window", {}).get("opacity", 0.85)
        else:
            self.engine.alpha = 0.0
            anim = self.transitions.appear()
            self.timeline.play(anim)

    def _handle_manual_hide(self, new_state: State, **ctx) -> None:
        """any → HIDDEN: tray/context menu hide."""
        _trace("handle_manual_hide")
        self.engine.hide()

    def _handle_song_change(self, new_state: State, **ctx) -> None:
        """VISIBLE → TRANSITIONING: song switch with fade_out → load → fade_in.

        ★ Rewritten 2026-06-27: uses _active_data buffer instead of
        context['snapshot'] + _prev_cover_image fallback.  The active
        data is frozen ONCE here (Phase 1 fade_out), cleared during
        Phase 2 (transparent), then atomically swapped to new data at
        Phase 3 entry (fade_in).  No fallback chains, no stale leaks.
        """
        old_data = ctx.get("snapshot")
        _trace(f"handle_song_change: old_song={old_data.get('song_name','') if old_data else 'None'}")

        # ★ Start cover spin deceleration
        try:
            self.compositor.start_song_end_spin()
            if hasattr(self.engine, '_painter'):
                self.engine._painter.start_song_end_spin()
            elif hasattr(self.engine, 'painter'):
                self.engine.painter.start_song_end_spin()
        except Exception as e:
            _window_logger.warn(f"start_song_end_spin failed: {e}")

        # ★ Phase 1 freeze: capture current display as _active_data.
        # During fade_out the renderer shows THIS data — the old song's
        # cover/text/lyrics (visual continuity during fade-out).
        #
        # ★ Bug #181: progress is zeroed here, NOT preserved from the old
        # song.  The old song's near-end progress (e.g. 90% full arc) has
        # no relevance to the new song.  Freezing it would show a full arc
        # bar during fade_out, then an empty bar during fade_in — a jarring
        # "full→empty" flash across the transition.  Zeroing it means the
        # arc simply isn't drawn during the transition (dur=0 guard → early
        # return) and appears naturally from 0% as the new song plays.
        if old_data:
            self._active_data = {
                "cover_image": old_data.get("cover_image"),
                "song_name": old_data.get("song_name", ""),
                "artist_name": old_data.get("artist_name", ""),
                "lyrics_timed": list(old_data.get("lyrics_timed", [])),
                "lyrics_merged": list(old_data.get("lyrics_merged", [])),
                "progress_pos": 0,
                "progress_dur": 0,
                "cover_fx": self._get_cover_fx(),
            }
        else:
            self._active_data = {
                "cover_image": self.reader.cover_image,
                "song_name": self.reader.song_name or "",
                "artist_name": self.reader.artist_name or "",
                "lyrics_timed": list(self.reader.lyrics_timed) if self.reader.lyrics_timed else [],
                "lyrics_merged": list(self.reader.lyrics_merged) if self.reader.lyrics_merged else [],
                "progress_pos": 0,
                "progress_dur": 0,
                "cover_fx": self._get_cover_fx(),
            }

        # ★ Reset progress immediately — before Phase 1 fade_out even
        # starts.  Prevents the old song's duration from leaking into
        # _provide_data() during the transition.
        self.progress.reset()

        # Force immediate SMTC poll for new song
        if self._smtc_engine:
            self._smtc_engine.force_poll()

        # Start transition.
        _is_source_switch = getattr(self, '_source_switch_active', False)
        self._source_switch_active = False
        self._in_source_switch = _is_source_switch
        _is_quick = getattr(self, '_quick_song_change', False)
        self._quick_song_change = False

        if _is_source_switch:
            anim = self.transitions.source_switch(
                on_load=lambda: self._load_new_song(),
                cover_ready=lambda: self.reader.has_cover_ready(),
                on_phase3_start=lambda: self._swap_to_new_data(),
            )
        elif _is_quick:
            tl = AnimationTimeline()
            anim = tl.sequence(
                tl.call(lambda: self._load_new_song()),
                tl.parallel(
                    tl.wait(0.15),
                    tl.wait_until(lambda: self.reader.has_cover_ready(), timeout=self.transitions.loading_timeout),
                ),
                tl.call(lambda: self._swap_to_new_data()),
                tl.to(self.engine, "alpha", self.transitions.target_alpha,
                      self.transitions.fade_in_ms, "ease-out"),
            )
        else:
            anim = self.transitions.song_change(
                on_load=lambda: self._load_new_song(),
                cover_ready=lambda: self.reader.has_cover_ready(),
                on_phase3_start=lambda: self._swap_to_new_data(),
            )
        self.timeline.play(anim)

    def _handle_song_end(self, new_state: State, **ctx) -> None:
        """VISIBLE → ENDING: song ended, fade out then hide."""
        _trace("handle_song_end")
        # ★ Freeze current display for the fade-out.
        # No snapshot in context — _provide_data() reads _active_data.
        self._active_data = {
            "cover_image": self.reader.cover_image,
            "song_name": self.reader.song_name or "",
            "artist_name": self.reader.artist_name or "",
            "lyrics_timed": list(self.reader.lyrics_timed) if self.reader.lyrics_timed else [],
            "lyrics_merged": list(self.reader.lyrics_merged) if self.reader.lyrics_merged else [],
            "progress_pos": self.progress.position,
            "progress_dur": self.progress.duration,
            "cover_fx": self._get_cover_fx(),
        }
        anim = self.timeline.sequence(
            self.transitions.song_end(),
            self.timeline.wait(0.5),
            self.timeline.call(lambda: None),
        )
        self.timeline.play(anim)

    def _handle_auto_hide(self, new_state: State, **ctx) -> None:
        """VISIBLE → HIDING: auto-hide timer, fade out then hide."""
        _trace("handle_auto_hide")
        anim = self.timeline.sequence(
            self.transitions.disappear(),
            self.timeline.wait(0.3),
            self.timeline.call(lambda: None),  # No-op; 'completed' does the hide
        )
        self.timeline.play(anim)

    def _handle_completed(self, new_state: State, **ctx) -> None:
        """Transition finished — update stable state based on which state we came from."""
        _trace(f"handle_completed → {new_state.value}")

    def _handle_error(self, new_state: State, **ctx) -> None:
        """Error recovery — force to HIDDEN with cleanup."""
        _trace("handle_error: forcing HIDDEN with cleanup")
        self.timeline.stop()
        self.engine.hide()
        self.engine.alpha = 0.0

    # ================================================================
    # Song-change helper (called mid-transition by song_change Phase 2)
    # ================================================================

    def _load_new_song(self) -> None:
        """Phase 2: prepare for new song while window is at alpha=0.

        Resets progress, rotation, element visibility.  Does NOT touch
        _active_data — the old data stays frozen for the duration of
        Phase 2 (transparent gap).  _swap_to_new_data() will atomically
        replace _active_data at Phase 3 entry.
        """
        # Restore all elements
        for _nid in ("title", "artist", "lyrics", "progress", "progress_time"):
            _node = self.scene.get(_nid)
            if _node:
                if getattr(self, '_timeline_hidden_for_netease', False):
                    if _nid in ("progress", "progress_time"):
                        continue
                    _node.visible = True
                else:
                    _node.visible = True
        _cover = self.scene.get("cover")
        if _cover:
            _cover.data.setdefault("spin", {})["enabled"] = True

        self.progress.reset()
        self.compositor.reset_progress_alpha()
        self.compositor.reset_rotation()
        if hasattr(self.engine, '_painter'):
            self.engine._painter.reset_rotation()
        elif hasattr(self.engine, 'painter'):
            self.engine.painter.reset_rotation()
        clear_snapshot(self.reader.song_name, self.reader.artist_name)
        self.scene.resolve_layout(self.engine.width, self.engine.height)
        _trace(f"_load_new_song: new song={self.reader.song_name} "
               f"cover_ready={self.reader.has_cover_ready()}")

    def _swap_to_new_data(self) -> None:
        """Phase 3 entry: atomically swap _active_data to new song.

        Called AFTER cover_ready returns True and BEFORE fade_in begins.
        Replaces the frozen old-song metadata with new-song metadata in
        a single assignment — there is no intermediate frame where the
        renderer can see mixed old/new data.  Progress is always live.

        ★ No fallback chains.  No _prev_cover_image.  No context dict.
        If the cover isn't ready, the default placeholder is used — it
        is honest signaling ("loading") vs. showing wrong (old) art.
        """
        self.progress.update(
            self.reader.duration_seconds or 0,
            self.reader.song_name,
            self.reader.artist_name)

        idx = self.config.get("behavior", {}).get("cover_source_index", 0)
        auto_idx = getattr(self.reader, 'auto_cover_index', -1)
        if idx == 0:
            idx = auto_idx
        cover = self.reader.get_selected_cover(idx)

        # If the selected cover is the default placeholder, try the
        # live SMTC thumbnail as a last resort.  Do NOT fall back to
        # any previous-song data — that would be a "flashback".
        if cover is not None and not getattr(self, '_in_source_switch', False):
            try:
                _default = self.reader._get_default_cover()
                if cover == _default and self._smtc_engine:
                    _live = self._smtc_engine.thumbnail
                    if _live is not None:
                        cover = _live
                        _window_logger.debug(
                            f"_swap_to_new_data: using live SMTC thumbnail "
                            f"(song={self.reader.song_name!r})")
            except Exception:
                pass

        # ★ Atomic swap: one assignment, no gap.
        self._active_data = {
            "cover_image": cover,
            "song_name": self.reader.song_name or "",
            "artist_name": self.reader.artist_name or "",
            "lyrics_timed": list(self.reader.lyrics_timed) if self.reader.lyrics_timed else [],
            "lyrics_merged": list(self.reader.lyrics_merged) if self.reader.lyrics_merged else [],
            "progress_pos": self.progress.position,
            "progress_dur": self.progress.duration,
            "cover_fx": self._get_cover_fx(),
        }
        _trace(f"_swap_to_new_data: cover_id={id(cover)} "
               f"song={self.reader.song_name!r}")

    # ================================================================
    # Engine callbacks (wired in __init__)
    # ================================================================

    def _on_open_settings(self) -> None:
        """Open the settings window."""
        if self._settings_callback:
            self._settings_callback()

    def _on_exit(self) -> None:
        """Exit the application."""
        self.close()

    def _on_topmost_toggle(self) -> None:
        """Toggle window always-on-top."""
        new_v = not self.config.get("window", {}).get("always_on_top", True)
        self.config.setdefault("window", {})["always_on_top"] = new_v
        self.engine.set_always_on_top(new_v)
        save_config(self.config)
        if self._tray_refresh:
            self._tray_refresh()

    def _on_clickthrough_toggle(self) -> None:
        """Toggle click-through mode."""
        new_v = not self.config.get("window", {}).get("click_through", False)
        self.config.setdefault("window", {})["click_through"] = new_v
        self.engine.set_click_through(new_v)
        save_config(self.config)
        if self._tray_refresh:
            self._tray_refresh()

    def _on_element_toggle(self, eid: str) -> None:
        """Toggle visibility of a scene element."""
        node = self.scene.get(eid)
        if node:
            node.visible = not node.visible
            self.scene.mark_dirty()

    # ================================================================
    # Config reload
    # ================================================================

    def reload_config(self, new_config: dict) -> None:
        """Hot-reload configuration."""
        self.config = new_config
        self.transitions = TransitionFactory(new_config, compositor=self.compositor, engine=self.engine)

        anim_cfg = new_config.get("animation", {})
        self._stay_forever = anim_cfg.get("stay_forever", False)
        self._stay_seconds = anim_cfg.get("stay_seconds", 8)

        # Update compositor background
        self.compositor.update_config(new_config)

        # Update canvas size and resize window
        size_cfg = new_config.get("window", {}).get("size", {})
        new_w = max(200, min(1500, int(size_cfg.get("width", 400))))
        new_h = max(200, min(1500, int(size_cfg.get("height", 550))))
        if new_w != self.engine.width or new_h != self.engine.height:
            old_w, old_h = self.engine.width, self.engine.height
            # ★ Proportionally scale all element offsets so positions
            # stay visually consistent when the canvas resizes.
            if old_w > 0 and old_h > 0:
                sx = new_w / old_w
                sy = new_h / old_h
                for node in self.config.get("scene", {}).get("nodes", []):
                    off = node.get("offset", {})
                    if "x" in off:
                        off["x"] = round(off["x"] * sx, 1)
                    if "y" in off:
                        off["y"] = round(off["y"] * sy, 1)
                save_config(self.config)  # persist scaled offsets
                _window_logger.debug(
                    f"canvas resize {old_w}x{old_h} → {new_w}x{new_h} "
                    f"(scale x{sx:.2f} y{sy:.2f}, offsets scaled)")
            self.engine.width = new_w
            self.engine.height = new_h
            if self.engine.root:
                self.engine.root.geometry(f"{new_w}x{new_h}")

        # ★ If user re-enabled progress in settings, clear the startup hide flag
        for node in new_config.get("scene", {}).get("nodes", []):
            if node.get("id") == "progress" and node.get("mode", "off") != "off":
                self._no_timeline_force_hide = False
                self._no_timeline_prompted = False
                break

        # Rebuild scene
        self.scene.load_from_config(new_config.get("scene", {}))
        self.scene.resolve_layout(self.engine.width, self.engine.height)

        # Update window properties
        self.engine.set_opacity(
            new_config.get("window", {}).get("opacity", 0.85))
        bg_cfg = new_config.get("window", {}).get("background", {})
        self.engine.set_background(
            bg_cfg.get("color", "#1a1a2e"), bg_cfg.get("opacity", 1.0))
        self.engine.set_click_through(
            new_config.get("window", {}).get("click_through", False))
        self.engine.set_always_on_top(
            new_config.get("window", {}).get("always_on_top", True))

        # ★ Sync engine animation parameters from config (hot-reload).
        # Without this, FPS cap / eco mode changes only take effect after restart.
        _cont_mode = anim_cfg.get("continuous_fps_mode", "eco")
        _cont_cap_raw = anim_cfg.get("continuous_fps_cap", 45)
        try:
            _cont_cap = int(str(_cont_cap_raw).split()[0])
        except (ValueError, IndexError):
            _cont_cap = 45
        _cont_cap = max(30, min(60, _cont_cap))
        self.engine._continuous_fps = self.engine.target_fps if _cont_mode == "full" else min(_cont_cap, self.engine.target_fps)
        self.engine._fullscreen_eco_mode = anim_cfg.get("fullscreen_eco_mode", "pause")
        self.engine.target_fps = int(anim_cfg.get("fps", 60))
        self.engine._active_fps = self.engine.target_fps
        self.engine._idle_fps = 10

        # Update detection source on the reader (hot-swap player preference)
        priority = new_config.get("behavior", {}).get("detection_priority", ["qqmusic", "netease"])
        try:
            self.reader.set_detection_priority(priority)
            if self._smtc_engine:
                self._smtc_engine.set_priority(priority)
        except Exception as e:
            _window_logger.warn(f"set_detection_priority failed: {e}")

        # Update lyrics source priority on the reader (hot-swap)
        lyrics_src = new_config.get("behavior", {}).get("lyrics_source_priority", "qq_first")
        try:
            self.reader.set_lyrics_source_priority(lyrics_src)
        except Exception as e:
            _window_logger.warn(f"set_lyrics_source_priority failed: {e}")

        # ★ Force full-speed rendering so changes appear immediately.
        # Without this, adaptive FPS may stay in idle/skip mode and delay
        # the visual update until the next song change or user interaction.
        self.engine._signal_activity(3.0)
        self.scene.mark_dirty()

        # Notify tray to refresh checkboxes
        if self._tray_refresh:
            self._tray_refresh()

    # ================================================================
    # Data provider
    # ================================================================

    def _provide_data(self) -> dict:
        """Provide current song data to the render engine.

        ★ Tray queue processed HERE (every draw frame) for redundancy —
        if _on_frame_update fails, draw path still drains commands.
        """
        self._process_tray_queue()

        # ★ Two-mode design (rewritten 2026-06-27):
        #
        # TRANSITION mode (SHOWING/TRANSITIONING/HIDING/ENDING):
        #   - Song metadata (cover, title, artist, lyrics) comes from
        #     _active_data — explicitly frozen at transition start and
        #     atomically swapped at Phase 3 entry.
        #   - Progress (position, duration, is_paused) is ALWAYS live
        #     from the progress tracker.
        #   - If _active_data is None (brief gap during appear before
        #     first cover loads), falls through to live reader data.
        #
        # STABLE mode (VISIBLE / HIDDEN):
        #   - All data comes live from the reader and progress tracker.
        #   - _active_data is None (cleared on state entry).

        # ★ Timeline update + completion detection also run here (every draw
        # frame) for redundancy.  If _on_frame_update's scheduler path is
        # broken, the draw path still advances animations and completes them.
        if self.timeline.is_playing:
            self.timeline.update(1.0 / max(1, self.engine.target_fps))
        if self._state.is_transitioning() and not self.timeline.is_playing:
            self._state.transition("completed")

        # Diagnostic: log state machine status
        _dc = getattr(self, '_provide_diag_ct', 0) + 1
        self._provide_diag_ct = _dc
        if _dc <= 5 or _dc % 120 == 1:
            _window_logger.debug(
                f"PROVIDE #{_dc}: state={self._state.state.name} "
                f"trans={self._state.is_transitioning()} "
                f"active_data={self._active_data is not None} "
                f"live_pos={self.progress.position:.1f}")

        # ---- Transition path ----
        if self._state.is_transitioning() and self._active_data is not None:
            _pos = self._active_data.get("progress_pos", 0)
            _dur = self._active_data.get("progress_dur", 0)
            _window_logger.rate_limited(
                "provide_progress_snapshot", 2.0, "INFO",
                f"PROVIDE_PROGRESS source=snapshot state={self._state.state.name} "
                f"out_pos={float(_pos or 0.0):.1f}s out_dur={float(_dur or 0.0):.1f}s "
                f"live_pos={self.progress.position:.1f}s live_dur={self.progress.duration:.1f}s "
                f"paused={self.progress.is_paused}")
            return {
                # ★ Frozen song metadata + progress — the snapshot
                # captured in _handle_song_change (Phase 1) or
                # _swap_to_new_data (Phase 3).  No live data leaks.
                "cover_image": self._active_data.get("cover_image"),
                "song_name": self._active_data.get("song_name", ""),
                "artist_name": self._active_data.get("artist_name", ""),
                "lyrics_timed": self._active_data.get("lyrics_timed", []),
                "lyrics_merged": self._active_data.get("lyrics_merged", []),
                "progress_pos": _pos,
                "progress_dur": _dur,
                "progress": (_pos / _dur) if _dur > 0 else 0.0,
                "is_paused": self.progress.is_paused,
                "is_animating": True,  # transition = always animating
                "cover_spinning": False,  # frozen cover during transition
                "no_timeline": (
                    self.progress.no_timeline
                    and getattr(self, '_no_timeline_force_hide', True)
                ),
                "cover_fx": self._active_data.get("cover_fx", {}),
            }

        # ---- Stable-state path ----
        # Update shared status dict for tray tooltip
        if self._status is not None:
            self._status["song"] = self.reader.song_name or ""
            self._status["artist"] = self.reader.artist_name or ""
            self._status["state"] = "playing" if self.reader.is_playing else "idle"

        # Cover selection
        cfg_idx = self.config.get("behavior", {}).get("cover_source_index", 0)
        auto_idx = getattr(self.reader, 'auto_cover_index', -1)
        if cfg_idx == 0:
            idx = auto_idx
        else:
            pref_name = self.config.get("behavior", {}).get("cover_source_name", "")
            idx = auto_idx
            if pref_name:
                candidates = self.reader.cover_candidates
                for i, (name, _) in enumerate(candidates):
                    if name == pref_name:
                        idx = i
                        break
        cover = self.reader.get_selected_cover(idx)

        # ★ Increment progress_alpha during stable VISIBLE state
        if (self._state.state == State.VISIBLE
                and self.progress.position > 0):
            self.compositor.tick_progress_alpha()

        _pos = self.progress.position
        _dur = self.progress.duration
        # ★ Bug #181 render-level gate: force progress=0 from song-change
        # detection until VISIBLE state entry.  This is the final defense
        # against the _on_draw / _on_frame_update race — regardless of
        # which runs first, the renderer will never see stale progress.
        if self._force_progress_zero:
            _pos = 0.0
            _dur = 0.0
        # ★ Diag: log when force_progress_zero gate is active (indicates
        # a song-change transition in progress).  At DEBUG, periodic
        # progress snapshots for freeze diagnosis.
        _pdiag = getattr(self, '_prog_diag_ct', 0) + 1
        self._prog_diag_ct = _pdiag
        if self._force_progress_zero:
            _window_logger.info(
                f"PROG: gate active pos={_pos:.1f}s dur={_dur:.1f}s "
                f"fresh_reset={self.progress._fresh_reset} "
                f"state={self._state.state.value}")
        elif _pdiag % 180 == 1:
            _window_logger.debug(
                f"PROG: pos={_pos:.1f}s dur={_dur:.1f}s "
                f"progress={(_pos/_dur*100) if _dur > 0 else 0:.0f}% "
                f"state={self._state.state.value}")
        _lyrics_t = self.reader.lyrics_timed
        _lyrics_m = self.reader.lyrics_merged
        # ★ Diagnostic: track lyrics data flow in stable state
        _lx_diag = getattr(self, '_lx_data_diag_frame', 0) + 1
        self._lx_data_diag_frame = _lx_diag
        if _lx_diag % 300 == 1:
            _window_logger.debug(
                f"LX_DATA: pos={_pos:.1f}s dur={_dur:.1f}s "
                f"timed_len={len(_lyrics_t)} merged_len={len(_lyrics_m)} "
                f"timed_first={_lyrics_t[0] if _lyrics_t else 'nil'} "
                f"paused={self.progress.is_paused}")
        _window_logger.rate_limited(
            "provide_progress_live", 2.0, "INFO",
            f"PROVIDE_PROGRESS source=live state={self._state.state.name} "
            f"out_pos={float(_pos or 0.0):.1f}s out_dur={float(_dur or 0.0):.1f}s "
            f"live_pos={self.progress.position:.1f}s live_dur={self.progress.duration:.1f}s "
            f"force_zero={self._force_progress_zero} paused={self.progress.is_paused} "
            f"no_timeline={self.progress.no_timeline}")
        return {
            "cover_image": cover,
            "song_name": self.reader.song_name,
            "artist_name": self.reader.artist_name,
            "lyrics_timed": _lyrics_t,
            "lyrics_merged": _lyrics_m,
            "progress_pos": _pos,
            "progress_dur": _dur,
            "progress": (_pos / _dur) if _dur > 0 else 0.0,
            "is_paused": self.progress.is_paused,
            "is_animating": self._state.is_transitioning(),
            "cover_spinning": (
                not self.progress.is_paused
                and cover is not None
                and self.progress.position > 0
            ),
            "no_timeline": (
                self.progress.no_timeline
                and getattr(self, '_no_timeline_force_hide', True)
            ),
            "cover_fx": self._get_cover_fx(),
        }

    # ================================================================
    # Helpers
    # ================================================================

    def _get_cover_fx(self) -> dict:
        """Read cover visual effects config from current config."""
        nodes = self.config.get("scene", {}).get("nodes", [])
        for n in nodes:
            if n.get("id") == "cover":
                return n.get("effects", {})
        return {}

    # ================================================================
    # Tray commands
    # ================================================================

    def _process_tray_queue(self) -> None:
        """Process commands from the system tray thread.

        Called from both _on_frame_update (pyglet clock) and _on_draw
        (GL render path) for redundancy — if one scheduler path fails,
        the other still drains the tray queue.

        ★ Also called from engine._scheduled_update via _handle_tray_command
        (the _gl_queue bypass path) — that's the most reliable path because
        it runs at the TOP of every frame, before any other work.
        """
        if self._tray_queue is None:
            return
        _processed = 0
        _heartbeat = getattr(self, '_tray_queue_heartbeat', 0)
        _heartbeat += 1
        self._tray_queue_heartbeat = _heartbeat
        # ★ Heartbeat every ~60s (3600 frames at 60fps) — confirms
        # _process_tray_queue IS being called without flooding logs.
        if _heartbeat % 3600 == 1:
            _window_logger.debug(
                f"tray_queue alive (beat #{_heartbeat}, "
                f"state={self._state.state.value})")
        try:
            while True:
                cmd = self._tray_queue.get_nowait()
                _processed += 1
                try:
                    self._handle_tray_command(cmd)
                except Exception:
                    import traceback as _tb
                    _window_logger.error(
                        f"tray cmd {cmd!r} crashed:\n{_tb.format_exc()}")
        except queue.Empty:
            pass
        if _processed:
            _window_logger.debug(f"tray_queue processed {_processed} cmds")

    def _handle_tray_command(self, cmd: str) -> None:
        """Process a single tray command. Also called from engine's
        _gl_queue bypass path (the most reliable dispatch mechanism)."""
        _window_logger.info(f"handle_tray_command: {cmd!r} state={self._state.state.value}")
        if cmd == "toggle_clickthrough":
            new_ct = not self.config.get("window", {}).get("click_through", False)
            self.config.setdefault("window", {})["click_through"] = new_ct
            self.engine.set_click_through(new_ct)
            save_config(self.config)
            if self._tray_refresh:
                self._tray_refresh()
            self._notify_settings_ui()
        elif cmd == "toggle_topmost":
            new_top = not self.config.get("window", {}).get("always_on_top", True)
            self.config.setdefault("window", {})["always_on_top"] = new_top
            self.engine.set_always_on_top(new_top)
            save_config(self.config)
            if self._tray_refresh:
                self._tray_refresh()
            self._notify_settings_ui()
        elif cmd == "show_window":
            self._suppress_auto_show = False  # user explicitly requested show
            self._state.transition("manual_show")
        elif cmd == "hide_window":
            self._suppress_auto_show = True   # suppress all auto-show paths
            self._state.transition("manual_hide")
        elif cmd == "open_settings":
            _on_settings = getattr(self.engine, '_on_settings', None)
            if _on_settings:
                _on_settings()
        elif cmd == "do_close":
            self.close()

    def _notify_settings_ui(self) -> None:
        """If the settings window is open, refresh its UI to reflect
        config changes made via tray toggles (bidirectional sync).

        ★ Thread-safe: defers Tk operations to the tk_queue when called
        from a non-Tk thread (e.g., tray daemon thread via nuclear option).
        """
        sw = getattr(self, '_settings_window', None)
        if sw is not None:
            try:
                # Tkinter is NOT thread-safe — push to tk_queue if needed
                self.engine._tk_queue.put(lambda: sw.refresh_from_config())
            except Exception:
                pass
