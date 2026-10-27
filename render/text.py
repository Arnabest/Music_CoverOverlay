"""High-quality text rendering using PIL → OpenGL texture.

Rather than building a glyph atlas (complex, fragile), we render entire text
strings to PIL RGBA images with full anti-aliasing, then upload as GL textures.
This gives excellent quality at the cost of some VRAM — acceptable for the
small number of text elements in an overlay.
"""
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
from pyglet.gl import (
    GL_TEXTURE_2D, GL_RGBA, GL_UNSIGNED_BYTE,
    GL_LINEAR, GL_LINEAR_MIPMAP_LINEAR,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE,
    glGenTextures, glBindTexture, glTexImage2D,
    glTexParameteri, glGenerateMipmap, glDeleteTextures,
    GLuint,
)
import ctypes
import os
from collections import OrderedDict
import struct

from core.logs import get_logger

_txt_log = get_logger("text")


# ── TrueType name table parser ──────────────────────────────────────────────
# Parses the 'name' table inside .ttf/.ttc/.otf files to extract font family
# names in ALL languages (English + Chinese + etc.), without requiring fontTools.
#
# This is needed because tkinter.font.families() returns CJK fonts by their
# Chinese name (e.g. "等线 Light") while the Windows registry and PIL
# getname() only return English names ("DengXian Light").  The name table
# inside the font file itself contains both, so we parse it to build a
# complete multi-language → filepath mapping.

def _read_name_table(font_path: str) -> dict[int, str]:
    """Parse the TrueType/OpenType name table from a font file.

    Returns a dict mapping nameID → best string, where nameID 1 = Family,
    2 = Subfamily, etc.  Prefers US English (1033) then any language.
    """
    result = {}
    try:
        with open(font_path, 'rb') as f:
            # ── Offset table ──
            head = f.read(12)
            if len(head) < 12:
                return result
            sfversion, num_tables, _, _, _ = struct.unpack('>IHHHH', head)
            tag = 'ttcf' if sfversion == 0x74746366 else ''

            # .ttc files: header has TTC tag + version + num_fonts + offset to first font
            if tag == 'ttcf':
                # We only parse the first face in a TTC
                num_fonts = struct.unpack('>I', f.read(4))[0]
                offsets = []
                for _i in range(num_fonts):
                    offsets.append(struct.unpack('>I', f.read(4))[0])
                if not offsets:
                    return result
                f.seek(offsets[0])
                head2 = f.read(12)
                if len(head2) < 12:
                    return result
                sfversion2, num_tables2, _, _, _ = struct.unpack('>IHHHH', head2[:12])
                sfversion, num_tables = sfversion2, num_tables2

            # ── Find 'name' table ──
            name_offset = None
            name_length = 0
            for _i in range(num_tables):
                rec = f.read(16)
                if len(rec) < 16:
                    break
                tag_bytes, _, offset, length = struct.unpack('>4sIII', rec)
                if tag_bytes == b'name':
                    name_offset = offset
                    name_length = length
                    break

            if name_offset is None:
                return result

            f.seek(name_offset)
            name_data = f.read(name_length)
            if len(name_data) < 6:
                return result

            # Name table header: USHORT format, USHORT count, USHORT stringOffset (6 bytes)
            fmt, count, string_offset = struct.unpack('>HHH', name_data[0:6])
            if string_offset > len(name_data):
                return result

            best_for_name_id: dict[int, tuple[str, int]] = {}
            # Priority: 1033 (US English) > 2052 (Chinese PRC) > any other

            for i in range(count):
                off = 6 + i * 12
                if off + 12 > len(name_data):
                    break
                platform_id, encoding_id, language_id, name_id, length, rec_offset = \
                    struct.unpack('>HHHHHH', name_data[off:off+12])

                str_off = string_offset + rec_offset
                if str_off + length > len(name_data):
                    continue

                raw = name_data[str_off:str_off+length]

                # Decode based on platform/encoding
                text = None
                if platform_id == 3 and encoding_id == 1:
                    # Windows, Unicode BMP (UTF-16BE)
                    try:
                        text = raw.decode('utf-16-be')
                    except Exception:
                        pass
                elif platform_id == 3 and encoding_id == 10:
                    # Windows, Unicode full (UTF-16BE)
                    try:
                        text = raw.decode('utf-16-be')
                    except Exception:
                        pass
                elif platform_id == 1 and encoding_id == 0:
                    # Mac Roman
                    try:
                        text = raw.decode('mac_roman')
                    except Exception:
                        pass
                elif platform_id == 0:
                    # Unicode (Apple)
                    try:
                        text = raw.decode('utf-16-be')
                    except Exception:
                        pass

                if text is None:
                    continue

                # Remove null bytes at end
                text = text.rstrip('\x00')

                if not text.strip():
                    continue

                # Priority: US English (1033) > Chinese PRC (2052) > anything
                priority = 0
                if language_id == 1033:
                    priority = 3
                elif language_id == 2052:
                    priority = 2
                else:
                    priority = 1

                prev = best_for_name_id.get(name_id)
                if prev is None or priority > prev[1]:
                    # For CJK names (2052), we want BOTH English and Chinese.
                    # So store each language separately, keyed by (name_id, language_id)
                    pass

                # Store: prefer US English for the main key, also add Chinese key
                if prev is None or priority > prev[1]:
                    best_for_name_id[name_id] = (text, priority)

                # Also store per-language entries for multi-language lookup
                # Use composite key: name_id * 10000 + language_id
                composite_key = name_id * 10000 + language_id
                if composite_key not in best_for_name_id or priority > best_for_name_id[composite_key][1]:
                    best_for_name_id[composite_key] = (text, priority)

            # Return: UK English nameID→text, plus composite keys for other languages
            for key, (text, _pri) in best_for_name_id.items():
                result[key] = text

    except Exception:
        _txt_log.warn("Failed to parse font name table (skipping)")
    return result


