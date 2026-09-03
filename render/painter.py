"""Painter — draws each type of scene node to OpenGL.

Uses pyglet 2.x GL API with correct ctypes calling conventions:
- glGen* functions take (n, POINTER(GLuint)) output arrays
- glShaderSource takes array of string pointers
"""
import math
import time
import ctypes
from PIL import Image
from pyglet.gl import (
    GL_TRIANGLES, GL_TEXTURE_2D, GL_RGBA, GL_UNSIGNED_BYTE,
    GL_NEAREST, GL_LINEAR, GL_LINEAR_MIPMAP_LINEAR,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE,
    glGenTextures, glBindTexture, glTexImage2D,
    glTexParameteri, glGenerateMipmap, glDeleteTextures,
    glGenVertexArrays, glBindVertexArray, glDeleteVertexArrays,
    glGenBuffers, glBindBuffer, glBufferData, GL_ARRAY_BUFFER,
    GL_STATIC_DRAW, glVertexAttribPointer,
    GL_FLOAT, GL_FALSE, glEnableVertexAttribArray,
    glDrawArrays, glEnable, GL_BLEND,
    GL_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA, glBlendFunc,
    GLuint,
)
from scene.node import SceneNode
from scene.nodes import CoverNode, TextNode, SyncedLyricsNode, ProgressBarNode, ProgressTimeNode
from render.shaders import get_shader
from render.text import get_text_renderer
from render.spin_controller import SpinController
from core.progress import format_time
from core.constants import ARC_CACHE_MAX, ARC_CACHE_EVICT
from core.logs import get_logger

_render_log = get_logger("render")


# ============================================================
# pyglet 2.x GL helpers — wrap glGen*/glDelete* with correct API
# ============================================================

def _gen_vertex_array() -> int:
    """Generate one VAO, return its ID."""
    arr = (GLuint * 1)()
    glGenVertexArrays(1, arr)
    return arr[0]


def _delete_vertex_array(vao_id: int) -> None:
    """Delete one VAO."""
    arr = (GLuint * 1)(vao_id)
    glDeleteVertexArrays(1, arr)


def _gen_buffer() -> int:
    """Generate one VBO, return its ID."""
    arr = (GLuint * 1)()
    glGenBuffers(1, arr)
    return arr[0]


def _delete_buffer(vbo_id: int) -> None:
    """Delete one VBO."""
    arr = (GLuint * 1)(vbo_id)
    glDeleteBuffers(1, arr)


def _gen_texture() -> int:
    """Generate one texture, return its ID."""
    arr = (GLuint * 1)()
    glGenTextures(1, arr)
    return arr[0]


def _delete_texture(tex_id: int) -> None:
    """Delete one texture."""
    arr = (GLuint * 1)(tex_id)
    glDeleteTextures(1, arr)


# ============================================================
# Painter
# ============================================================

