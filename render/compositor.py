"""Layer compositor — PIL-based offscreen rendering.

Renders the scene graph to a PIL RGBA image each frame.
No OpenGL required — uses PIL ImageDraw for all drawing.

Prefer pillow-simd if installed (~2-4× faster resize/rotate/composite).
"""
# ★ Try pillow-simd first (SIMD-accelerated PIL drop-in)
try:
    import pillow_simd  # noqa: F401 — force-load as PIL replacement
except ImportError:
    pass
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageColor
from scene.graph import SceneGraph
from render.spin_controller import SpinController
import math


class Compositor:
    """Per-frame render orchestrator using PIL."""

    def __init__(self, config: dict):
        self.config = config
        bg_cfg = config.get("window", {}).get("background", {})
        self.bg_color = bg_cfg.get("color", "#1a1a2e")
        self.bg_opacity = bg_cfg.get("opacity", 1.0)

        # Font cache
        self._fonts: dict[tuple, ImageFont.FreeTypeFont] = {}

        # Cover cache
        self._cover_cache: dict[str, Image.Image] = {}
        self._mask_cache: dict[str, Image.Image] = {}
        # Cached SS-masked thumbnails: (id(cover), size, shape, radius, SS) → masked RGBA
        self._ss_thumb_cache: dict[tuple, Image.Image] = {}
        # ★ Cached rounded-rect perimeters (geometry-only, see _rect_perimeter_pts)
        self._rect_perimeter_cache: dict[tuple, list] = {}
        # Pre-allocated buffers (reused every frame to avoid allocation)
        self._ss_progress_buf: Image.Image | None = None
        self._ss_progress_buf_size: tuple[int, int] | None = None
        # ★ Frame buffer reuse — avoids ~4 MB allocation per frame at 900×600
        self._frame_buf: Image.Image | None = None
        self._frame_buf_size: tuple[int, int] | None = None

        # ★ Text render cache — keyed by (text, font_family, font_size, bold, color, max_w, align, outline_color, outline_w)
        # Stores pre-rendered RGBA images of text with outline, avoiding 9+ draw.text() calls per frame
        self._text_img_cache: dict[tuple, Image.Image] = {}

        # ★ Shared spin controller (eliminates duplication with GL painter)
        self._spin = SpinController()
        self._progress_alpha = 0.0  # 0=溶解到背景, 1=完全可见
        self._last_frame: Image.Image | None = None  # 缓存上一帧
        self._frozen_frame: Image.Image | None = None  # cover_out 冻结帧

        # Current data (set before each frame)
        self._cover_image = None
        self._song_name = ""
        self._artist_name = ""
        self._lyrics_timed = []
        self._progress_pos = 0.0
        self._progress_dur = 0.0
        self._no_timeline = False

        # ★ Per-phase profiling accumulators (logged every 2s by engine)
        self._prof_init = 0.0     # frame buffer setup
        self._prof_cover = 0.0     # cover rendering
        self._prof_text = 0.0      # text rendering
        self._prof_lyrics = 0.0    # lyrics rendering
        self._prof_progress = 0.0  # progress bar/time rendering
        self._prof_blend = 0.0     # opacity blending
        self._prof_frame_count = 0

    def update_config(self, config: dict) -> None:
        """Refresh background settings from config (called by reload_config)."""
        self.config = config
        bg_cfg = config.get("window", {}).get("background", {})
        self.bg_color = bg_cfg.get("color", "#1a1a2e")
        self.bg_opacity = bg_cfg.get("opacity", 1.0)

    def set_data(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, f"_{key}", value)

    def render_to_pil(self, scene: SceneGraph, data: dict,
                      width: int, height: int) -> Image.Image:
        """Render one complete frame to a PIL Image."""
        import time as _time
        _t_total_start = _time.time()

        # ★ cover_out freeze mode: return cached frame, no re-render
        if self._frozen_frame is not None:
            return self._frozen_frame

        self.set_data(**data)

        # Guard against float values from config (DoubleVar sliders)
        width = int(width)
        height = int(height)

        # Resolve layout
        if scene.layout_dirty:
            scene.resolve_layout(width, height)

        # 1. Background
        _bg_alpha = int(self.bg_opacity * 255)
        bg_rgba = ImageColor.getrgb(self.bg_color) + (_bg_alpha,)
        buf_key = (width, height)
        if self._frame_buf is None or self._frame_buf_size != buf_key:
            self._frame_buf = Image.new("RGBA", buf_key, bg_rgba)
            self._frame_buf_size = buf_key
            self._frame_draw = ImageDraw.Draw(self._frame_buf)
        else:
            # ★ Fast clear: reuse draw object, draw full-coverage rectangle
            self._frame_draw.rectangle((0, 0, width, height), fill=bg_rgba)
        img = self._frame_buf
        draw = self._frame_draw
        _t_init = _time.time()

        # 2. Nodes — always render (cover placeholder needs to show even
        # without music when stay_forever is enabled)
        _t_cover = _t_text = _t_lyrics = _t_progress = _t_blend = 0.0
        for node in scene.render_order():
            try:
                _t0 = _time.time()
                self._render_node(img, draw, node, width, height, scene)
                _dt = _time.time() - _t0
                nt = node.node_type
                if nt == "cover":
                    _t_cover += _dt
                elif nt == "text":
                    _t_text += _dt
                elif nt == "synced_lyrics":
                    _t_lyrics += _dt
                elif nt in ("progress_bar", "progress_time"):
                    _t_progress += _dt
            except Exception as e:
                import traceback, os, datetime
                try:
                    log_path = os.path.join(os.environ.get("TEMP", "."), "qqoverlay_ng.log")
                    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"[{ts}] [Compositor] {node.id} error: {e}\n{traceback.format_exc()}\n")
                except Exception:
                    pass
        self._prof_cover += _t_cover
        self._prof_text += _t_text
        self._prof_lyrics += _t_lyrics
        self._prof_progress += _t_progress
        self._prof_init += (_t_init - _t_total_start)

        self._prof_frame_count += 1
        self._last_frame = img
        return img

    # ================================================================
    # Node render dispatch
    # ================================================================

    def _render_node(self, img: Image.Image, draw: ImageDraw.Draw,
                     node, w: int, h: int, scene: SceneGraph | None = None) -> None:
        if not node.visible or node.opacity < 0.01:
            return

        x, y = int(node.abs_x), int(node.abs_y)
        nw, nh = int(node.abs_width), int(node.abs_height)

        # Guard against invalid positions (should never happen, but catch it)
        if nw <= 0 or nh <= 0:
            return
        if x < -5000 or y < -5000 or x > 10000 or y > 10000:
            return

        # ★ Save background region BEFORE rendering the element.
        # After rendering, we blend the element with this saved background
        # using node.opacity. This is the CORRECT way to do opacity —
        # the old approach blended the element with itself (no-op).
        saved_bg = None
        bg_x = bg_y = bg_w = bg_h = 0
        if node.opacity < 0.995:
            bg_x = max(0, x)
            bg_y = max(0, y)
            bg_w = min(nw, img.width - bg_x)
            bg_h = min(nh, img.height - bg_y)
            if bg_w > 0 and bg_h > 0:
                saved_bg = img.crop((bg_x, bg_y, bg_x + bg_w, bg_y + bg_h)).copy()

        # Dispatch to specific renderer
        node_type = node.node_type
        if node_type == "cover":
            self._render_cover(img, node, x, y, nw, nh, w, h, scene)
        elif node_type == "text":
            self._render_text(img, draw, node, x, y, nw, nh)
        elif node_type == "synced_lyrics":
            self._render_lyrics(img, draw, node, x, y, nw, nh)
        elif node_type == "progress_bar":
            self._render_progress_bar(img, node, x, y, nw, nh)
        elif node_type == "progress_time":
            self._render_progress_time(img, draw, node, x, y, nw, nh)

        # ★ Blend rendered region with saved background at node opacity
        if saved_bg is not None:
            self._opacity_blend(img, saved_bg, bg_x, bg_y, bg_w, bg_h, node.opacity)

    # ================================================================
    # Cover rendering
    # ================================================================

    def _render_cover(self, img: Image.Image, node,
                      x: int, y: int, size: int, _nw: int, pw: int, ph: int,
                      scene: SceneGraph | None = None) -> None:
        cover_img = self._cover_image
        if cover_img is None:
            # ★ Cover region filled with placeholder image
            try:
                import os
                _proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                placeholder = os.path.join(_proj, "assets", "default_cover.png")
                ph = Image.open(placeholder).convert("RGBA")
                s = min(ph.size)
                left = (ph.size[0] - s) // 2
                top = (ph.size[1] - s) // 2
                ph = ph.crop((left, top, left + s, top + s))
                ph = ph.resize((size, size), Image.LANCZOS)
                # Draw subtle border around placeholder for visibility
                draw = ImageDraw.Draw(img)
                draw.rectangle((x-1, y-1, x+size+1, y+size+1), outline="#555577")
                img.paste(ph, (x, y), ph)
            except Exception:
                try:
                    draw = ImageDraw.Draw(img)
                    draw.rectangle((x, y, x + size, y + size), fill="#2a2a3e", outline="#555577")
                except Exception:
                    pass
            return

        # Crop to square
        s = min(cover_img.size)
        left = (cover_img.size[0] - s) // 2
        top = (cover_img.size[1] - s) // 2
        thumb = cover_img.crop((left, top, left + s, top + s))

        shape = node.data.get("shape", "circle")
        radius = node.data.get("border_radius", 20)
        spin = node.data.get("spin", {})
        spinning = spin.get("enabled", False)
        # ★ Diagnostic: log spin state once per cover change (rate-limited)
        _diag_key = ('spin_diag', id(cover_img))
        if spinning != getattr(self, '_diag_last_spin_enabled', None):
            self._diag_last_spin_enabled = spinning
            from core.logs import get_logger
            get_logger("compositor").info(
                f"SPIN enabled={spinning} is_paused={getattr(self,'_is_paused',False)} "
                f"song_ending={getattr(self,'_song_ending',False)} "
                f"rotation={self._cover_rotation:.1f}")
        # Feather: reinterpreted — 0 = supersampled crisp edge only,
        # >0 = additional light blur (only for non-spinning mode).
        feather_raw = max(0, float(node.data.get("feather", 5)))

        # Supersample factor — user-configurable (2X/4X/6X).
        # Higher = smoother edges but more CPU per frame.
        SS = self._parse_ss(node.data.get("supersample", "2X"))

        if spinning:
            # ★ Fast rotation: render at 1X with feathered mask.
            #    The SS mask pipeline is overkill for rotation — the
            #    rotation itself resamples the image, and a 1-2px
            #    feathered mask provides equivalent edge quality at
            #    1/10 the per-frame cost.
            #    180×180 BILINEAR rotate is ~6× faster than 360×360.
            rot_size = size

            # Resize cover to final size (one-time per cover change)
            cache_key = (id(cover_img), size, shape, radius, feather_raw)
            if cache_key in self._ss_thumb_cache:
                thumb = self._ss_thumb_cache[cache_key]
            else:
                thumb.thumbnail((rot_size, rot_size), Image.LANCZOS)
                # ★ thumbnail() never upscales. SMTC thumbnails (e.g.
                # NetEase 162×162) may be smaller than rot_size, causing
                # Image.composite to fail with "images do not match".
                # Force exact size to match mask and bg.
                if thumb.size != (rot_size, rot_size):
                    thumb = thumb.resize((rot_size, rot_size), Image.LANCZOS)
                if thumb.mode != "RGBA":
                    thumb = thumb.convert("RGBA")
                # Feathered mask at 1X for AA (replaces SS downscale)
                mask = Image.new("L", (rot_size, rot_size), 0)
                mask_draw = ImageDraw.Draw(mask)
                if shape == "circle":
                    mask_draw.ellipse((0, 0, rot_size, rot_size), fill=255)
                elif shape == "roundrect":
                    r = radius * 2
                    mask_draw.rounded_rectangle(
                        (0, 0, rot_size, rot_size), radius=r, fill=255)
                else:
                    mask_draw.rectangle((0, 0, rot_size, rot_size), fill=255)
                # Feather: 1-2px GaussianBlur on mask for edge AA
                blur_r = max(0.3, min(2.0, feather_raw / 10.0))
                mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_r))
                bg = Image.new("RGBA", (rot_size, rot_size), (0, 0, 0, 0))
                thumb = Image.composite(thumb, bg, mask)
                self._ss_thumb_cache[cache_key] = thumb
                if len(self._ss_thumb_cache) > 8:
                    self._ss_thumb_cache.pop(next(iter(self._ss_thumb_cache)))

            # Rotate at 1X — ~10× faster than the old SS pipeline
            self._update_spin(spin)
            thumb = thumb.rotate(self._cover_rotation,
                                 resample=Image.BILINEAR, expand=False)
        else:
            # No rotation — use full SS, cache the result
            cache_key = (id(cover_img), size, shape, radius, SS, feather_raw)
            if cache_key in self._ss_thumb_cache:
                thumb = self._ss_thumb_cache[cache_key]
            else:
                ss_size = size * SS
                thumb.thumbnail((ss_size, ss_size), Image.LANCZOS)
                thumb = thumb.convert("RGBA")
                thumb = self._apply_mask_ss(thumb, ss_size, shape, radius)
                thumb = thumb.resize((size, size), Image.LANCZOS)
                # Optional light feather (GaussianBlur on final image, not mask)
                if feather_raw > 0:
                    blur_r = feather_raw / 20.0
                    thumb = thumb.filter(ImageFilter.GaussianBlur(radius=min(2.0, blur_r)))
                self._ss_thumb_cache[cache_key] = thumb
                if len(self._ss_thumb_cache) > 8:
                    self._ss_thumb_cache.pop(next(iter(self._ss_thumb_cache)))

        # Ensure RGBA and correct size
        if thumb.mode != "RGBA":
            thumb = thumb.convert("RGBA")
        if thumb.size != (size, size):
            thumb = thumb.resize((size, size), Image.LANCZOS)

        # ★ NEW: Solid backplate behind cover — prevents light leak on
        # high-contrast desktop backgrounds. The backplate is the window
        # background color at full opacity, drawn in the same shape as
        # the cover. This absorbs any edge transition before it reaches
        # the desktop.
        # ★ Skip when bg is fully opaque — the uniform bg already provides
        # the same protection without the extra draw call.
        if self.bg_opacity < 0.995:
            self._draw_cover_backplate(img, x, y, size, shape, radius)

        # Composite cover onto main image
        # ★ In-place alpha composite — the old crop→alpha_composite→paste
        # chain did three full-region copies per frame.
        if (x >= 0 and y >= 0 and x + size <= img.width
                and y + size <= img.height
                and img.mode == "RGBA" and thumb.mode == "RGBA"):
            img.alpha_composite(thumb, dest=(x, y))
        else:
            region = img.crop((x, y, x + size, y + size))
            if region.mode != "RGBA":
                region = region.convert("RGBA")
            composited = Image.alpha_composite(region, thumb)
            img.paste(composited, (x, y), composited)

        # Progress indicator on cover
        self._render_cover_progress(img, node, x, y, size, scene)

    def _apply_mask_ss(self, img: Image.Image, ss_size: int,
                       shape: str, radius: int) -> Image.Image:
        """Apply shape mask using supersampling for crisp anti-aliased edges.

        Unlike the old _circle_mask / _roundrect_mask, this does NOT use
        GaussianBlur for feathering. Instead, the mask is drawn at the
        supersampled resolution and the subsequent LANCZOS downscale
        provides natural 1px anti-aliasing. This eliminates:
        - Rotation ghosting (no variable-width soft edge per frame)
        - Light-leak halo (no 2-3px Gaussian blur band around the cover)
        """
        if img.size != (ss_size, ss_size):
            img = img.resize((ss_size, ss_size), Image.LANCZOS)
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        mask = Image.new("L", (ss_size, ss_size), 0)
        mask_draw = ImageDraw.Draw(mask)

        if shape == "circle":
            mask_draw.ellipse((0, 0, ss_size, ss_size), fill=255)
        elif shape == "roundrect":
            r = min(radius * 2, ss_size // 2)  # scale radius to SS coords
            mask_draw.rounded_rectangle((0, 0, ss_size, ss_size),
                                        radius=r, fill=255)
        else:
            # Square / unknown — no mask
            return img

        # Composite: img over transparent bg using the crisp mask
        bg = Image.new("RGBA", (ss_size, ss_size), (0, 0, 0, 0))
        return Image.composite(img, bg, mask)

    def _draw_cover_backplate(self, img: Image.Image, x: int, y: int,
                               size: int, shape: str, radius: int) -> None:
        """Draw a solid opaque shape behind the cover to block light leak.

        Without this, the cover's edge transition (even with supersampled
        AA) can let desktop light through on high-contrast backgrounds.
        The backplate is the window background color at full opacity,
        matching the cover's shape exactly.
        """
        try:
            bp_color = ImageColor.getrgb(self.bg_color)
        except Exception:
            bp_color = (26, 26, 46)
        draw = ImageDraw.Draw(img)

        if shape == "circle":
            draw.ellipse((x, y, x + size, y + size), fill=bp_color)
        elif shape == "roundrect":
            draw.rounded_rectangle((x, y, x + size, y + size),
                                   radius=radius, fill=bp_color)
        # square: no backplate needed (rectangular cover fills its own area)

    # ================================================================
    # Spin animation helpers (de-duplicated from _update_spin)
    # ================================================================

    @staticmethod
    def _decel_factor(elapsed: float, duration: float) -> float:
        """Quadratic deceleration: 1 → 0 over `duration` seconds."""
        if elapsed >= duration or duration <= 0:
            return 0.0
        return 1.0 - (elapsed / duration) ** 2

    @staticmethod
    def _accel_factor(elapsed: float, duration: float) -> float:
        """Quadratic acceleration: 0 → 1 over `duration` seconds."""
        if elapsed >= duration or duration <= 0:
            return 1.0
        return (elapsed / duration) ** 2

    def _spin_dt(self, now: float) -> float:
        """Return clamped frame delta and update last_cover_time."""
        if self._last_cover_time <= 0:
            self._last_cover_time = now
            return 0.016
        dt = min(now - self._last_cover_time, 1.0)
        self._last_cover_time = now
        return dt

    def _apply_spin_delta(self, speed: float, dt: float, factor: float,
                          direction: str) -> None:
        """Apply rotation delta with direction and modulo."""
        delta = speed * dt * factor
        if "ccw" in direction:
            delta = -delta
        self._cover_rotation = (self._cover_rotation + delta) % 360

    # ================================================================

    def _update_spin(self, spin: dict) -> None:
        spin_dur = float(spin.get("spin_duration", spin.get("acceleration", 2.5)))
        stop_dur = float(spin.get("stop_duration", spin_dur * 0.6))
        speed = float(spin.get("speed", 30))
        direction = str(spin.get("direction", "cw"))
        import time
        now = time.time()

        # ★ Song-change deceleration (highest priority).
        # Slow to a stop at the CURRENT angle — do NOT snap to 0°.
        # Preserves visual continuity during the fade-out.
        if getattr(self, '_song_ending', False):
            if not hasattr(self, '_song_end_start') or self._song_end_start is None:
                self._song_end_start = now
            factor = self._decel_factor(now - self._song_end_start, stop_dur)
            if factor <= 0:
                return  # stop at current angle
            self._apply_spin_delta(speed, self._spin_dt(now), factor, direction)
            return

        # ★ Pause → gradual deceleration over stop_dur
        if getattr(self, '_is_paused', False):
            if spin.get("pause_spin", True):
                self._spin_was_paused = True
                if not hasattr(self, '_spin_pause_start') or self._spin_pause_start is None:
                    self._spin_pause_start = now
                factor = self._decel_factor(now - self._spin_pause_start, stop_dur)
                if factor <= 0:
                    return
                self._apply_spin_delta(speed, self._spin_dt(now), factor, direction)
            else:
                self._spin_pause_start = None
            return

        # ★ Resume from pause → reset for acceleration
        self._spin_pause_start = None
        if getattr(self, '_spin_was_paused', False):
            self._spin_was_paused = False
            self._last_cover_time = now
            self._rotation_start_time = now

        # ★ Normal acceleration
        if self._last_cover_time <= 0:
            self._last_cover_time = now
            self._rotation_start_time = now
        dt = self._spin_dt(now)
        factor = self._accel_factor(now - self._rotation_start_time, spin_dur)
        self._apply_spin_delta(speed, dt, factor, direction)

    def _render_cover_progress(self, img: Image.Image, node, x: int, y: int, size: int,
                               scene: SceneGraph | None = None) -> None:
        """Draw arc/ring/roundrect progress on the cover, matching original styles.

        Uses 2× supersampling for anti-aliased edges — PIL draw.line() has
        no built-in AA, so we render at double resolution and LANCZOS-downscale.
        """
        if scene is None:
            return
        pg_node = scene.get("progress")
        if pg_node is None:
            return
        mode = pg_node.data.get("mode", "off")
        if mode not in ("arc", "ring", "roundrect") or self._progress_dur <= 0 or self._no_timeline:
            return
        progress = min(1.0, max(0.0, self._progress_pos / self._progress_dur))
        if progress <= 0:
            return

        color = ImageColor.getrgb(pg_node.data.get("color", "#44cc88"))
        # ★ Blend progress color toward background if progress_alpha < 1
        if self._progress_alpha < 1.0:
            blended = self._blend_to_bg(pg_node.data.get("color", "#44cc88"), self._progress_alpha)
            color = blended
        pw = max(2, int(pg_node.data.get("width", 4)))

        # Supersample: progress arc lines don't benefit from >4X.
        SS = min(4, self._parse_ss(pg_node.data.get("supersample", "2X")))
        ss_size = size * SS
        ss_pw = max(2, pw * SS)

        # ★ Reuse pre-allocated buffer to avoid 4+ MB allocation per frame
        buf_key = (ss_size, ss_size)
        if (self._ss_progress_buf is None or
                self._ss_progress_buf_size != buf_key):
            self._ss_progress_buf = Image.new("RGBA", buf_key, (0, 0, 0, 0))
            self._ss_progress_buf_size = buf_key
        else:
            # Clear by drawing full-coverage transparent rect
            ImageDraw.Draw(self._ss_progress_buf).rectangle(
                (0, 0, ss_size, ss_size), fill=(0, 0, 0, 0))
        ss_layer = self._ss_progress_buf
        ss_draw = ImageDraw.Draw(ss_layer)

        # Scale node data for SS coordinates (border_radius, etc.)
        ss_data = dict(pg_node.data)
        if "border_radius" in ss_data:
            ss_data["border_radius"] = int(ss_data["border_radius"]) * SS

        if mode == "arc":
            self._draw_arc(ss_draw, 0, 0, ss_size, progress, color, ss_pw, ss_data)
        elif mode == "ring":
            self._draw_ring(ss_draw, 0, 0, ss_size, progress, color, ss_pw, ss_data)
        elif mode == "roundrect":
            self._draw_roundrect_progress(ss_draw, 0, 0, ss_size, progress, color, ss_pw, ss_data)

        # Downscale with LANCZOS for anti-aliased result
        ss_layer = ss_layer.resize((size, size), Image.LANCZOS)
        # Composite: use alpha channel of ss_layer as mask
        img.paste(ss_layer, (x, y), ss_layer)

    def _draw_arc(self, draw, x, y, size, progress, color, pw, node):
        """Circular arc around cover."""
        cx, cy = x + size / 2, y + size / 2
        r = size / 2 - pw - 2
        if r <= 0:
            return
        start_map = {
            "top": -90, "top-right": -45,
            "right": 0, "bottom-right": 45,
            "bottom": 90, "bottom-left": 135,
            "left": 180, "top-left": -135,
        }
        start_key = node.get("start_position") or node.get("start", "top")
        start_angle = start_map.get(start_key, -90)
        direction = node.get("direction", "cw")
        cap = node.get("cap_style", "round")
        # PIL arcs don't support end caps well — use line segments for smoothness
        steps = max(32, int(r))
        n = max(1, int(steps * progress))
        # ★ Full-circle sweep: ±360°.  Step angle is constant (full_sweep/steps),
        #   n controls arc length → linear progress response (not quadratic).
        full_sweep = -360 if "ccw" in str(direction) else 360
        pts = []
        import math
        for i in range(n + 1):
            a = math.radians(start_angle + full_sweep * i / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
        if len(pts) >= 4:
            for i in range(0, len(pts) - 2, 2):
                draw.line([pts[i], pts[i+1], pts[i+2], pts[i+3]], fill=color, width=pw)

    def _rect_perimeter_pts(self, rx, ry, rs, r, arc_steps, start_pos, direction):
        """Cached rounded-rect perimeter points rotated to start_pos.

        _build_rect_perimeter + _rotate_points regenerate hundreds of points
        with per-point trig every frame; the geometry only changes with the
        layout parameters, so cache by those.
        """
        key = (rx, ry, rs, r, arc_steps, start_pos)
        pts = self._rect_perimeter_cache.get(key)
        if pts is None:
            pts = self._rotate_points(
                self._build_rect_perimeter(rx, ry, rs, r, arc_steps),
                start_pos, rx, ry, rs)
            if len(self._rect_perimeter_cache) > 8:
                self._rect_perimeter_cache.clear()
            self._rect_perimeter_cache[key] = pts
        # Return a reversed COPY for ccw — never mutate the cached list.
        return pts[::-1] if "ccw" in str(direction) else pts

    def _draw_ring(self, draw, x, y, size, progress, color, pw, node):
        """Rectangular ring around cover."""
        r = max(2, int(node.get("border_radius", 20))) if "border_radius" in node else pw
        pad = pw + 3
        rx, ry = x + pad, y + pad
        rs = size - 2 * pad
        if rs <= 0:
            return
        # Auto-scale arc steps — more steps for larger sizes (SS-safe)
        arc_steps = max(24, int(rs / 5))
        direction = node.get("direction", "cw")
        start_pos = node.get("start_position") or node.get("start", "topleft")
        pts = self._rect_perimeter_pts(rx, ry, rs, r, arc_steps, start_pos, direction)
        n = max(1, int((len(pts) - 1) * progress))
        for i in range(min(n, len(pts) - 1)):
            draw.line([pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1]], fill=color, width=pw)

    def _draw_roundrect_progress(self, draw, x, y, size, progress, color, pw, node):
        """Round rect outline around cover."""
        r = max(2, int(node.get("border_radius", 20)))
        pad = pw + 3
        rx, ry = x + pad, y + pad
        rs = size - 2 * pad
        if rs <= 0:
            return
        arc_steps = max(24, int(rs / 5))
        direction = node.get("direction", "cw")
        start_pos = node.get("start_position") or node.get("start", "topleft")
        pts = self._rect_perimeter_pts(rx, ry, rs, r, arc_steps, start_pos, direction)
        n = max(1, int((len(pts) - 1) * progress))
        for i in range(min(n, len(pts) - 1)):
            draw.line([pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1]], fill=color, width=pw)

    @staticmethod
    def _build_rect_perimeter(rx, ry, rs, r, steps_per_arc):
        """Build perimeter point list for rounded rectangle."""
        import math
        pts = []
        step = 1.0
        # Top edge (left to right)
        px = rx + r
        while px <= rx + rs - r:
            pts.append((px, ry))
            px += step
        # Top-right arc (270→360)
        cx, cy = rx + rs - r, ry + r
        for i in range(steps_per_arc + 1):
            a = math.radians(270 + 90 * i / steps_per_arc)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        # Right edge (top to bottom)
        py = ry + r
        while py <= ry + rs - r:
            pts.append((rx + rs, py))
            py += step
        # Bottom-right arc (0→90)
        cx, cy = rx + rs - r, ry + rs - r
        for i in range(steps_per_arc + 1):
            a = math.radians(90 * i / steps_per_arc)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        # Bottom edge (right to left)
        px = rx + rs - r
        while px >= rx + r:
            pts.append((px, ry + rs))
            px -= step
        # Bottom-left arc (90→180)
        cx, cy = rx + r, ry + rs - r
        for i in range(steps_per_arc + 1):
            a = math.radians(90 + 90 * i / steps_per_arc)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        # Left edge (bottom to top)
        py = ry + rs - r
        while py >= ry + r:
            pts.append((rx, py))
            py -= step
        # Top-left arc (180→270)
        cx, cy = rx + r, ry + r
        for i in range(steps_per_arc + 1):
            a = math.radians(180 + 90 * i / steps_per_arc)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts

    @staticmethod
    def _rotate_points(pts, start_pos, rx, ry, rs):
        """Rotate point list so start_pos is first."""
        markers = {
            "topleft": (rx + rs * 0.1, ry),
            "top": (rx + rs / 2, ry),
            "topright": (rx + rs * 0.9, ry),
            "right": (rx + rs, ry + rs / 2),
            "bottomright": (rx + rs * 0.9, ry + rs),
            "bottom": (rx + rs / 2, ry + rs),
            "bottomleft": (rx + rs * 0.1, ry + rs),
            "left": (rx, ry + rs / 2),
        }
        target = markers.get(start_pos, markers["topleft"])
        best_idx = 0
        best_dist = float('inf')
        for i, p in enumerate(pts):
            d = (p[0] - target[0])**2 + (p[1] - target[1])**2
            if d < best_dist:
                best_dist = d
                best_idx = i
        if best_idx > 0:
            pts = pts[best_idx:] + pts[:best_idx]
        return pts

    # ================================================================
    # Text rendering
    # ================================================================

    def _get_font(self, family: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (family, size, bold)
        if key in self._fonts:
            return self._fonts[key]

        import os
        font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")

        # CJK-capable font candidates (always try these if the requested font isn't CJK)
        CJK_CANDIDATES = ["msyh.ttc", "msyhbd.ttc", "simsun.ttc", "msgothic.ttc"]

        font = None
        # Try the requested family first
        for ext in (".ttf", ".otf", ".ttc"):
            path = os.path.join(font_dir, f"{family}{ext}")
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, size)
                    break
                except Exception:
                    continue

        # If the loaded font likely lacks CJK, also try CJK fonts
        is_cjk_likely = bool(font) and family.lower() in (
            "arial", "segoe ui", "tahoma", "verdana", "times new roman",
            "courier new", "georgia", "trebuchet ms", "comic sans ms", "impact",
        )
        if is_cjk_likely or not font:
            for cand in CJK_CANDIDATES:
                path = os.path.join(font_dir, cand)
                if os.path.exists(path):
                    try:
                        font = ImageFont.truetype(path, size)
                        break
                    except Exception:
                        continue

        # Last resort: any available font
        if not font:
            for cand in ["msyh.ttc", "segoeui.ttf", "arial.ttf", "segoe.ttf"]:
                path = os.path.join(font_dir, cand)
                if os.path.exists(path):
                    try:
                        font = ImageFont.truetype(path, size)
                        break
                    except Exception:
                        continue

        if not font:
            font = ImageFont.load_default()

        self._fonts[key] = font
        return font

    def _render_text(self, img: Image.Image, draw: ImageDraw.Draw,
                     node, x: int, y: int, nw: int, nh: int) -> None:
        text = node.data.get("text", "")
        source = node.data.get("source", "")
        if source == "song_name":
            text = self._song_name
        elif source == "artist_name":
            text = self._artist_name
        if not text:
            return

        font_family = node.data.get("font_family", "Microsoft YaHei")
        font_size = int(node.data.get("font_size", 14))
        font_bold = node.data.get("font_bold", False)
        # ★ Script-aware font fallback (parity with the GL painter): YaHei
        # lacks Hangul/Thai/Arabic/Hebrew glyphs — foreign lyrics rendered
        # as tofu.  Resolve before loading the font so the image cache key
        # (which includes font_family) matches what actually renders.
        try:
            from render.text import get_text_renderer
            font_family = get_text_renderer().resolve_family_for_text(
                text, font_family)
        except Exception:
            pass
        font = self._get_font(font_family, font_size, font_bold)
        color_hex = node.data.get("color", "#ffffff")
        color = ImageColor.getrgb(color_hex)
        max_w = int(float(node.data.get("max_width", 350)))
        align = node.data.get("alignment", "center")

        # Outline
        outline = node.data.get("outline_color", "")
        outline_w = int(node.data.get("outline_width", 0))
        if outline_w > 0 and not outline:
            outline = "#000000"

        # ★ Cache key includes alignment
        cache_key = (text, font_family, font_size, font_bold, color_hex,
                     max_w, outline, outline_w, align)
        if cache_key in self._text_img_cache:
            tex_img = self._text_img_cache[cache_key]
        else:
            tex_img = self._render_text_to_image(
                text, font, color, max_w, outline, outline_w, align)
            self._text_img_cache[cache_key] = tex_img
            # Keep cache bounded
            if len(self._text_img_cache) > 64:
                # Remove oldest 16 entries
                for _ in range(16):
                    self._text_img_cache.pop(next(iter(self._text_img_cache)), None)

        # Composite onto main image with alignment-aware X position
        if tex_img:
            tw, th = tex_img.size
            nw = int(node.abs_width)
            if align == "right":
                tx = x + nw - tw
            elif align == "center":
                tx = x + (nw - tw) / 2
            else:
                tx = x  # left
            tx = int(max(0, min(tx, img.width - tw)))
            ty = int(max(0, min(y, img.height - th)))
            img.paste(tex_img, (tx, ty), tex_img)

    def _render_text_to_image(self, text: str, font, color: tuple,
                               max_w: int, outline: str, outline_w: int,
                               align: str = "center"
                               ) -> Image.Image | None:
        """Render text + optional outline to a standalone RGBA image.
        Returns a pre-multiplied image ready for alpha compositing.
        align: "left" | "center" | "right"
        """
        if not text:
            return None
        lines = self._wrap_text(text, font, max_w)
        if not lines:
            return None

        line_h = font.size + 4
        # Measure
        max_line_w = 0
        for line in lines:
            bbox = font.getbbox(line)
            max_line_w = max(max_line_w, bbox[2] - bbox[0])
        total_h = line_h * len(lines)

        pad = outline_w + 2 if outline and outline_w > 0 else 2
        iw = max_line_w + pad * 2
        ih = total_h + pad * 2

        tex = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
        tex_draw = ImageDraw.Draw(tex)

        # ★ Compute per-line X offset for center/right alignment
        def _x_offset(line_w: int) -> int:
            if align == "right":
                return iw - pad - line_w
            elif align == "center":
                return (iw - line_w) // 2
            return pad  # left

        if outline and outline_w > 0:
            try:
                oc = ImageColor.getrgb(outline)
            except Exception:
                oc = (0, 0, 0)
            for dx in range(-outline_w, outline_w + 1):
                for dy in range(-outline_w, outline_w + 1):
                    if dx == 0 and dy == 0:
                        continue
                    for i, line in enumerate(lines):
                        lw = font.getbbox(line)[2] - font.getbbox(line)[0]
                        tex_draw.text((_x_offset(lw) + dx, pad + dy + i * line_h),
                                     line, fill=oc, font=font)

        for i, line in enumerate(lines):
            lw = font.getbbox(line)[2] - font.getbbox(line)[0]
            tex_draw.text((_x_offset(lw), pad + i * line_h), line, fill=color + (255,), font=font)

        return tex


    def _render_lyrics(self, img: Image.Image, draw: ImageDraw.Draw,
                       node, x: int, y: int, nw: int, nh: int) -> None:
        if not self._lyrics_timed:
            return
        ms = self._progress_pos * 1000 + node.data.get("time_offset_ms", 0)
        current = ""
        for t, text in self._lyrics_timed:
            if t <= ms:
                current = text.strip()
            else:
                break
        if not current:
            return

        font_family = node.data.get("font_family", "Microsoft YaHei")
        font_size = int(node.data.get("font_size", 14))
        font_bold = node.data.get("font_bold", False)
        # ★ Script-aware font fallback (parity with the GL painter): YaHei
        # lacks Hangul/Thai/Arabic/Hebrew glyphs — foreign lyrics rendered
        # as tofu.  Resolve before loading the font so the image cache key
        # (which includes font_family) matches what actually renders.
        try:
            from render.text import get_text_renderer
            font_family = get_text_renderer().resolve_family_for_text(
                text, font_family)
        except Exception:
            pass
        font = self._get_font(font_family, font_size, font_bold)
        color_hex = node.data.get("color", "#cccccc")
        color = ImageColor.getrgb(color_hex)
        max_w = int(float(node.data.get("max_width", 350)))
        align = node.data.get("alignment", "center")

        # ★ Cache lyrics text image — lyrics line changes every few seconds,
        # so most frames hit the cache
        cache_key = (current, font_family, font_size, font_bold, color_hex, max_w, align, 0)
        if cache_key in self._text_img_cache:
            tex_img = self._text_img_cache[cache_key]
        else:
            tex_img = self._render_text_to_image(current, font, color, max_w, "", 0, align)
            self._text_img_cache[cache_key] = tex_img
            if len(self._text_img_cache) > 64:
                for _ in range(16):
                    self._text_img_cache.pop(next(iter(self._text_img_cache)), None)

        if tex_img:
            tw, th = tex_img.size
            tx = max(0, min(x, img.width - tw))
            ty = max(0, min(y, img.height - th))
            img.paste(tex_img, (tx, ty), tex_img)


    def _render_progress_bar(self, img: Image.Image, node,
                             x: int, y: int, nw: int, nh: int) -> None:
        if node.data.get("mode", "off") != "bar":
            return
        if self._no_timeline:
            return
        has_dur = self._progress_dur > 0
        progress = min(1.0, self._progress_pos / self._progress_dur) if has_dur else 0.0
        bar_h = max(3, int(node.data.get("width", 4)))
        # ★ Read bar-specific settings (isolated namespace)
        bar_cfg = node.data.get("bar", {})
        length_ratio = float(bar_cfg.get("length_ratio", node.data.get("length_ratio", 0.7)))
        bar_w = max(20, int(nw * length_ratio))
        direction = bar_cfg.get("direction", node.data.get("direction", "lr"))
        start_pos = bar_cfg.get("start_position", node.data.get("start_position", "left"))
        bx = x + (nw - bar_w) // 2
        by = y + nh // 2 - bar_h // 2

        draw = ImageDraw.Draw(img)
        bg = ImageColor.getrgb(node.data.get("background_color", "#333355"))
        fg_raw = node.data.get("color", "#44cc88")
        # ★ Blend progress color toward background if progress_alpha < 1
        if self._progress_alpha < 1.0:
            fg = self._blend_to_bg(fg_raw, self._progress_alpha)
        else:
            fg = ImageColor.getrgb(fg_raw)

        # Background
        draw.rounded_rectangle((bx, by, bx + bar_w, by + bar_h),
                               radius=bar_h // 2, fill=bg)

        # Progress fill (only if duration known)
        if has_dur and progress > 0:
            fw = int(bar_w * progress)
            if fw > bar_h:
                if direction == "rl" or start_pos == "right":
                    draw.rounded_rectangle((bx + bar_w - fw, by, bx + bar_w, by + bar_h),
                                          radius=bar_h // 2, fill=fg)
                else:
                    draw.rounded_rectangle((bx, by, bx + fw, by + bar_h),
                                          radius=bar_h // 2, fill=fg)

        # Time text with configurable position + outline
        time_cfg = node.data.get("time_text", {})
        if time_cfg.get("visible", True):
            from core.progress import format_time
            if has_dur:
                time_text = f"{format_time(self._progress_pos)} / {format_time(self._progress_dur)}"
            else:
                time_text = format_time(self._progress_pos)
            t_family = time_cfg.get("font", {}).get("family", "Microsoft YaHei")
            t_size = int(time_cfg.get("font", {}).get("size", 8))
            font = self._get_font(t_family, t_size, False)
            tc = ImageColor.getrgb(time_cfg.get("color", "#cccccc"))
            tw = font.getlength(time_text)
            th = t_size + 4
            time_pos = time_cfg.get("position", "bottom")
            # PIL draws text with y=top — small gap from bar edge
            gap = 2
            if time_pos == "inside":
                tx = bx + (bar_w - tw) / 2
                ty = by + (bar_h - th) / 2
            elif time_pos == "left":
                tx = bx - tw - gap
                ty = by + (bar_h - th) / 2
            elif time_pos == "right":
                tx = bx + bar_w + gap
                ty = by + (bar_h - th) / 2
            elif time_pos == "top":
                tx = bx + (bar_w - tw) / 2
                ty = by - th - gap
            elif time_pos == "top-left":
                tx = bx
                ty = by - th - gap
            elif time_pos == "top-right":
                tx = bx + bar_w - tw
                ty = by - th - gap
            elif time_pos == "bottom-left":
                tx = bx
                ty = by + bar_h + gap
            elif time_pos == "bottom-right":
                tx = bx + bar_w - tw
                ty = by + bar_h + gap
            else:  # "bottom"
                tx = bx + (bar_w - tw) / 2
                ty = by + bar_h + gap
            # Fine-tune offset
            tx += int(float(time_cfg.get("offset_x", 0)))
            ty += int(float(time_cfg.get("offset_y", 0)))
            # Outline
            outline_color = time_cfg.get("outline_color", "")
            outline_w = int(float(time_cfg.get("outline_width", 0)))
            if outline_w > 0:
                if not outline_color:
                    outline_color = "#000000"
                try: oc = ImageColor.getrgb(outline_color)
                except Exception: oc = (0, 0, 0)
                for dx in range(-outline_w, outline_w + 1):
                    for dy in range(-outline_w, outline_w + 1):
                        if dx == 0 and dy == 0: continue
                        draw.text((tx + dx, ty + dy), time_text, fill=oc, font=font)
            draw.text((tx, ty), time_text, fill=tc, font=font)


    def _render_progress_time(self, img: Image.Image, draw: ImageDraw.Draw,
                               node, x: int, y: int, nw: int, nh: int) -> None:
        """Render standalone progress time text (elapsed / total)."""
        if not node.visible:
            return
        if self._no_timeline:
            return
        from core.progress import format_time

        elapsed = format_time(self._progress_pos)
        has_dur = self._progress_dur > 0
        fmt = node.data.get("format", "elapsed_total")
        sep = node.data.get("separator", " / ")

        if fmt == "elapsed_total":
            total = format_time(self._progress_dur) if has_dur else "--:--"
            text = f"{elapsed}{sep}{total}"
        elif fmt == "remaining":
            if has_dur:
                remain = max(0, self._progress_dur - self._progress_pos)
                text = f"-{format_time(remain)}"
            else:
                text = elapsed
        else:
            text = elapsed

        font = self._get_font(
            node.data.get("font_family", "Microsoft YaHei"),
            int(node.data.get("font_size", 10)),
            node.data.get("font_bold", False),
        )
        color = ImageColor.getrgb(node.data.get("color", "#cccccc"))

        # Outline
        outline = node.data.get("outline_color", "")
        outline_w = int(node.data.get("outline_width", 0))
        if outline_w > 0:
            if not outline:
                outline = "#000000"
            oc = ImageColor.getrgb(outline)
            for dx in range(-outline_w, outline_w + 1):
                for dy in range(-outline_w, outline_w + 1):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x + dx, y + dy), text, fill=oc, font=font)

        draw.text((x, y), text, fill=color, font=font)


    # ================================================================
    # Placeholder
    # ================================================================

    def _draw_placeholder(self, img: Image.Image, draw: ImageDraw.Draw,
                          w: int, h: int) -> None:
        font = self._get_font("Microsoft YaHei", 16, False)
        text = "QQ音乐未在播放"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (w - tw) // 2
        y = (h - th) // 2
        draw.text((x, y), text, fill=(140, 140, 170, 200), font=font)

    # ================================================================
    # Helpers
    # ================================================================

    def _wrap_text(self, text: str, font, max_width: int) -> list[str]:
        lines = []
        for paragraph in text.split("\n"):
            current = ""
            for word in paragraph.split():
                test = current + (" " if current else "") + word
                if font.getlength(test) > max_width and current:
                    lines.append(current)
                    current = ""
                    test = word
                # ★ CJK fallback: a token without spaces (Chinese/Japanese/
                # Korean lyrics and titles) can be wider than the whole line —
                # hard-break it per character instead of overflowing forever.
                if font.getlength(test) <= max_width:
                    current = test
                    continue
                for ch in test:
                    piece = current + (" " if current else "") + ch
                    if font.getlength(piece) > max_width and current:
                        lines.append(current)
                        current = ch
                    else:
                        current = piece
            if current:
                lines.append(current)
        return lines if lines else [text]

    def _opacity_blend(self, img: Image.Image, bg: Image.Image,
                       x: int, y: int, w: int, h: int, opacity: float) -> None:
        """Blend the rendered element region with the saved background.

        Uses Image.blend(fg, bg, opacity) where opacity=1.0 = all foreground,
        opacity=0.0 = all background. This replaces the old _apply_opacity_region
        which blended the element with itself (producing a no-op).
        """
        if opacity >= 0.995:
            return
        fg = img.crop((x, y, x + w, y + h))
        if fg.size != bg.size:
            return
        if fg.mode != "RGBA":
            fg = fg.convert("RGBA")
        if bg.mode != "RGBA":
            bg = bg.convert("RGBA")
        blended = Image.blend(fg, bg, opacity)
        img.paste(blended, (x, y))

    # ---- Spin state (delegated to shared SpinController) ----

    @property
    def _cover_rotation(self) -> float:
        return self._spin._cover_rotation
    @_cover_rotation.setter
    def _cover_rotation(self, v: float) -> None:
        self._spin._cover_rotation = v

    @property
    def _last_cover_time(self) -> float:
        return self._spin._last_cover_time
    @_last_cover_time.setter
    def _last_cover_time(self, v: float) -> None:
        self._spin._last_cover_time = v

    @property
    def _rotation_start_time(self) -> float:
        return self._spin._rotation_start_time
    @_rotation_start_time.setter
    def _rotation_start_time(self, v: float) -> None:
        self._spin._rotation_start_time = v

    @property
    def _song_ending(self) -> bool:
        return self._spin._song_ending
    @_song_ending.setter
    def _song_ending(self, v: bool) -> None:
        self._spin._song_ending = v

    @property
    def _song_end_start(self) -> float | None:
        return self._spin._song_end_start
    @_song_end_start.setter
    def _song_end_start(self, v: float | None) -> None:
        self._spin._song_end_start = v

    @property
    def _is_paused(self) -> bool:
        return self._spin._is_paused
    @_is_paused.setter
    def _is_paused(self, v: bool) -> None:
        self._spin._is_paused = v

    def reset_rotation(self) -> None:
        """Reset cover spin for new song (smooth acceleration restart)."""
        self._spin.reset_rotation()

    def start_song_end_spin(self) -> None:
        """Begin deceleration for song change."""
        self._spin.start_song_end_spin()

    # ---- Progress Alpha (independent progress opacity) ----

    def tick_progress_alpha(self) -> None:
        """Increment progress_alpha toward 1.0 (called each frame during showing)."""
        if self._progress_alpha < 1.0:
            self._progress_alpha = min(1.0, self._progress_alpha + 0.05)

    def reset_progress_alpha(self) -> None:
        """Reset progress_alpha to 0 (called on new song load)."""
        self._progress_alpha = 0.0

    def set_progress_alpha(self, value: float) -> None:
        """Directly set progress_alpha (for transition fade-out)."""
        self._progress_alpha = max(0.0, min(1.0, value))

    def get_progress_alpha(self) -> float:
        return self._progress_alpha

    @staticmethod
    def _parse_ss(val) -> int:
        """Parse supersample config value to integer multiplier.
        Accepts "2X", "4X", "6X" (str) or 2, 4, 6 (int/float) for backward compat.
        Returns 2-6, always even.
        """
        try:
            if isinstance(val, str):
                n = int(val.rstrip("Xx"))
            else:
                n = int(val)
        except (ValueError, TypeError):
            return 2
        n = max(2, min(6, n))
        if n % 2 != 0:
            n = 2
        return n

    def _blend_to_bg(self, color_hex: str, alpha: float) -> tuple:
        """Blend a color toward the background color by (1-alpha)."""
        try:
            fg = ImageColor.getrgb(color_hex)
        except Exception:
            return (68, 204, 136)
        bg = ImageColor.getrgb(self.bg_color)
        return tuple(int(fg[i] * alpha + bg[i] * (1.0 - alpha)) for i in range(3))

    # ---- Freeze frame (cover_out phase stops re-rendering) ----

    def freeze_frame(self) -> None:
        """Freeze the current last frame — subsequent renders return it unchanged."""
        if self._last_frame:
            self._frozen_frame = self._last_frame.copy()

    def unfreeze(self) -> None:
        """Resume normal rendering."""
        self._frozen_frame = None

    def clear_caches(self) -> None:
        self._cover_cache.clear()
        self._mask_cache.clear()
        self._ss_thumb_cache.clear()
        self._ss_progress_buf = None
        self._ss_progress_buf_size = None
        self._text_img_cache.clear()
        self._frame_buf = None
        self._frame_buf_size = None
        self.reset_rotation()

    def get_profile_report(self) -> dict:
        """Return per-phase timing totals and reset accumulators.
        Called by the engine every 2 seconds for FPS logging.
        """
        n = max(1, self._prof_frame_count)
        report = {
            "frames": n,
            "init_ms": (self._prof_init / n) * 1000,
            "cover_ms": (self._prof_cover / n) * 1000,
            "text_ms": (self._prof_text / n) * 1000,
            "lyrics_ms": (self._prof_lyrics / n) * 1000,
            "progress_ms": (self._prof_progress / n) * 1000,
        }
        self._prof_init = 0.0
        self._prof_cover = 0.0
        self._prof_text = 0.0
        self._prof_lyrics = 0.0
        self._prof_progress = 0.0
        self._prof_frame_count = 0
        return report

    def cleanup(self) -> None:
        self.clear_caches()