def _build_font_map() -> dict[str, str]:
    """Build a comprehensive font name → file path mapping.

    Combines:
      1. Windows Fonts registry (English names → filenames)
      2. TrueType name table parsing (Chinese + English names from each font file)

    Returns dict mapping lowercase normalized name → full file path.
    Keys include both English names ("dengxian light") and Chinese names ("等线 light").
    """
    font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
    mapping: dict[str, str] = {}

    # ── Source 1: Windows registry ──
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
        for i in range(winreg.QueryInfoKey(key)[1]):
            name, value, _ = winreg.EnumValue(key, i)
            # Strip " (TrueType)" / " (OpenType)" suffixes
            clean = name.replace(' (TrueType)', '').replace(' (OpenType)', '')
            fname = value.strip()
            # Resolve to full path
            if not os.path.isabs(fname):
                fpath = os.path.join(font_dir, fname)
            else:
                fpath = fname
            if os.path.exists(fpath):
                # Handle combined entries like "Arial & Arial Bold" → add each separately
                names_to_add = [clean]
                if '&' in clean:
                    names_to_add.extend([n.strip() for n in clean.split('&')])
                for name_to_add in names_to_add:
                    # setdefault: first (usually Regular) wins, variants don't overwrite
                    mapping.setdefault(name_to_add.lower(), fpath)
                    # Also add without style suffix
                    parts = name_to_add.rsplit(' ', 1)
                    if len(parts) == 2:
                        style_words = {'light', 'bold', 'italic', 'regular', 'semibold',
                                       'semilight', 'extralight', 'extrabold', 'black',
                                       'heavy', 'thin', 'medium', 'oblique', 'narrow'}
                        if parts[1].lower() in style_words:
                            mapping.setdefault(parts[0].lower(), fpath)
        winreg.CloseKey(key)
    except Exception:
        _txt_log.error("Failed to read font registry", exc_info=True)

    # ── Source 2: TrueType name table in each font file ──
    try:
        all_fonts = [f for f in os.listdir(font_dir)
                     if f.lower().endswith(('.ttf', '.ttc', '.otf'))]
        for fname in all_fonts:
            fpath = os.path.join(font_dir, fname)
            names = _read_name_table(fpath)
            # Collect all family + style names across languages
            # nameID 1 = Family, nameID 2 = Subfamily (style)
            # Composite key: nameID * 10000 + languageID
            family_candidates = set()
            style_candidates = set()

            # Get US English names (nameID 1 and 2 with lang 1033)
            en_family_key = 1 * 10000 + 1033
            en_style_key = 2 * 10000 + 1033
            en_family = names.get(en_family_key) or names.get(1)
            en_style = names.get(en_style_key) or names.get(2)

            if en_family:
                family_candidates.add(en_family)

            # Get Chinese names (lang 2052)
            cn_family_key = 1 * 10000 + 2052
            cn_style_key = 2 * 10000 + 2052
            cn_family = names.get(cn_family_key)
            cn_style = names.get(cn_style_key)

            if cn_family:
                family_candidates.add(cn_family)

            # Collect ALL language variants for thorough matching
            for key, text in names.items():
                if key > 10000:
                    name_id = key // 10000
                    if name_id == 1:
                        family_candidates.add(text)
                    elif name_id == 2:
                        style_candidates.add(text)

            # Register: family name alone, family + " " + style
            # setdefault: don't overwrite registry entries (Source 1)
            for family in family_candidates:
                if family.strip():
                    mapping.setdefault(family.lower(), fpath)
                    for style in style_candidates:
                        if style.strip():
                            full_name = f"{family} {style}"
                            mapping.setdefault(full_name.lower(), fpath)

    except Exception:
        _txt_log.error("Failed to parse font name tables from font files", exc_info=True)

    return mapping