class Painter:
    """Draws scene nodes using OpenGL.

    Manages:
    - Fullscreen quad VAO (reused for all textured rendering)
    - Texture upload/download
    - Node-type dispatch
    """

    def __init__(self, config: dict):
        self.config = config
        self._vao: int = 0
        self._vbo: int = 0
        self._textures: dict[str, tuple[int, int]] = {}  # key → (tex_id, img_id)
        self._text_renderer = get_text_renderer()
        self._spin = SpinController()
        self._cover_x = 0
        self._cover_y = 0
        self._cover_size = 180  # updated by engine each frame
        self._cover_border_radius = 20  # from cover node, for roundrect arc

        # Screen size (set before each frame)
        self.screen_w = 400
        self.screen_h = 550

        # Cover flip cache — avoid re-creating FLIP_TOP_BOTTOM image every frame
        self._cover_flip_key = 0
        self._cover_flipped = None

    def init_gl(self) -> None:
        """Initialize OpenGL resources (call after context creation)."""
        self._create_quad_vao()

    def _create_quad_vao(self) -> None:
        """Create a fullscreen quad VAO for textured rendering."""
        # 6 vertices × 4 floats (pos.xy + texcoord.uv) = 24 floats
        vertices = (ctypes.c_float * 24)(
            0.0, 0.0,   0.0, 0.0,  # bottom-left
            1.0, 0.0,   1.0, 0.0,  # bottom-right
            0.0, 1.0,   0.0, 1.0,  # top-left

            1.0, 0.0,   1.0, 0.0,  # bottom-right
            0.0, 1.0,   0.0, 1.0,  # top-left
            1.0, 1.0,   1.0, 1.0,  # top-right
        )

        self._vao = _gen_vertex_array()
        glBindVertexArray(self._vao)

        self._vbo = _gen_buffer()
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
        glBufferData(GL_ARRAY_BUFFER, ctypes.sizeof(vertices), vertices, GL_STATIC_DRAW)

        stride = 4 * ctypes.sizeof(ctypes.c_float)
        # position (location=0): 2 floats, offset=0
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, stride, 0)
        glEnableVertexAttribArray(0)
        # texcoord (location=1): 2 floats, offset=2 floats
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, stride,
                              ctypes.c_void_p(2 * ctypes.sizeof(ctypes.c_float)))
        glEnableVertexAttribArray(1)

        glBindVertexArray(0)

    # ================================================================
    # Node dispatch
    # ================================================================

    def paint_node(self, node: SceneNode) -> None:
        """Paint a single scene node."""
        if not node.visible or node.opacity <= 0.001:
            return
        if isinstance(node, CoverNode):
            self.paint_cover(node)
        elif isinstance(node, TextNode):
            self.paint_text(node)
        elif isinstance(node, SyncedLyricsNode):
            self.paint_lyrics(node)
        elif isinstance(node, ProgressBarNode):
            self.paint_progress_bar(node)
        elif isinstance(node, ProgressTimeNode):
            self.paint_progress_time(node)

    def paint_cover(self, node: CoverNode) -> None:
        """Paint album cover art."""
        cover_img = self._get_cover_image()
        if cover_img is None:
            # ★ Use cached default cover placeholder
            if not hasattr(self, '_placeholder_img'):
                try:
                    import os
                    _proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    path = os.path.join(_proj, "assets", "default_cover.png")
                    from PIL import Image as PILImage
                    self._placeholder_img = PILImage.open(path).convert("RGBA")
                except Exception:
                    self._placeholder_img = False
            if self._placeholder_img and self._placeholder_img is not False:
                cover_img = self._placeholder_img
                self._cover_image = cover_img
            else:
                return
        self._diag_cover_none = True
        # ★ Cache the flipped image per source-image.  FLIP_TOP_BOTTOM creates
        # a new PIL Image every call, which would invalidate the id()-based
        # texture cache and cause per-frame re-upload → flicker/transparency.
        # By caching the flipped version we keep the texture cache stable.
        _flip_key = id(cover_img)
        _flipped = getattr(self, '_cover_flipped', None)
        if _flip_key != getattr(self, '_cover_flip_key', 0):
            self._cover_flip_key = _flip_key
            self._cover_flipped = cover_img.transpose(Image.FLIP_TOP_BOTTOM)
        cover_img = self._cover_flipped
        # One-time diagnostic: log cover rendering parameters
        if getattr(self, '_diag_cover_done', 0) < 5:
            self._diag_cover_done = getattr(self, '_diag_cover_done', 0) + 1
            from core.logs import get_logger
            # Check alpha channel
            if cover_img.mode in ("RGBA", "LA"):
                bands = cover_img.getextrema()
                alpha_band = bands[-1] if len(bands) > 3 else (0, 0)
            else:
                alpha_band = ("N/A", "N/A")
            get_logger("render").debug(
                f"COVER DIAG #{self._diag_cover_done}: "
                f"img={cover_img.size} mode={cover_img.mode} "
                f"alpha_range={alpha_band} "
                f"id={id(cover_img)} flip_key={self._cover_flip_key} "
                f"node.opacity={node.opacity:.3f} node.visible={node.visible} "
                f"pos=({node.abs_x:.0f},{node.abs_y:.0f}) size={int(node.size)} "
                f"screen=({self.screen_w},{self.screen_h})"
            )
        size = int(node.size)
        shape = node.data.get("shape", "circle")
        feather_px = float(node.data.get("feather", 5))  # pixel units
        # Minimum 1px AA to avoid hard aliased edges at feather=0
        feather_aa = max(1.0, feather_px)

        spin_cfg = node.data.get("spin", {})
        spin_enabled = spin_cfg.get("enabled", False) if spin_cfg else node.data.get("spin_enabled", False)
        if spin_enabled:
            self._update_cover_spin(node, spin_cfg)
        else:
            # ★ Reset rotation to 0 when spin is disabled (cover snaps back to original angle)
            self._cover_rotation = 0.0
            self._last_cover_time = 0.0
            self._rotation_start_time = 0.0

        tex_id = self._get_or_upload_texture(f"cover_{node.id}", cover_img)

        if shape == "circle":
            shader = get_shader("circle_mask")
        elif shape in ("roundrect", "square"):
            # square = roundrect with radius=0, supports stroke
            shader = get_shader("roundrect_mask")
        else:
            shader = get_shader("default")

        # ★ Stroke config
        stroke_cfg = node.data.get("stroke", {})
        stroke_color = stroke_cfg.get("color", "")
        stroke_width = float(stroke_cfg.get("width", 0))

        shader.use()
        shader.set_texture("uTexture", 0, tex_id)
        shader.set_float("uOpacity", node.opacity)
        shader.set_vec2("uOffset", node.abs_x, node.abs_y)
        shader.set_vec2("uScale", node.scale_x, node.scale_y)
        shader.set_float("uRotation", self._cover_rotation)
        shader.set_vec2("uSize", size, size)
        shader.set_vec2("uScreenSize", self.screen_w, self.screen_h)
        shader.set_vec2("uTexSize", size, size)
        if shape == "circle":
            shader.set_float("uFeather", feather_aa)
        elif shape in ("roundrect", "square"):
            br = float(node.data.get("border_radius", 20)) if shape == "roundrect" else 0.0
            shader.set_float("uRadius", br)
            shader.set_float("uFeather", feather_aa)
        # Stroke uniforms (both shaders have them)
        if stroke_color and stroke_color.startswith("#") and stroke_width > 0:
            sc = self._hex_to_rgb(stroke_color)
            shader.set_vec3("uStrokeColor", sc[0] / 255.0, sc[1] / 255.0, sc[2] / 255.0)
            shader.set_float("uStrokeWidth", stroke_width)
        else:
            shader.set_vec3("uStrokeColor", 0.0, 0.0, 0.0)
            shader.set_float("uStrokeWidth", 0.0)

        # ---- Visual effects uniforms (fed via set_data() from _provide_data) ----
        fx = getattr(self, '_cover_fx', None) or {}
        cd = fx.get("cd_groove", {})
        mb = fx.get("motion_blur", {})
        sp = fx.get("specular", {})

        cd_val = float(cd.get("intensity", 0.5)) if cd.get("enabled") else 0.0
        cd_freq = float(cd.get("frequency", 80))
        mb_val = float(mb.get("intensity", 0.5)) if mb.get("enabled") else 0.0
        sp_val = float(sp.get("intensity", 0.6)) if sp.get("enabled") else 0.0

        shader.set_float("uCDGroove", cd_val)
        shader.set_float("uCDGrooveFreq", cd_freq)
        shader.set_float("uMotionBlur", mb_val)
        shader.set_float("uSpecular", sp_val)

        glEnable(GL_BLEND)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)  # Pre-multiplied alpha for UpdateLayeredWindow
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLES, 0, 6)

    def paint_text(self, node: TextNode) -> None:
        """Paint a text element."""
        text = self._get_text_content(node)
        if not text:
            return
        color_rgb = self._hex_to_rgb(node.data.get("color", "#ffffff"))
        max_w = int(float(node.data.get("max_width", 350)))
        align = node.data.get("alignment", "center")
        font_family = node.data.get("font_family", "Microsoft YaHei")
        font_size = int(float(node.data.get("font_size", 14)))
        font_bold = node.data.get("font_bold", False)

        outline_color = node.data.get("outline_color", "")
        outline_width = int(float(node.data.get("outline_width", 0)))
        if outline_width > 0 and not outline_color:
            outline_color = "#000000"
        outline_rgb = self._hex_to_rgb(outline_color) if outline_width > 0 else (0, 0, 0)

        # ★ Pre-composite outline in PIL (text.py handles it natively).
        # Avoids 8 extra GPU draw calls per outlined text element.
        tex_id = self._text_renderer.render_to_texture(
            text, font_family, font_size, font_bold, color_rgb, max_w, align,
            outline_width=outline_width, outline_color=outline_rgb)
        _outline_suffix = f"|{outline_width}|{outline_rgb}" if outline_width > 0 else ""
        cache_key = f"{text}|{font_family}|{font_size}|{font_bold}|{color_rgb}|{max_w}|{align}{_outline_suffix}"
        tex_w, tex_h = self._text_renderer.get_texture_size(cache_key)

        if node.data.get("shadow_color", "") and outline_width == 0:
            self._draw_with_shadow(node, tex_id, tex_w, tex_h)
        else:
            # Single draw call — outline (if any) is already baked into the texture
            self._draw_textured_quad(node, tex_id, tex_w, tex_h, node.opacity)

    def paint_lyrics(self, node: SyncedLyricsNode) -> None:
        """Paint synced lyrics with per-line bilingual support.

        Two rendering modes (参考 YesPlayMusic / LyricsX):
          - merged: main lyrics block → translation block below (single stacked pair)
          - separate: each main line → its translation below (interleaved per-line)

        Translation text uses dimmer color and optionally smaller font for
        clear visual distinction from the original lyrics.
        """
        # ---- Gather styling ----
        main_color = self._hex_to_rgb(node.data.get("color", "#cccccc"))
        main_family = node.data.get("font_family", "Microsoft YaHei")
        main_size = int(float(node.data.get("font_size", 14)))
        main_bold = node.data.get("font_bold", False)
        max_w = int(float(node.data.get("max_width", 350)))
        align = node.data.get("alignment", "center")
        line_spacing = int(node.data.get("line_spacing", 4))

        bilingual = node.data.get("bilingual", True)
        mode = node.data.get("bilingual_mode", "merged")

        trans_color_raw = node.data.get("trans_color", "")
        if trans_color_raw and trans_color_raw.startswith("#"):
            trans_color = self._hex_to_rgb(trans_color_raw)
        else:
            # Auto: dim main color by 30%
            trans_color = tuple(max(0, int(c * 0.7)) for c in main_color)

        trans_family = node.data.get("trans_font_family") or main_family
        trans_size_raw = node.data.get("trans_font_size", 0)
        trans_size = int(float(trans_size_raw)) if trans_size_raw > 0 else max(8, int(main_size * 0.85))

        outline_color = node.data.get("outline_color", "")
        outline_width = int(float(node.data.get("outline_width", 0)))
        time_offset = node.data.get("time_offset_ms", 0)

        # ---- Find active lines (new or legacy format) ----
        ms = self._progress_pos * 1000 + time_offset
        max_lines = int(float(node.data.get("max_lines", 1)))
        active_lines = self._get_active_bilingual_lines(ms, max_lines, bilingual)

        if not active_lines:
            return

        # ---- Per-line rendering ----
        self._render_lyrics_lines(
            node, active_lines,
            main_color=main_color, main_family=main_family, main_size=main_size, main_bold=main_bold,
            trans_color=trans_color, trans_family=trans_family, trans_size=trans_size,
            max_w=max_w, align=align, line_spacing=line_spacing,
            outline_color=outline_color, outline_width=outline_width,
            mode=mode,
        )

    def paint_progress_bar(self, node: ProgressBarNode) -> None:
        """Paint a progress bar/arc/ring with time text."""
        mode = node.data.get("mode", "bar")
        if mode == "off":
            return
        progress = self._get_progress()
        # ★ Diagnostic: log first 5 frames to catch silent rendering failures
        _diag_cnt = getattr(self, '_diag_pb_count', 0)
        if _diag_cnt < 5:
            from core.logs import get_logger as _get_log
            _get_log("render").info(
                f"PROGRESS_BAR: mode={mode} progress={progress:.4f} "
                f"pos={self._progress_pos:.1f} dur={self._progress_dur:.0f} "
                f"cover_size={self._cover_size} cover_x={self._cover_x} "
                f"visible={node.visible} opacity={node.opacity:.3f}")
            self._diag_pb_count = _diag_cnt + 1
        if progress <= 0 and mode != "bar":
            return

        bar_color = self._hex_to_rgb(node.data.get("color", "#44cc88"))
        bg_color = self._hex_to_rgb(node.data.get("background_color", "#333355"))
        bar_h = max(4, int(node.data.get("width", 4)))

        if mode == "bar":
            # ★ Read bar-specific settings (isolated from arc/ring/roundrect)
            bar_cfg = node.data.get("bar", {})
            length_ratio = float(bar_cfg.get("length_ratio", node.data.get("length_ratio", 0.7)))
            direction = bar_cfg.get("direction", node.data.get("direction", "lr"))
            bar_w = max(20, int(node.width * length_ratio))
            tex_id = self._render_bar_texture(bar_w, bar_h, progress, bg_color, bar_color, direction)
            self._draw_textured_quad(node, tex_id, bar_w, bar_h, node.opacity)
        else:
            # Arc / ring / roundrect — overlay on cover position
            # ★ arc.position + arc.distance control placement relative to cover:
            #   edge:    arc visual midline exactly on cover boundary
            #   inside:  arc fully inside cover, distance=extra px inward
            #   outside: arc fully outside cover, distance=extra px outward
            #
            # Geometry: arc midline in texture = tex_size/2 - pw - 2
            #           target midline from cover center = cover_size/2 + target_offset
            #           Since texture is centered on cover:
            #             tex_size/2 - pw - 2 = cover_size/2 + target_offset
            #           → tex_size = cover_size + 2*(pw + 2 + target_offset)
            arc_cfg = node.data.get("arc", {})
            arc_pos = arc_cfg.get("position", "edge")
            arc_dist = int(arc_cfg.get("distance", 0))
            cover_size = self._cover_size
            pw = max(2, int(node.data.get("width", 4)))

            if arc_pos == "inside":
                # Outer edge of stroke on cover boundary at d=0
                target_offset = -(pw / 2.0 + arc_dist)
            elif arc_pos == "outside":
                # Inner edge of stroke on cover boundary at d=0
                target_offset = pw / 2.0 + arc_dist
            else:  # edge
                # Inner edge of stroke on cover boundary at d=0
                target_offset = pw / 2.0 + arc_dist

            arc_tex_size = int(max(20, cover_size + 2 * (pw + 2 + target_offset)))
            arc_dx = self._cover_x + (cover_size - arc_tex_size) // 2
            arc_dy = self._cover_y + (cover_size - arc_tex_size) // 2

            tex_id = self._render_arc_texture(arc_tex_size, progress, mode, node, bar_color)
            self._draw_textured_quad_at(arc_dx, arc_dy, arc_tex_size, arc_tex_size, tex_id, node.opacity)
            _diag_ac = getattr(self, '_diag_pb_arc_count', 0)
            if _diag_ac < 5:
                from core.logs import get_logger as _get_log
                _get_log("render").info(
                    f"PROGRESS_ARC: mode={mode} tex={arc_tex_size}px @({arc_dx},{arc_dy}) "
                    f"tex_id={tex_id} pw={pw} pos={arc_pos} dist={arc_dist}")
                self._diag_pb_arc_count = _diag_ac + 1

        # Embedded time text: only for bar mode.  Arc/ring use standalone
        # ProgressTimeNode (paint_progress_time) positioned relative to cover.
        if mode == "bar" and node.data.get("time_visible", node.data.get("time_text", {}).get("visible", True)):
            self._paint_time_text(node)

    # ★ Time-text texture: single reusable texture, re-specified with
    # glTexImage2D when the time string changes (~1/sec).  No cache, no
    # gen/delete churn, no atlas UV issues — just one long-lived tex_id.
    _time_tex_garbage: list = []

    def _get_time_texture(self, text: str, font_family: str, font_size: int,
                          font_bold: bool, color_rgb: tuple,
                          outline_width: int = 0,
                          outline_rgb: tuple = (0, 0, 0)) -> tuple[int, int, int]:
        """Render time text to PIL, upload to a NEW GL texture each call.

        Returns (tex_id, tex_w, tex_h).  Each call creates a fresh texture
        because multiple callers (standalone time, bar time, lyrics outline)
        must NOT share a single texture — glTexImage2D overwrites would
        corrupt the texture content between render calls.
        """
        import PIL.Image, PIL.ImageDraw
        font = self._text_renderer.get_font(font_family, font_size, font_bold)
        outline_width = max(0, int(outline_width))
        probe = PIL.Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        probe_draw = PIL.ImageDraw.Draw(probe)
        try:
            bbox = probe_draw.textbbox((0, 0), text, font=font,
                                       stroke_width=outline_width)
        except TypeError:
            bbox = font.getbbox(text)
            if outline_width > 0:
                bbox = (
                    bbox[0] - outline_width,
                    bbox[1] - outline_width,
                    bbox[2] + outline_width,
                    bbox[3] + outline_width,
                )
        pad = 4
        tw = bbox[2] - bbox[0] + pad * 2
        th = bbox[3] - bbox[1] + pad * 2
        img = PIL.Image.new("RGBA", (max(10, tw), max(10, th)), (0, 0, 0, 0))
        draw = PIL.ImageDraw.Draw(img)
        draw_x = pad - bbox[0]
        draw_y = pad - bbox[1]
        draw_kwargs = {
            "fill": color_rgb + (255,),
            "font": font,
        }
        if outline_width > 0:
            try:
                draw.text(
                    (draw_x, draw_y), text,
                    stroke_width=outline_width,
                    stroke_fill=outline_rgb + (255,),
                    **draw_kwargs,
                )
            except TypeError:
                for dx in range(-outline_width, outline_width + 1):
                    for dy in range(-outline_width, outline_width + 1):
                        if dx == 0 and dy == 0:
                            continue
                        draw.text(
                            (draw_x + dx, draw_y + dy), text,
                            fill=outline_rgb + (255,),
                            font=font,
                        )
                draw.text((draw_x, draw_y), text, **draw_kwargs)
        else:
            draw.text((draw_x, draw_y), text, **draw_kwargs)
        img = img.transpose(PIL.Image.FLIP_TOP_BOTTOM)
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        data = img.tobytes("raw", "RGBA", 0, -1)

        # ★ Fresh texture per call — no sharing between callers
        tex_id = _gen_texture()
        glBindTexture(GL_TEXTURE_2D, tex_id)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, img.width, img.height, 0,
                     GL_RGBA, GL_UNSIGNED_BYTE, data)
        glGenerateMipmap(GL_TEXTURE_2D)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        # ★ Queue for cleanup next frame (after draw calls complete)
        _garbage = getattr(self, '_time_tex_garbage', None)
        if _garbage is None:
            _garbage = []
            self._time_tex_garbage = _garbage
        _garbage.append(tex_id)
        return tex_id, img.width, img.height

    def paint_progress_time(self, node: ProgressTimeNode) -> None:
        """Paint standalone progress time display (elapsed / total).

        Renders via PIL + _upload_texture with text-string caching —
        same proven path as the arc/bar progress textures.
        """
        if not node.visible:
            return
        if getattr(self, '_no_timeline', False):
            return
        pos = self._get_progress_position()
        dur = self._get_progress_duration()
        fmt = node.data.get("format", "elapsed_total")
        sep = node.data.get("separator", " / ")
        has_dur = dur > 0

        if fmt == "elapsed":
            text = format_time(pos)
        elif fmt == "remaining":
            text = f"-{format_time(max(0, dur - pos))}" if has_dur else "--:--"
        else:
            text = f"{format_time(pos)}{sep}{format_time(dur) if has_dur else '--:--'}"

        font_family = node.data.get("font_family", "Microsoft YaHei")
        font_size = int(node.data.get("font_size", 10))
        font_bold = node.data.get("font_bold", False)
        color_rgb = self._hex_to_rgb(node.data.get("color", "#cccccc"))
        outline_color = node.data.get("outline_color", "")
        outline_width = int(float(node.data.get("outline_width", 0)))
        if outline_width > 0 and not outline_color:
            outline_color = "#000000"
        outline_rgb = self._hex_to_rgb(outline_color) if outline_width > 0 else (0, 0, 0)

        tex_id, tex_w, tex_h = self._get_time_texture(
            text, font_family, font_size, font_bold, color_rgb,
            outline_width=outline_width, outline_rgb=outline_rgb)
        self._diag_time_text("standalone", text, pos, dur, tex_id, tex_w, tex_h,
                             node.abs_x, node.abs_y, node)
        self._draw_textured_quad_at(
            node.abs_x, node.abs_y, tex_w, tex_h, tex_id, node.opacity)

    def paint_placeholder(self, screen_w: int, screen_h: int) -> None:
        """Draw an idle placeholder when no song is playing."""
        text = "QQ音乐未在播放"
        font_size = 16
        tex_id = self._text_renderer.render_to_texture(
            text, "Microsoft YaHei", font_size, False, (140, 140, 170))
        cache_key = f"{text}|Microsoft YaHei|{font_size}|False|(140, 140, 170)|0|center"  # matches render_to_texture default
        tex_w, tex_h = self._text_renderer.get_texture_size(cache_key)

        # Center the text on screen
        x = (screen_w - tex_w) / 2
        y = (screen_h - tex_h) / 2
        self._draw_textured_quad_at(x, y, tex_w, tex_h, tex_id, 0.8)

    # ================================================================
    # Internal drawing helpers
    # ================================================================

    def _draw_textured_quad(self, node, tex_id, tex_w, tex_h, opacity):
        shader = get_shader("default")
        shader.use()
        shader.set_texture("uTexture", 0, tex_id)
        shader.set_float("uOpacity", opacity)
        shader.set_vec4("uColor", 1.0, 1.0, 1.0, 1.0)
        shader.set_vec2("uOffset", node.abs_x, node.abs_y)
        shader.set_vec2("uScale", node.scale_x, node.scale_y)
        shader.set_float("uRotation", node.rotation)
        shader.set_vec2("uSize", tex_w, tex_h)
        shader.set_vec2("uScreenSize", self.screen_w, self.screen_h)
        glEnable(GL_BLEND)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)  # Pre-multiplied alpha for UpdateLayeredWindow
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLES, 0, 6)

    def _draw_textured_quad_at(self, x, y, w, h, tex_id, opacity):
        shader = get_shader("default")
        shader.use()
        shader.set_texture("uTexture", 0, tex_id)
        shader.set_float("uOpacity", opacity)
        shader.set_vec4("uColor", 1.0, 1.0, 1.0, 1.0)
        shader.set_vec2("uOffset", x, y)
        shader.set_vec2("uScale", 1.0, 1.0)
        shader.set_float("uRotation", 0.0)
        shader.set_vec2("uSize", w, h)
        shader.set_vec2("uScreenSize", self.screen_w, self.screen_h)
        glEnable(GL_BLEND)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLES, 0, 6)

    def _draw_with_outline(self, node, tex_id, outline_tex_id, tex_w, tex_h,
                           outline_width):
        # Draw outline-colored copies offset in 8 directions
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx == 0 and dy == 0:
                    continue
                self._draw_textured_quad_at(
                    node.abs_x + dx, node.abs_y + dy,
                    tex_w, tex_h, outline_tex_id, node.opacity)
        # Draw main text on top
        self._draw_textured_quad(node, tex_id, tex_w, tex_h, node.opacity)

    def _draw_with_shadow(self, node, tex_id, tex_w, tex_h):
        offset_x = int(node.data.get("shadow_offset_x", 2))
        offset_y = int(node.data.get("shadow_offset_y", 2))
        self._draw_textured_quad_at(
            node.abs_x + offset_x, node.abs_y + offset_y,
            tex_w, tex_h, tex_id, node.opacity * 0.5)
        self._draw_textured_quad(node, tex_id, tex_w, tex_h, node.opacity)

    # Cache for bar progress textures: (w, h, pct_key, color_key) → tex_id
    _bar_tex_cache: dict[tuple, tuple[int, float]] = {}

    def _render_bar_texture(self, w, h, progress, bg_color, fg_color, direction="lr"):
        from PIL import Image, ImageDraw

        # ★ Cache by 1% progress granularity — avoids per-frame PIL allocation
        # + GL upload for a texture that changes imperceptibly between frames.
        pct_key = int(progress * 100)
        color_key = (bg_color[0], bg_color[1], bg_color[2],
                     fg_color[0], fg_color[1], fg_color[2])
        cache_key = (w, h, pct_key, color_key, direction)
        if cache_key in self._bar_tex_cache:
            tex_id, _ = self._bar_tex_cache[cache_key]
            return tex_id

        scale = 2
        sw, sh = w * scale, h * scale
        radius = sh // 2
        img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, sw - 1, sh - 1), radius=radius,
                               fill=bg_color + (255,))
        if progress > 0:
            fw = int(sw * progress)
            fg_layer = Image.new("RGBA", (sw, sh), fg_color + (255,))
            mask = Image.new("L", (sw, sh), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, sw - 1, sh - 1), radius=radius, fill=255)
            fg_clipped = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
            fg_clipped.paste(fg_layer, (0, 0), mask)
            # ★ Direction support: lr = left-to-right fill, rl = right-to-left fill
            if direction == "rl":
                img.paste(fg_clipped.crop((sw - fw, 0, sw, sh)), (sw - fw, 0))
            else:
                img.paste(fg_clipped.crop((0, 0, fw, sh)), (0, 0))
        img = img.resize((w, h), Image.LANCZOS)
        tex_id = self._upload_texture(img)
        self._bar_tex_cache[cache_key] = (tex_id, time.time())

        # Clean old cache entries (keep last 30)
        if len(self._bar_tex_cache) > 30:
            stale = sorted(self._bar_tex_cache.items(),
                          key=lambda x: x[1][1])[:5]
            for k, (tid, _) in stale:
                try:
                    _delete_texture(tid)
                except Exception:
                    pass
                del self._bar_tex_cache[k]

        return tex_id

    # Cache for arc progress textures: (size, progress_pct, mode, color_key) → tex_id
    _arc_tex_cache: dict[tuple, tuple[int, float]] = {}

    def _render_arc_texture(self, size: int, progress: float,
                             mode: str, node, color: tuple) -> int:
        """Render arc/ring/roundrect progress to a GL texture via PIL.

        All modes use PIL's draw.arc() for smooth anti-aliased curves.
        Round caps are drawn as filled circles at arc ends when cap=round.
        Textures are cached by progress percentage (1% granularity).

        Mode differences:
          - arc:       progress arc only, no background
          - ring:      full background circle + progress arc (donut-chart style)
          - roundrect: full background rounded-rect + progress arc overlay
        """
        import math
        from PIL import Image, ImageDraw

        # ★ Read arc-specific settings (isolated from bar mode)
        arc_cfg = node.data.get("arc", {})
        ss_str = arc_cfg.get("supersample", node.data.get("supersample", "2X"))
        ss = int(ss_str.replace("X", "").strip()) if isinstance(ss_str, str) else 2
        ss = max(1, min(6, ss))

        # Parse cap style
        cap_style = arc_cfg.get("cap_style", node.data.get("cap", "flat"))  # round | flat

        # Background color for ring/roundrect modes
        bg_hex = node.data.get("background_color", "#333355")
        bg_color = self._hex_to_rgb(bg_hex)

        pct_key = int(progress * 100)  # 1% cache granularity
        color_key = f"{color[0]},{color[1]},{color[2]}"
        bg_key = f"{bg_color[0]},{bg_color[1]},{bg_color[2]}"
        cache_key = (size, ss, pct_key, mode, color_key, cap_style, bg_key)

        # Check cache
        if cache_key in self._arc_tex_cache:
            tex_id, _ = self._arc_tex_cache[cache_key]
            return tex_id

        ss_size = int(size * ss)
        img = Image.new("RGBA", (ss_size, ss_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pw = max(2, int(node.data.get("width", 4)) * ss)
        margin = pw // 2 + 2  # keep arc inside texture bounds
        r_outer = ss_size / 2 - margin
        if r_outer <= 0:
            tex_id = self._upload_texture(img)
            self._arc_tex_cache[cache_key] = (tex_id, time.time())
            return tex_id

        start_map = {
            "top": -90, "top-right": -45,
            "right": 0, "bottom-right": 45,
            "bottom": 90, "bottom-left": 135,
            "left": 180, "top-left": -135,
        }
        arc_cfg = node.data.get("arc", {})
        start_angle = start_map.get(
            arc_cfg.get("start_position", node.data.get("start_position", "top")), -90)
        direction = arc_cfg.get("direction", node.data.get("direction", "cw"))
        sweep = 360 * progress
        is_ccw = "ccw" in str(direction)

        cx, cy = ss_size / 2, ss_size / 2
        bbox = (margin, margin, ss_size - margin, ss_size - margin)

        # ★ Ring mode: draw full background circle (donut-chart style)
        if mode == "ring":
            draw.arc(bbox, 0, 360, fill=bg_color, width=pw)

        # ★ Roundrect / Square mode: segmented path (rounded-rect or sharp rectangle).
        # Corner radius follows cover border_radius; square → 0 (sharp corners).
        rr_radius = 0
        if mode in ("roundrect", "square"):
            cover_r = float(getattr(self, '_cover_border_radius', 20))
            if mode == "square":
                cover_r = 0
            cover_sz = max(1, getattr(self, '_cover_size', 180))
            rr_radius = max(0, int(cover_r * (size / cover_sz) * ss))

        # ★ Draw progress portion
        if mode in ("roundrect", "square"):
            self._draw_roundrect_progress(
                draw, bbox, rr_radius, start_angle, sweep, color, pw,
                cap_style, progress, ss_size, margin,
            )
        else:
            # Arc / ring — circular progress arc
            if cap_style == "round" and progress > 0.001:
                _r_cap = pw / 2
                _r_center = r_outer - pw / 2
                _cap_angles = [
                    math.radians(start_angle),
                    math.radians(start_angle + (sweep if not is_ccw else -sweep)),
                ]
                for _ang in _cap_angles:
                    _ex = cx + _r_center * math.cos(_ang)
                    _ey = cy + _r_center * math.sin(_ang)
                    draw.ellipse(
                        (_ex - _r_cap, _ey - _r_cap, _ex + _r_cap, _ey + _r_cap),
                        fill=color,
                    )
            if is_ccw:
                draw.arc(bbox, start_angle - sweep, start_angle,
                         fill=color, width=pw)
            else:
                draw.arc(bbox, start_angle, start_angle + sweep,
                         fill=color, width=pw)

        # FLIP_TOP_BOTTOM compensates shader Y-flip for arc orientation.
        # Shader has no X-flip → no horizontal compensation needed.
        # Arc is centered → flip doesn't shift its position.
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        img = img.resize((size, size), Image.LANCZOS)
        tex_id = self._upload_texture(img)
        self._arc_tex_cache[cache_key] = (tex_id, time.time())

        # Clean old cache entries (keep last ARC_CACHE_MAX)
        if len(self._arc_tex_cache) > ARC_CACHE_MAX:
            stale = sorted(self._arc_tex_cache.items(),
                          key=lambda x: x[1][1])[:ARC_CACHE_EVICT]
            for k, (tid, _) in stale:
                try:
                    _delete_texture(tid)
                except Exception:
                    pass
                del self._arc_tex_cache[k]

        return tex_id

    @staticmethod
    def _draw_roundrect_progress(draw, bbox, rr_radius, start_angle_deg, sweep_deg,
                                  color, pw, cap_style, progress, ss_size, margin):
        """Draw progress along a rounded-rectangle path via 9 segments.

        Path (clockwise from top-center):
          0: top-edge right half (straight)
          1: top-right corner (quarter-circle arc, PIL -90→0°)
          2: right edge (straight)
          3: bottom-right corner (arc, 0→90°)
          4: bottom edge (straight, right→left)
          5: bottom-left corner (arc, 90→180°)
          6: left edge (straight)
          7: top-left corner (arc, 180→270°)
          8: top-edge left half (straight, back to top-center)
        """
        import math

        x1, y1, x2, y2 = bbox
        r = rr_radius
        W = x2 - x1
        H = y2 - y1
        hw = W / 2

        if r <= 0 or W <= 2 * r or H <= 2 * r:
            if r <= 0:
                # Square mode: sharp-corner rectangle (4 edges, no arcs)
                Painter._draw_square_progress(
                    draw, bbox, start_angle_deg, sweep_deg, color, pw,
                    cap_style, progress,
                )
                return
            draw.arc(bbox, start_angle_deg, start_angle_deg + sweep_deg,
                     fill=color, width=pw)
            return

        # ---- segment definitions ----
        # (type, params...)
        # line: ("L", sx, sy, ex, ey)
        # arc:  ("A", cx, cy, cr, start_deg, end_deg)
        segs = [
            ("L", x1 + hw, y1, x2 - r, y1),             # 0: top-right half
            ("A", x2 - r, y1 + r, r, -90.0, 0.0),       # 1: top-right corner
            ("L", x2, y1 + r, x2, y2 - r),               # 2: right edge
            ("A", x2 - r, y2 - r, r, 0.0, 90.0),         # 3: bottom-right corner
            ("L", x2 - r, y2, x1 + r, y2),               # 4: bottom edge
            ("A", x1 + r, y2 - r, r, 90.0, 180.0),       # 5: bottom-left corner
            ("L", x1, y2 - r, x1, y1 + r),               # 6: left edge
            ("A", x1 + r, y1 + r, r, 180.0, 270.0),      # 7: top-left corner
            ("L", x1 + r, y1, x1 + hw, y1),              # 8: top-left half
        ]

        quarter_arc = (math.pi / 2.0) * r
        seg_lens = [
            hw - r, quarter_arc, H - 2 * r, quarter_arc,
            W - 2 * r, quarter_arc, H - 2 * r, quarter_arc, hw - r,
        ]
        total = sum(seg_lens)

        # Cumulative start position of each segment
        cum = [0.0]
        for sl in seg_lens[:-1]:
            cum.append(cum[-1] + sl)

        # Map start_angle → perimeter position (cardinal + corners)
        start_map = {
            -90:  0.0,                                                       # top-center
            -45:  cum[1] + seg_lens[1] / 2,                                 # top-right corner
            0:    cum[0] + seg_lens[0] + seg_lens[1] + seg_lens[2] / 2,   # right-center
            45:   cum[3] + seg_lens[3] / 2,                                 # bottom-right corner
            90:   cum[0] + seg_lens[0] + seg_lens[1] + seg_lens[2]
                  + seg_lens[3] + seg_lens[4] / 2,                          # bottom-center
            135:  cum[5] + seg_lens[5] / 2,                                 # bottom-left corner
            180:  cum[0] + seg_lens[0] + seg_lens[1] + seg_lens[2]
                  + seg_lens[3] + seg_lens[4] + seg_lens[5] + seg_lens[6] / 2,  # left-center
            -135: cum[7] + seg_lens[7] / 2,                                 # top-left corner
        }
        pos_start = start_map.get(start_angle_deg, 0.0)

        # Progress distance along perimeter (handle ccw)
        if sweep_deg < 0:
            progress_dist = (abs(sweep_deg) / 360.0) * total
            # Walk backwards
            pos_a = pos_start
            pos_b = (pos_start - progress_dist) % total
        else:
            progress_dist = (sweep_deg / 360.0) * total
            pos_a = pos_start
            pos_b = (pos_start + progress_dist) % total

        # ---- helper: draw a sub-segment ----
        def _draw_sub(seg_idx, frac_a, frac_b):
            """Draw portion of segment seg_idx from frac_a to frac_b."""
            frac_a = max(0.0, min(1.0, frac_a))
            frac_b = max(0.0, min(1.0, frac_b))
            if frac_b <= frac_a + 0.0001:
                return
            st, *sp = segs[seg_idx]
            if st == "L":
                sx, sy, ex, ey = sp
                dx, dy = ex - sx, ey - sy
                draw.line(
                    [(sx + dx * frac_a, sy + dy * frac_a),
                     (sx + dx * frac_b, sy + dy * frac_b)],
                    fill=color, width=pw)
            else:  # "A"
                cx, cy, cr, a0, a1 = sp
                span = a1 - a0
                # ★ PIL draw.arc() draws stroke INWARD from ellipse boundary.
                # draw.line() and draw.rounded_rectangle(outline=) are CENTERED.
                # Expand bbox by pw/2 so the arc midline aligns with the lines.
                adj = cr + pw / 2.0
                bb = (cx - adj, cy - adj, cx + adj, cy + adj)
                draw.arc(bb, a0 + span * frac_a, a0 + span * frac_b,
                         fill=color, width=pw)

        # ---- draw segments in [pos_a, pos_b] ----
        if abs(pos_b - pos_a) < 0.001:
            pass  # zero progress, nothing to draw
        elif pos_a <= pos_b:
            # Simple: forward within one perimeter lap
            for i in range(len(segs)):
                cs, ce = cum[i], cum[i] + seg_lens[i]
                if ce <= pos_a or cs >= pos_b:
                    continue
                fa = (pos_a - cs) / seg_lens[i] if cs < pos_a else 0.0
                fb = (pos_b - cs) / seg_lens[i] if ce > pos_b else 1.0
                _draw_sub(i, fa, fb)
        else:
            # Wrap around: [pos_a, total] then [0, pos_b]
            for i in range(len(segs)):
                cs, ce = cum[i], cum[i] + seg_lens[i]
                # Part 1: pos_a → total
                if ce > pos_a and cs < total:
                    fa = (pos_a - cs) / seg_lens[i] if cs < pos_a else 0.0
                    fb = (total - cs) / seg_lens[i] if ce > total else 1.0
                    _draw_sub(i, max(0, fa), min(1, fb))
                # Part 2: 0 → pos_b
                if ce > 0 and cs < pos_b:
                    fa = 0.0
                    fb = (pos_b - cs) / seg_lens[i] if ce > pos_b else 1.0
                    _draw_sub(i, 0.0, max(0, fb))

        # ---- caps (points on path at pos_a and pos_b) ----
        def _pt(dist):
            d = dist % total
            for i in range(len(segs)):
                cs, ce = cum[i], cum[i] + seg_lens[i]
                if d < ce or i == len(segs) - 1:
                    frac = (d - cs) / seg_lens[i] if seg_lens[i] > 0 else 0
                    frac = max(0.0, min(1.0, frac))
                    st, *sp = segs[i]
                    if st == "L":
                        sx, sy, ex, ey = sp
                        return (sx + (ex - sx) * frac, sy + (ey - sy) * frac)
                    else:
                        cx, cy, cr, a0, a1 = sp
                        ang = math.radians(a0 + (a1 - a0) * frac)
                        return (cx + cr * math.cos(ang), cy + cr * math.sin(ang))
            return (x1 + hw, y1)

        if cap_style == "round" and progress > 0.001:
            _r_cap = pw / 2
            for dist in (pos_a, pos_b):
                px, py = _pt(dist)
                draw.ellipse(
                    (px - _r_cap, py - _r_cap, px + _r_cap, py + _r_cap),
                    fill=color,
                )

    @staticmethod
    def _draw_square_progress(draw, bbox, start_angle_deg, sweep_deg,
                               color, pw, cap_style, progress):
        """Draw progress along a rectangular (sharp-corner) path.

        Path clockwise from top-center: 5 straight line segments.
        """
        import math

        x1, y1, x2, y2 = bbox
        W = x2 - x1
        H = y2 - y1
        hw = W / 2
        hh = H / 2

        # 5 segments clockwise from top-center
        segs = [
            ("L", x1 + hw, y1, x2, y1),         # 0: top right half
            ("L", x2, y1, x2, y2),              # 1: right edge
            ("L", x2, y2, x1, y2),              # 2: bottom edge
            ("L", x1, y2, x1, y1),              # 3: left edge
            ("L", x1, y1, x1 + hw, y1),         # 4: top left half
        ]
        seg_lens = [hw, H, W, H, hw]
        total = sum(seg_lens)

        cum = [0.0]
        for sl in seg_lens[:-1]:
            cum.append(cum[-1] + sl)

        # Map start_angle → perimeter position (cardinal + corners)
        start_map = {
            -90:  0.0,                   # top-center
            -45:  hw,                    # top-right corner
            0:    hw + H / 2,           # right-center
            45:   hw + H,               # bottom-right corner
            90:   hw + H + W / 2,      # bottom-center
            135:  hw + H + W,          # bottom-left corner
            180:  hw + H + W + H / 2, # left-center
            -135: hw + H + W + H,     # top-left corner
        }
        pos_start = start_map.get(start_angle_deg, 0.0)

        if sweep_deg < 0:
            dist = (abs(sweep_deg) / 360.0) * total
            pos_a, pos_b = pos_start, (pos_start - dist) % total
        else:
            dist = (sweep_deg / 360.0) * total
            pos_a, pos_b = pos_start, (pos_start + dist) % total

        # Draw segments in [pos_a, pos_b]
        def _draw_seg(idx, fa, fb):
            fa, fb = max(0.0, min(1.0, fa)), max(0.0, min(1.0, fb))
            if fb <= fa + 0.0001:
                return
            sx, sy, ex, ey = segs[idx][1:]
            dx, dy = ex - sx, ey - sy
            draw.line(
                [(sx + dx * fa, sy + dy * fa),
                 (sx + dx * fb, sy + dy * fb)],
                fill=color, width=pw)

        if abs(pos_b - pos_a) < 0.001:
            pass
        elif pos_a <= pos_b:
            for i in range(len(segs)):
                cs, ce = cum[i], cum[i] + seg_lens[i]
                if ce <= pos_a or cs >= pos_b:
                    continue
                fa = (pos_a - cs) / seg_lens[i] if cs < pos_a else 0.0
                fb = (pos_b - cs) / seg_lens[i] if ce > pos_b else 1.0
                _draw_seg(i, fa, fb)
        else:
            for i in range(len(segs)):
                cs, ce = cum[i], cum[i] + seg_lens[i]
                if ce > pos_a and cs < total:
                    fa = (pos_a - cs) / seg_lens[i] if cs < pos_a else 0.0
                    fb = (total - cs) / seg_lens[i] if ce > total else 1.0
                    _draw_seg(i, max(0, fa), min(1, fb))
                if ce > 0 and cs < pos_b:
                    _draw_seg(i, 0.0, max(0, (pos_b - cs) / seg_lens[i] if ce > pos_b else 1.0))

        # Caps
        def _pt(dist):
            d = dist % total
            for i in range(len(segs)):
                cs, ce = cum[i], cum[i] + seg_lens[i]
                if d < ce or i == len(segs) - 1:
                    frac = (d - cs) / seg_lens[i] if seg_lens[i] > 0 else 0
                    frac = max(0.0, min(1.0, frac))
                    sx, sy, ex, ey = segs[i][1:]
                    return (sx + (ex - sx) * frac, sy + (ey - sy) * frac)
            return (x1 + hw, y1)

        if cap_style == "round" and progress > 0.001:
            r_cap = pw / 2
            for dist in (pos_a, pos_b):
                px, py = _pt(dist)
                draw.ellipse(
                    (px - r_cap, py - r_cap, px + r_cap, py + r_cap),
                    fill=color,
                )

    def _paint_time_text(self, node):
        mode = node.data.get("mode", "bar")
        # ★ Read time position from nested time_text config (was reading flat "time_position" which never exists)
        time_cfg = node.data.get("time_text", {})
        time_pos = time_cfg.get("position", "inside")
        color_rgb = self._hex_to_rgb(time_cfg.get("color") or node.data.get("time_color", "#cccccc"))
        pos = self._get_progress_position()
        dur = self._get_progress_duration()
        time_text = f"{format_time(pos)} / {format_time(dur)}"
        font_family = time_cfg.get("font", {}).get("family") or node.data.get("time_font_family", "Microsoft YaHei")
        font_size = int(time_cfg.get("font", {}).get("size",
                        node.data.get("time_font_size") or node.data.get("font_size", 10)))
        outline_color = time_cfg.get("outline_color", "")
        outline_width = int(float(time_cfg.get("outline_width", 0)))
        if outline_width > 0 and not outline_color:
            outline_color = "#000000"
        outline_rgb = self._hex_to_rgb(outline_color) if outline_width > 0 else (0, 0, 0)

        # Pre-composite outline into the time texture so both bar time text
        # and standalone progress_time use the same reliable outline path.
        tex_id, tex_w, tex_h = self._get_time_texture(
            time_text, font_family, font_size, False, color_rgb,
            outline_width=outline_width, outline_rgb=outline_rgb)

        # ★ Arc/ring mode: position time text relative to cover, not progress node
        if mode in ("arc", "ring"):
            cover_cx = self._cover_x + self._cover_size / 2
            cover_bottom = self._cover_y + self._cover_size
            tx = cover_cx - tex_w / 2
            ty = cover_bottom + 8  # centered below the cover
        else:
            # Bar mode: texture is bar_w×bar_h, drawn at node.abs_x (left-aligned).
            # The bar is NOT centered — the shader places it at uOffset directly.
            bar_cfg = node.data.get("bar", {})
            length_ratio = float(bar_cfg.get("length_ratio", node.data.get("length_ratio", 0.7)))
            actual_bar_w = max(20, int(node.width * length_ratio))
            bar_x = node.abs_x                     # texture left edge = node left edge
            bar_y = node.abs_y
            bar_w = actual_bar_w                   # visual bar width
            bar_h = max(4, int(node.data.get("width", 4)))
            # ★ Texture has 4px internal padding — compensate
            tex_pad = 4
            gap = -tex_pad + 1
            if time_pos == "inside":
                tx = bar_x + bar_w / 2 - tex_w / 2
                ty = bar_y + bar_h / 2 - tex_h / 2
            elif time_pos == "left":
                tx = bar_x - tex_w - gap
                ty = bar_y + bar_h / 2 - tex_h / 2
            elif time_pos == "right":
                tx = bar_x + bar_w + gap
                ty = bar_y + bar_h / 2 - tex_h / 2
            elif time_pos == "top":
                tx = bar_x + bar_w / 2 - tex_w / 2
                ty = bar_y - tex_h - gap
            elif time_pos == "top-left":
                tx = bar_x
                ty = bar_y - tex_h - gap
            elif time_pos == "top-right":
                tx = bar_x + bar_w - tex_w
                ty = bar_y - tex_h - gap
            elif time_pos == "bottom-left":
                tx = bar_x
                ty = bar_y + bar_h + gap
            elif time_pos == "bottom-right":
                tx = bar_x + bar_w - tex_w
                ty = bar_y + bar_h + gap
            else:  # "bottom"
                tx = bar_x + bar_w / 2 - tex_w / 2
                ty = bar_y + bar_h + gap
        # ★ Fine-tune offset
        tx += int(float(time_cfg.get("offset_x", 0)))
        ty += int(float(time_cfg.get("offset_y", 0)))
        self._diag_time_text("embedded", time_text, pos, dur, tex_id, tex_w, tex_h,
                             tx, ty, node)
        self._draw_textured_quad_at(tx, ty, tex_w, tex_h, tex_id, node.opacity)

    def _diag_time_text(self, kind: str, text: str, pos: float, dur: float,
                        tex_id: int, tex_w: int, tex_h: int,
                        x: float, y: float, node) -> None:
        """Low-rate diagnostics for the time-text render branch."""
        attr = f"_diag_{kind}_time_text"
        changed = text != getattr(self, attr, None)
        if changed:
            setattr(self, attr, text)
            _render_log.info(
                f"TIME_TEXT_CHANGE kind={kind} node={getattr(node, 'id', '?')} "
                f"text={text!r} pos={pos:.1f}s dur={dur:.1f}s "
                f"tex={tex_id}:{tex_w}x{tex_h} xy=({x:.1f},{y:.1f}) "
                f"visible={getattr(node, 'visible', None)} "
                f"opacity={getattr(node, 'opacity', 0.0):.3f}")
        else:
            _render_log.rate_limited(
                f"time_text_{kind}_stable", 2.0, "INFO",
                f"TIME_TEXT_STABLE kind={kind} node={getattr(node, 'id', '?')} "
                f"text={text!r} pos={pos:.1f}s dur={dur:.1f}s "
                f"tex={tex_id}:{tex_w}x{tex_h} xy=({x:.1f},{y:.1f}) "
                f"opacity={getattr(node, 'opacity', 0.0):.3f}")

    def _update_cover_spin(self, node, spin_cfg: dict | None = None):
        if spin_cfg is None:
            spin_cfg = node.data.get("spin", {})
        spin_dur = float(spin_cfg.get("spin_duration", spin_cfg.get("acceleration", 2.5)))
        stop_dur = float(spin_cfg.get("stop_duration", spin_dur * 0.6))
        now = time.time()

        # ★ Song-change deceleration — slow to a stop at current angle.
        # The cover keeps its rotation (does NOT snap back to 0°) so the
        # fade-out preserves the visual continuity of the spinning cover.
        if getattr(self, '_song_ending', False):
            if not hasattr(self, '_song_end_start') or self._song_end_start is None:
                self._song_end_start = now
            elapsed = now - self._song_end_start
            if elapsed >= stop_dur:
                return  # stop at current angle, do NOT reset to 0
            decel = 1.0 - (elapsed / stop_dur) ** 2
            if decel <= 0:
                return  # stop at current angle, do NOT reset to 0
            dt = min(now - self._last_cover_time, 1.0) if self._last_cover_time > 0 else 0.016
            self._last_cover_time = now
            speed = float(spin_cfg.get("speed", 30))
            delta = speed * dt * decel
            if "ccw" in str(spin_cfg.get("direction", "cw")): delta = -delta
            self._cover_rotation = (self._cover_rotation + delta) % 360
            return

        # ★ Pause → deceleration over stop_dur
        if getattr(self, '_is_paused', False):
            if spin_cfg.get("pause_spin", True):
                if getattr(self, '_diag_spin_pause', 0) < 3:
                    self._diag_spin_pause = getattr(self, '_diag_spin_pause', 0) + 1
                    from core.logs import get_logger
                    get_logger("render").debug(f"SPIN PAUSE: decelerating, _spin_pause_start={getattr(self,'_spin_pause_start','None')}")
                self._spin_was_paused = True
                if not hasattr(self, '_spin_pause_start') or self._spin_pause_start is None:
                    self._spin_pause_start = now
                elapsed = now - self._spin_pause_start
                if elapsed >= stop_dur: return
                decel = 1.0 - (elapsed / stop_dur) ** 2
                if decel <= 0: return
                dt = min(now - self._last_cover_time, 1.0) if self._last_cover_time > 0 else 0.016
                self._last_cover_time = now
                speed = float(spin_cfg.get("speed", 30))
                delta = speed * dt * decel
                if "ccw" in str(spin_cfg.get("direction", "cw")): delta = -delta
                self._cover_rotation = (self._cover_rotation + delta) % 360
            else:
                self._spin_pause_start = None
            return

        # ★ Resume from pause
        self._spin_pause_start = None
        if getattr(self, '_spin_was_paused', False):
            self._spin_was_paused = False
            self._last_cover_time = now
            self._rotation_start_time = now

        if self._last_cover_time <= 0:
            self._last_cover_time = now
            self._rotation_start_time = now
        dt = min(now - self._last_cover_time, 1.0)
        self._last_cover_time = now
        speed = float(spin_cfg.get("speed", 30))
        elapsed = now - self._rotation_start_time
        accel = (elapsed / spin_dur) ** 2 if elapsed < spin_dur else 1.0
        delta = speed * dt * accel
        if "ccw" in str(spin_cfg.get("direction", "cw")): delta = -delta
        self._cover_rotation = (self._cover_rotation + delta) % 360

    # ================================================================
    # Texture management
    # ================================================================

    def _get_or_upload_texture(self, key, img):
        # ★ Only re-upload when the image actually changes.
        # The old code deleted + re-uploaded every frame — that's a
        # 2+ MB PCIe transfer per frame for a 720×720 cover. Fatal for FPS.
        img_id = id(img)
        cache_entry = self._textures.get(key)
        if cache_entry is not None:
            cached_tex_id, cached_img_id = cache_entry
            if cached_img_id == img_id:
                return cached_tex_id  # unchanged — reuse
            # Image changed — delete old texture
            try:
                _delete_texture(cached_tex_id)
            except Exception:
                pass
        # Upload new texture
        tex_id = self._upload_texture(img)
        self._textures[key] = (tex_id, img_id)
        return tex_id

    def _upload_texture(self, img):
        tex_id = _gen_texture()
        glBindTexture(GL_TEXTURE_2D, tex_id)
        # ★ Ensure RGBA format. Local covers may be grayscale (L), paletted (P),
        # or RGB — none of which have an alpha channel.  Calling tobytes("RGBA")
        # on a non-RGBA image produces garbled/truncated data → transparent texture.
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        data = img.tobytes("raw", "RGBA", 0, -1)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, img.width, img.height, 0,
                     GL_RGBA, GL_UNSIGNED_BYTE, data)
        glGenerateMipmap(GL_TEXTURE_2D)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        return tex_id

    def clear_textures(self):
        for tex_id, _ in self._textures.values():
            try:
                _delete_texture(tex_id)
            except Exception:
                pass
        self._textures.clear()
        for tex_id, _ in self._arc_tex_cache.values():
            try:
                _delete_texture(tex_id)
            except Exception:
                pass
        self._arc_tex_cache.clear()
        for tex_id, _, _ in self._bar_tex_cache.values():
            try:
                _delete_texture(tex_id)
            except Exception:
                pass
        self._bar_tex_cache.clear()
        if self._time_tex_garbage:
            for old_tex in self._time_tex_garbage:
                try:
                    _delete_texture(old_tex)
                except Exception:
                    pass
            self._time_tex_garbage.clear()

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
        for tex_id, _ in self._bar_tex_cache.values():
            try:
                _delete_texture(tex_id)
            except Exception:
                pass
        self._bar_tex_cache.clear()
        if self._time_tex_garbage:
            for old_tex in self._time_tex_garbage:
                try:
                    _delete_texture(old_tex)
                except Exception:
                    pass
            self._time_tex_garbage.clear()
        self._text_renderer.clear_cache()

    def cleanup(self):
        self.clear_textures()
        if self._vao:
            try:
                _delete_vertex_array(self._vao)
            except Exception:
                pass
        if self._vbo:
            try:
                _delete_buffer(self._vbo)
            except Exception:
                pass

    # ================================================================
    # Data access
    # ================================================================

    _cover_image: Image.Image | None = None
    _song_name: str = ""
    _artist_name: str = ""
    _lyrics_timed: list = []
    _lyrics_merged: list = []  # New: [(time_ms, main_text, trans_text), ...]
    _progress_pos: float = 0.0
    _progress_dur: float = 0.0

    def set_data(self, **kwargs):
        # ★ Clean up time-text textures from previous frame
        _garbage = getattr(self, '_time_tex_garbage', None)
        if _garbage:
            for old_tex in _garbage:
                try:
                    _delete_texture(old_tex)
                except Exception:
                    pass
            _garbage.clear()
        for key, value in kwargs.items():
            setattr(self, f"_{key}", value)
        # Track data changes through the pipeline
        _new_pos = kwargs.get('progress_pos', -1)
        _last = getattr(self, '_last_setdata_pos', -999)
        if _new_pos >= 0 and abs(_new_pos - _last) > 0.8:
            self._last_setdata_pos = _new_pos
            _render_log.rate_limited(
                "painter_set_data_progress", 1.0, "INFO",
                f"PAINTER_DATA pos={float(_new_pos):.1f}s "
                f"dur={float(kwargs.get('progress_dur') or 0.0):.1f}s "
                f"progress={float(kwargs.get('progress') or 0.0):.4f} "
                f"paused={kwargs.get('is_paused')} "
                f"animating={kwargs.get('is_animating')} "
                f"no_timeline={kwargs.get('no_timeline')}")

    def _get_cover_image(self):      return self._cover_image
    def _get_text_content(self, node):
        source = node.data.get("source", "")
        if source == "song_name":     return self._song_name
        elif source == "artist_name": return self._artist_name
        return node.data.get("text", "")

    def _get_active_bilingual_lines(
        self, progress_ms: int, max_lines: int, bilingual: bool,
    ) -> list[tuple[str, str]]:
        """Get active lyrics lines as (main_text, trans_text) pairs.

        Prefers new _lyrics_merged format, falls back to legacy _lyrics_timed.
        """
        # ★ Lyrics diagnostic: track matching state
        _lx_frame = getattr(self, '_lx_diag_frame', 0) + 1
        self._lx_diag_frame = _lx_frame
        _lx_prev_ms = getattr(self, '_lx_last_ms', -1)

        merged = self._lyrics_merged
        if merged:
            # New structured format
            current_idx = -1
            for i, (t, _m, _tr) in enumerate(merged):
                if t <= progress_ms:
                    current_idx = i
                else:
                    break
            if current_idx < 0:
                # ★ Diagnostic: position before first lyric timestamp
                if _lx_frame % 300 == 1 or (_lx_prev_ms >= 0 and progress_ms != _lx_prev_ms):
                    from core.logs import get_logger
                    get_logger("render").debug(
                        f"LYRIC_MISS merged: ms={progress_ms} first_t={merged[0][0] if merged else -1} "
                        f"len={len(merged)} idx={current_idx}")
                self._lx_last_ms = progress_ms
                return []

            start_idx = max(0, current_idx - max_lines + 1)
            lines = []
            for i in range(start_idx, current_idx + 1):
                _ts, mt, tt = merged[i]
                if not bilingual:
                    tt = ""
                lines.append((mt, tt))
            # Periodic heartbeat
            if _lx_frame % 300 == 1:
                from core.logs import get_logger
                get_logger("render").debug(
                    f"LYRIC_OK merged: ms={progress_ms} idx={current_idx}/{len(merged)} "
                    f"line='{merged[current_idx][1][:40] if current_idx >= 0 else '?'}'")
            self._lx_last_ms = progress_ms
            return lines

        # ---- Legacy format fallback ----
        if not self._lyrics_timed:
            if _lx_frame % 300 == 1:
                from core.logs import get_logger
                get_logger("render").debug(
                    f"LYRIC_EMPTY: ms={progress_ms} timed=[] merged=[]")
            return []

        current_idx = -1
        for i, (t, _text) in enumerate(self._lyrics_timed):
            if t <= progress_ms:
                current_idx = i
            else:
                break

        if current_idx < 0:
            if _lx_frame % 300 == 1 or (_lx_prev_ms >= 0 and progress_ms != _lx_prev_ms):
                from core.logs import get_logger
                get_logger("render").debug(
                    f"LYRIC_MISS legacy: ms={progress_ms} first_t={self._lyrics_timed[0][0] if self._lyrics_timed else -1} "
                    f"len={len(self._lyrics_timed)} idx={current_idx}")
            self._lx_last_ms = progress_ms
            return []

        start_idx = max(0, current_idx - max_lines + 1)
        lines = []
        for i in range(start_idx, current_idx + 1):
            _ts, text = self._lyrics_timed[i]
            if not bilingual:
                nl_pos = text.find("\n  ")
                if nl_pos >= 0:
                    text = text[:nl_pos]
                lines.append((text, ""))
            elif "\n  " in text:
                parts = text.split("\n  ", 1)
                lines.append((parts[0], parts[1]))
            elif "\n" in text:
                parts = text.split("\n", 1)
                lines.append((parts[0], parts[1]))
            else:
                lines.append((text, ""))
        # Periodic heartbeat
        if _lx_frame % 300 == 1:
            from core.logs import get_logger
            get_logger("render").debug(
                f"LYRIC_OK legacy: ms={progress_ms} idx={current_idx}/{len(self._lyrics_timed)} "
                f"line='{self._lyrics_timed[current_idx][1][:40] if current_idx >= 0 else '?'}'")
        self._lx_last_ms = progress_ms
        return lines

    def _render_lyrics_lines(
        self, node, active_lines, *,
        main_color, main_family, main_size, main_bold,
        trans_color, trans_family, trans_size,
        max_w, align, line_spacing,
        outline_color, outline_width, mode,
    ):
        """Render lyrics lines with visual differentiation.

        For merged mode: render all main lines as one texture, all trans as another,
        then stack vertically.

        For separate mode: render each (main, trans) pair sequentially for
        interleaved display.
        """
        if mode == "separate" and len(active_lines) > 1:
            # Per-pair interleaved rendering for separate mode
            self._render_lyrics_separate(
                node, active_lines,
                main_color=main_color, main_family=main_family, main_size=main_size,
                main_bold=main_bold,
                trans_color=trans_color, trans_family=trans_family, trans_size=trans_size,
                max_w=max_w, align=align, line_spacing=line_spacing,
                outline_color=outline_color, outline_width=outline_width,
            )
        else:
            # Merged block rendering (one main block + one trans block)
            self._render_lyrics_merged(
                node, active_lines,
                main_color=main_color, main_family=main_family, main_size=main_size,
                main_bold=main_bold,
                trans_color=trans_color, trans_family=trans_family, trans_size=trans_size,
                max_w=max_w, align=align, line_spacing=line_spacing,
                outline_color=outline_color, outline_width=outline_width,
                mode=mode,
            )

    def _render_lyrics_merged(
        self, node, active_lines, *,
        main_color, main_family, main_size, main_bold,
        trans_color, trans_family, trans_size,
        max_w, align, line_spacing,
        outline_color, outline_width, mode,
    ):
        """Render lyrics as stacked main block + trans block."""
        main_lines_text = [mt for mt, _tt in active_lines if mt]
        trans_lines_text = [tt for _mt, tt in active_lines if tt]

        main_block = "\n".join(main_lines_text)
        has_trans = any(trans_lines_text)

        # Render main texture
        main_tex_id = None
        main_tex_w = main_tex_h = 0
        if main_block.strip():
            main_tex_id = self._text_renderer.render_to_texture(
                main_block, main_family, main_size, main_bold, main_color, max_w, align)
            main_cache = f"{main_block}|{main_family}|{main_size}|{main_bold}|{main_color}|{max_w}|{align}"  # no outline suffix — matches render_to_texture default
            main_tex_w, main_tex_h = self._text_renderer.get_texture_size(main_cache)

        # Render trans texture
        trans_tex_id = None
        trans_tex_w = trans_tex_h = 0
        if has_trans:
            trans_block = "\n".join(trans_lines_text)
            if trans_block.strip():
                trans_tex_id = self._text_renderer.render_to_texture(
                    trans_block, trans_family, trans_size, False, trans_color, max_w, align)
                trans_cache = f"{trans_block}|{trans_family}|{trans_size}|False|{trans_color}|{max_w}|{align}"  # matches render_to_texture default (no outline)
                trans_tex_w, trans_tex_h = self._text_renderer.get_texture_size(trans_cache)

        # Calculate layout
        gap = line_spacing + 2
        if mode == "separate":
            gap = line_spacing + 6

        total_h = main_tex_h + (trans_tex_h + gap if has_trans else 0)
        start_y = node.abs_y + max(0, (node.height - total_h) / 2)

        # ★ Block X position follows text alignment
        def _block_x(tex_w):
            if align == "right":
                return node.abs_x + node.width - tex_w
            elif align == "center":
                return node.abs_x + (node.width - tex_w) / 2
            return float(node.abs_x)  # left

        # Draw main block
        if main_tex_id:
            mx = _block_x(main_tex_w)
            self._draw_lyrics_textured(mx, start_y, main_tex_w, main_tex_h,
                                       main_tex_id, node.opacity,
                                       main_block, main_family, main_size, main_bold,
                                       outline_color, outline_width, max_w, align)

        # Draw trans block below
        if trans_tex_id:
            tx = _block_x(trans_tex_w)
            ty = start_y + main_tex_h + gap
            trans_block = "\n".join(trans_lines_text)
            self._draw_lyrics_textured(tx, ty, trans_tex_w, trans_tex_h,
                                       trans_tex_id, node.opacity * 0.95,
                                       trans_block, trans_family, trans_size, False,
                                       outline_color, max(1, outline_width // 2), max_w, align)

    def _render_lyrics_separate(
        self, node, active_lines, *,
        main_color, main_family, main_size, main_bold,
        trans_color, trans_family, trans_size,
        max_w, align, line_spacing,
        outline_color, outline_width,
    ):
        """Render each lyrics line with its translation interleaved.

        Layout per line pair:
          [main text]
          [trans text]  ← dimmer, smaller font
          <gap>
        """
        # First pass: measure all line pairs (use measure_text to avoid GL upload)
        pair_sizes = []
        total_h = 0
        for mt, tt in active_lines:
            mh = th = 0
            mw = tw = 0
            if mt:
                mw, mh = self._text_renderer.measure_text(
                    mt, main_family, main_size, main_bold, max_w)
            if tt:
                tw, th = self._text_renderer.measure_text(
                    tt, trans_family, trans_size, False, max_w)
            pair_gap = line_spacing + 2 if tt else 0
            pair_h = mh + (th + pair_gap if tt else 0)
            pair_sizes.append((mt, tt, mw, mh, tw, th, pair_h))
            total_h += pair_h

        # Add gaps between pairs
        inter_pair_gap = line_spacing + 4
        total_h += inter_pair_gap * (len(active_lines) - 1)

        # ★ Block X position follows text alignment
        def _block_x(tex_w):
            if align == "right":
                return node.abs_x + node.width - tex_w
            elif align == "center":
                return node.abs_x + (node.width - tex_w) / 2
            return float(node.abs_x)  # left

        # Second pass: render each pair
        y = node.abs_y + max(0, (node.height - total_h) / 2)
        for mt, tt, mw, mh, tw, th, pair_h in pair_sizes:
            # Main text
            if mt:
                mx = _block_x(mw)
                self._draw_lyrics_textured(mx, y, mw, mh,
                                           self._text_renderer.render_to_texture(
                                               mt, main_family, main_size, main_bold,
                                               main_color, max_w, align),
                                           node.opacity,
                                           mt, main_family, main_size, main_bold,
                                           outline_color, outline_width, max_w, align)
            y += mh

            # Trans text
            if tt:
                tx = _block_x(tw)
                ty = y + line_spacing + 2
                self._draw_lyrics_textured(tx, ty, tw, th,
                                           self._text_renderer.render_to_texture(
                                               tt, trans_family, trans_size, False,
                                               trans_color, max_w, align),
                                           node.opacity * 0.95,
                                           tt, trans_family, trans_size, False,
                                           outline_color, max(1, outline_width // 2),
                                           max_w, align)
                y = ty + th
            else:
                y += line_spacing + 2

            y += inter_pair_gap

    def _draw_lyrics_textured(
        self, x, y, w, h, tex_id, opacity,
        text, family, size, bold, outline_color, outline_width, max_w, align,
    ):
        """Draw a lyrics text texture with optional outline."""
        if outline_width > 0:
            if not outline_color or not outline_color.startswith("#"):
                outline_color = "#000000"
            outline_rgb = self._hex_to_rgb(outline_color)
            outline_tex_id = self._text_renderer.render_to_texture(
                text, family, size, bold, outline_rgb, max_w, align)
            self._draw_with_outline_at(x, y, w, h, tex_id, outline_tex_id,
                                       outline_width, opacity)
        else:
            self._draw_textured_quad_at(x, y, w, h, tex_id, opacity)

    def _draw_with_outline_at(self, x, y, w, h, tex_id, outline_tex_id,
                               outline_width, opacity):
        """Draw outline offset in 8 directions, then main text on top."""
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx == 0 and dy == 0:
                    continue
                self._draw_textured_quad_at(x + dx, y + dy, w, h,
                                            outline_tex_id, opacity)
        self._draw_textured_quad_at(x, y, w, h, tex_id, opacity)

    # Legacy fallback — kept for backward compat
    def _get_lyrics_text(self, node):
        """Legacy: get lyrics text as single combined string."""
        if not self._lyrics_timed:
            return ""
        ms = self._progress_pos * 1000 + node.data.get("time_offset_ms", 0)
        bilingual = node.data.get("bilingual", True)
        max_lines = int(float(node.data.get("max_lines", 1)))

        current_idx = -1
        for i, (t, _text) in enumerate(self._lyrics_timed):
            if t <= ms:
                current_idx = i
            else:
                break

        if current_idx < 0:
            return ""

        start_idx = max(0, current_idx - max_lines + 1)
        lines = []
        for i in range(start_idx, current_idx + 1):
            _ts, text = self._lyrics_timed[i]
            if not bilingual:
                nl_pos = text.find("\n  ")
                if nl_pos >= 0:
                    text = text[:nl_pos]
            lines.append(text)

        return "\n".join(lines)

    def _get_progress(self):
        dur = self._progress_dur
        return min(1.0, self._progress_pos / dur) if dur > 0 else 0.0

    def _get_progress_position(self): return self._progress_pos
    def _get_progress_duration(self): return self._progress_dur

    @staticmethod
    def _hex_to_rgb(hex_color):
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 3:
            hex_color = "".join(c * 2 for c in hex_color)
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