# ── TextRenderer ────────────────────────────────────────────────────────────

class TextRenderer:
    """Renders text to OpenGL textures using PIL."""

    def __init__(self):
        self._font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}
        # ★ LRU-bounded texture cache.  Every lyric line / title / time string
        # accumulates here; previously unbounded, so long sessions leaked
        # thousands of textures.  Access recency ordering + popitem(last=False)
        # eviction — recently drawn entries can never be evicted mid-frame.
        self._texture_cache: OrderedDict[str, int] = OrderedDict()  # key → GL texture ID
        self._texture_sizes: dict[str, tuple[int, int]] = {}
        self._texture_cache_max: int = 256
        self._font_map: dict[str, str] = _build_font_map()
        # ★ Script-aware font fallback state (see resolve_family_for_text).
        # Probed on real machines: Microsoft YaHei has NO glyphs for Hangul,
        # Thai, Arabic, Hebrew or Devanagari — lyrics in those scripts
        # rendered as tofu boxes.  These caches make per-text resolution cheap.
        self._cov_font_cache: dict[str, ImageFont.FreeTypeFont] = {}
        self._glyph_cov: dict[tuple[str, int], bool] = {}
        self._family_choice_cache: dict[tuple[str, str], str] = {}

    def _resolve_font_path(self, family: str) -> str | None:
        """Resolve a font family name to a file path using the font map."""
        return self._font_map.get(family.lower())

    # ── Script-aware font fallback ────────────────────────────────────────

    _PROBE_SIZE = 32
    # Private-use codepoints that no normal font maps — their masks ARE the
    # .notdef tofu box; comparing against them detects missing glyphs.
    _NOTDEF_REFS = ("\ue000", "\uf8ff")
    _FALLBACK_FAMILIES = (
        "Microsoft YaHei", "Malgun Gothic", "Microsoft JhengHei",
        "Yu Gothic UI", "MS Gothic", "Leelawadee UI", "Nirmala UI",
        "Segoe UI", "Tahoma", "Arial",
    )

    def _probe_font(self, family: str):
        """Font for glyph-coverage probing (fixed size, cached per family)."""
        f = self._cov_font_cache.get(family)
        if f is None:
            path = self._resolve_font_path(family)
            if not path:
                return None
            try:
                f = ImageFont.truetype(path, self._PROBE_SIZE)
            except Exception:
                return None
            self._cov_font_cache[family] = f
        return f

    def _covers(self, family: str, ch: str) -> bool:
        """True when the family has a real glyph for ch (not .notdef tofu)."""
        key = (family, ord(ch))
        hit = self._glyph_cov.get(key)
        if hit is not None:
            return hit
        covered = True
        font = self._probe_font(family)
        if font is None:
            covered = False
        else:
            try:
                m = font.getmask(ch)
                for r in self._NOTDEF_REFS:
                    rm = font.getmask(r)
                    if m.size == rm.size and bytes(m) == bytes(rm):
                        covered = False
                        break
            except Exception:
                covered = False
        self._glyph_cov[key] = covered
        return covered

    def resolve_family_for_text(self, text: str, family: str) -> str:
        """Pick the family whose glyphs best cover this text.

        Checks the unique non-ASCII characters of the text against the
        configured family; if any glyph is missing, walks the fallback chain
        and returns the family with the fewest uncovered characters (the
        configured family wins ties).  Results are cached per (text, family)
        — resolution cost is paid once per distinct lyric line.
        """
        if not text:
            return family
        cache_key = (text, family)
        hit = self._family_choice_cache.get(cache_key)
        if hit is not None:
            return hit
        if len(self._family_choice_cache) > 512:
            self._family_choice_cache.clear()

        best = family
        chars = {c for c in text if not c.isspace() and ord(c) >= 0x80}
        if chars and not all(self._covers(family, c) for c in chars):
            best_miss: int | None = None
            for cand in self._FALLBACK_FAMILIES:
                miss = sum(0 if self._covers(cand, c) else 1 for c in chars)
                if best_miss is None or miss < best_miss:
                    best, best_miss = cand, miss
                if miss == 0:
                    break
            if best != family:
                _txt_log.rate_limited(
                    "font_fallback", 30.0, "INFO",
                    f"font fallback: {family!r} missing {best_miss} glyph(s) "
                    f"→ {best!r} for {text[:24]!r}")
        self._family_choice_cache[cache_key] = best
        return best

    def get_font(self, family: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        """Get a PIL font (cached).  When bold=True, loads the bold variant.

        Uses a comprehensive font name → file path mapping built from the
        Windows registry AND TrueType name table parsing, so both English
        names ("DengXian Light") and Chinese names ("等线 Light") resolve
        correctly.
        """
        key = (family, size, bold)
        if key in self._font_cache:
            return self._font_cache[key]

        font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
        family_lower = family.lower()

        def _try_file(path: str, index: int = 0) -> ImageFont.FreeTypeFont | None:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size, index=index)
                except Exception:
                    pass
            return None

        font = None

        if bold:
            # ★ Try the family's OWN bold face via the font map first —
            # e.g. "Malgun Gothic" → "Malgun Gothic Bold" → malgunbd.ttf.
            # The hardcoded list below ignores the requested family entirely,
            # which made bold Korean/Thai text render with YaHei (missing
            # glyphs) even after the family was resolved for coverage.
            for bold_name in (f"{family} Bold", f"{family} bold", f"{family}-Bold"):
                fpath = self._resolve_font_path(bold_name)
                if fpath:
                    font = _try_file(fpath, 0)
                    if font is None and fpath.lower().endswith('.ttc'):
                        font = _try_file(fpath, 1)
                    if font is not None:
                        break

            # Try bold face within .ttc collections (only if the family's own
            # bold face above didn't load — this loop clobbers `font` otherwise)
            if font is None:
                ttc_candidates = [
                    ("msyhbd.ttc", 0),     # Microsoft YaHei Bold
                    ("msyh.ttc", 1),       # legacy fallback face (UI Regular on Win11!)
                    ("simhei.ttf", 0),     # SimHei (黑体, inherently bold)
                    ("msjhbd.ttc", 0),     # Microsoft JhengHei Bold
                    ("msjh.ttc", 1),
                    ("segoeui.ttc", 1),    # Segoe UI Bold
                    ("seguibl.ttf", 0),    # Segoe UI Bold (separate)
                    ("seguisb.ttf", 0),    # Segoe UI Semibold
                    ("arialbd.ttf", 0),    # Arial Bold
                    ("arialb.ttf", 0),     # Arial Bold (alternate name)
                ]
                for fname, idx in ttc_candidates:
                    path = os.path.join(font_dir, fname)
                    font = _try_file(path, idx)
                    if font is not None:
                        break

            # Try family name with bold suffixes
            if font is None:
                for suffix in ("bd", "b", "bold", " Bold", "Bold"):
                    for ext in (".ttf", ".otf"):
                        path = os.path.join(font_dir, f"{family}{suffix}{ext}")
                        font = _try_file(path)
                        if font is not None:
                            break
                    if font is not None:
                        break

        # ── Main lookup: use font map ──
        if font is None:
            # Step 1: Exact file name match
            for fname in [f"{family}.ttf", f"{family}.otf",
                          f"{family_lower}.ttf", f"{family_lower}.otf"]:
                path = os.path.join(font_dir, fname)
                font = _try_file(path)
                if font is not None:
                    break

        if font is None:
            # Step 2: Font map lookup (registry + name table)
            fpath = self._resolve_font_path(family)
            if fpath:
                # Determine index for .ttc files
                idx = 0
                if fpath.lower().endswith('.ttc'):
                    # For TTC, try index 0 first; bold is handled above
                    idx = 0
                font = _try_file(fpath, idx)
                # If TTC index 0 didn't work, try index 1
                if font is None and fpath.lower().endswith('.ttc'):
                    font = _try_file(fpath, 1)

        if font is None:
            # Step 3: Case-insensitive font map search (slower, only when needed)
            for map_name, fpath in self._font_map.items():
                if map_name == family_lower or family_lower in map_name or map_name in family_lower:
                    idx = 0
                    font = _try_file(fpath, idx)
                    if font is None and fpath.lower().endswith('.ttc'):
                        font = _try_file(fpath, 1)
                    if font is not None:
                        break

        if font is None:
            # Step 4: Scan entire Fonts directory (last resort)
            try:
                all_fonts = [f for f in os.listdir(font_dir)
                           if f.lower().endswith(('.ttf', '.ttc', '.otf'))]
                _cjk_keywords = ['msyh', 'simsun', 'simhei', 'simkai', 'msjh',
                                'mingliu', 'msgothic', 'yugoth', 'meiryo',
                                'malgun', 'gulim', 'noto', 'cjk', 'han', 'songti',
                                'heiti', 'kaiti', 'fangsong', 'deng']
                def _cjk_priority(fname):
                    low = fname.lower()
                    for kw in _cjk_keywords:
                        if kw in low:
                            return (0, fname)
                    return (1, fname)
                all_fonts.sort(key=_cjk_priority)
                for fname in all_fonts:
                    path = os.path.join(font_dir, fname)
                    font = _try_file(path, 0)
                    if font is not None:
                        break
            except Exception:
                _txt_log.error("Failed during CJK font search, falling back to default font", exc_info=True)

        if font is None:
            font = ImageFont.load_default()

        self._font_cache[key] = font
        return font

    def render_to_texture(
        self,
        text: str,
        font_family: str = "Microsoft YaHei",
        font_size: int = 14,
        font_bold: bool = False,
        color: tuple[int, int, int] = (255, 255, 255),
        max_width: int = 0,
        alignment: str = "left",
        outline_width: int = 0,
        outline_color: tuple[int, int, int] = (0, 0, 0),
    ) -> int:
        """Render text to an OpenGL texture. Returns texture ID.

        Text is left-aligned with anti-aliasing padding, then FLIP_TOP_BOTTOM'd
        to convert PIL top-down → OpenGL bottom-up texture convention.
        Shader Y-flip (no X-flip) handles the screen coordinate mapping;
        text stays left-aligned in the source image — no position compensation needed.

        CJK text is auto-detected and rendered with a CJK-capable font
        if the requested font family doesn't support CJK glyphs.

        When outline_width > 0, the outline is pre-composited in PIL (native
        stroke or manual offset rendering), eliminating 8 GPU draw calls per
        outlined text element.  A single texture + single draw call is returned.
        """
        _outline_suffix = f"|{outline_width}|{outline_color}" if outline_width > 0 else ""
        # ★ Resolve the family for THIS text first — the cache key must be
        # built from the family that will actually render (a fallback family
        # produces different metrics/size than the configured one).
        font_family = self.resolve_family_for_text(text, font_family)
        cache_key = f"{text}|{font_family}|{font_size}|{font_bold}|{color}|{max_width}|{alignment}{_outline_suffix}"

        # Return cached texture if text hasn't changed (LRU touch)
        if cache_key in self._texture_cache:
            self._texture_cache.move_to_end(cache_key)
            return self._texture_cache[cache_key]

        font = self.get_font(font_family, font_size, font_bold)

        # Measure text
        if max_width > 0:
            lines = self._wrap_text(text, font, max_width)
        else:
            lines = text.split("\n")

        # Calculate image size
        line_heights = []
        total_width = 0
        total_height = 0
        for line in lines:
            bbox = font.getbbox(line)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1] + 4
            line_heights.append((lw, lh))
            total_width = max(total_width, lw)
            total_height += lh

        if total_width <= 0 or total_height <= 0:
            total_width = max(total_width, 10)
            total_height = max(total_height, 10)

        # Pad for anti-aliasing
        pad = 4
        img_w = total_width + pad * 2
        img_h = total_height + pad * 2

        # Render text to PIL image
        img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        y = pad
        for i, line in enumerate(lines):
            lw, lh = line_heights[i]
            if alignment == "center":
                x = pad + (total_width - lw) // 2
            elif alignment == "right":
                x = pad + total_width - lw
            else:
                x = pad

            # ★ Pre-composite outline in PIL, converting 9 GPU draw calls → 1.
            # Uses Pillow 10+ native stroke_width, falling back to manual offset
            # rendering for older Pillow versions.
            if outline_width > 0:
                try:
                    # Pillow >= 10.0 native stroke
                    draw.text(
                        (x, y), line, fill=color + (255,), font=font,
                        stroke_width=outline_width,
                        stroke_fill=outline_color + (255,),
                    )
                except TypeError:
                    # Fallback: manual offset rendering for older Pillow
                    for dx in range(-outline_width, outline_width + 1):
                        for dy in range(-outline_width, outline_width + 1):
                            if dx == 0 and dy == 0:
                                continue
                            draw.text(
                                (x + dx, y + dy), line,
                                fill=outline_color + (255,), font=font,
                            )
                    draw.text((x, y), line, fill=color + (255,), font=font)
            else:
                draw.text((x, y), line, fill=color + (255,), font=font)
            y += lh

        img = img.transpose(Image.FLIP_TOP_BOTTOM)

        # Upload to OpenGL
        tex_id = self._upload_texture(img)
        self._texture_cache[cache_key] = tex_id
        self._texture_sizes[cache_key] = (img_w, img_h)

        # ★ LRU eviction — cap live text textures so multi-hour sessions with
        # ever-changing lyric lines don't grow VRAM without bound.  Evicted
        # entries are the least recently used; anything drawn this frame was
        # just touched and stays.
        while len(self._texture_cache) > self._texture_cache_max:
            old_key, old_tex = self._texture_cache.popitem(last=False)
            self._texture_sizes.pop(old_key, None)
            try:
                glDeleteTextures(GLuint(old_tex))
            except Exception:
                pass

        return tex_id

    def _wrap_text(self, text: str, font, max_width: int) -> list[str]:
        """Wrap text to fit within max_width pixels."""
        def _px(f, s) -> int:
            b = f.getbbox(s)
            return b[2] - b[0]

        lines = []
        for paragraph in text.split("\n"):
            current = ""
            for word in paragraph.split():
                test = current + (" " if current else "") + word
                if _px(font, test) > max_width and current:
                    lines.append(current)
                    current = ""
                    test = word
                # ★ CJK fallback: a token without spaces (Chinese/Japanese/
                # Korean lyrics and titles) can be wider than the whole line —
                # hard-break it per character instead of overflowing forever.
                if _px(font, test) <= max_width:
                    current = test
                    continue
                for ch in test:
                    piece = current + (" " if current else "") + ch
                    if _px(font, piece) > max_width and current:
                        lines.append(current)
                        current = ch
                    else:
                        current = piece
            if current:
                lines.append(current)
        return lines if lines else [text]

    def get_texture_size(self, cache_key: str) -> tuple[int, int]:
        """Get the size of a cached texture."""
        return self._texture_sizes.get(cache_key, (10, 10))

    def measure_text(
        self,
        text: str,
        font_family: str = "Microsoft YaHei",
        font_size: int = 14,
        font_bold: bool = False,
        max_width: int = 0,
    ) -> tuple[int, int]:
        """Measure text dimensions using PIL font metrics (no texture upload).

        Returns (width, height) in pixels. Much faster than render_to_texture()
        when only size is needed — avoids image allocation, draw, and GL upload.
        """
        if not text:
            return (10, 10)
        font_family = self.resolve_family_for_text(text, font_family)
        font = self.get_font(font_family, font_size, font_bold)
        if max_width > 0:
            lines = self._wrap_text(text, font, max_width)
        else:
            lines = text.split("\n")
        total_w = 0
        total_h = 0
        for line in lines:
            bbox = font.getbbox(line)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1] + 4
            total_w = max(total_w, lw)
            total_h += lh
        pad = 4
        return (max(10, total_w + pad * 2), max(10, total_h + pad * 2))

    def _upload_texture(self, img: Image.Image) -> int:
        """Upload PIL image to OpenGL texture. Returns texture ID."""
        arr = (GLuint * 1)()
        glGenTextures(1, arr)
        tex_id = arr[0]
        glBindTexture(GL_TEXTURE_2D, tex_id)

        # RGBA data as bytes
        data = img.tobytes("raw", "RGBA", 0, -1)
        glTexImage2D(
            GL_TEXTURE_2D, 0, GL_RGBA,
            img.width, img.height, 0,
            GL_RGBA, GL_UNSIGNED_BYTE, data
        )

        # Mipmaps for smooth scaling
        glGenerateMipmap(GL_TEXTURE_2D)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

        return tex_id

    def clear_cache(self) -> None:
        """Delete all cached textures."""
        for tex_id in self._texture_cache.values():
            try:
                arr = (GLuint * 1)(tex_id)
                glDeleteTextures(1, arr)
            except Exception:
                pass
        self._texture_cache.clear()
        self._texture_sizes.clear()


# Global instance
_text_renderer: Optional[TextRenderer] = None


def get_text_renderer() -> TextRenderer:
    global _text_renderer
    if _text_renderer is None:
        _text_renderer = TextRenderer()
    return _text_renderer
