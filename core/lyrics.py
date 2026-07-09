"""LRC parser and bilingual lyrics merger.

References:
  - syncedlyrics (moehmeni/syncedlyrics): LRC parsing + lang= translation param
  - YesPlayMusic / R3play: bilingual lyrics display with separate-line rendering
  - LyricsX (HankunYu/LyricsX): desktop lyrics overlay bilingual mode
  - Mp3tag community: line-by-line LRC zip/interleave algorithm

Architecture:
  1. LRCParser — parse LRC text into metadata + timed lines
  2. BilingualMerger — merge original + translation LRC with smart alignment
  3. format_bilingual_lines — format merged lines for display engine

The QQ Music API returns base64-encoded LRC in two fields: lyric (original)
and trans (translation).  This module merges them and produces structured
output that the Painter can render with visual differentiation.
"""
import re
import base64
from typing import Optional


# ================================================================
# LRC Parser — parses .lrc format into structured data
# ================================================================

# Regex patterns for LRC
_RE_TAG = re.compile(r'\[(\w+):(.*?)\]')                   # [ti:...], [ar:...], [offset:...]
_RE_TIMESTAMP = re.compile(r'\[(\d{1,3}):(\d{2})(?:\.(\d{2,3}))?\]')  # [mm:ss.xx] or [mm:ss.xxx]
_RE_WORD_TIME = re.compile(r'<(\d{2}):(\d{2})\.(\d{2,3})>')  # <mm:ss.xx> word-level (enhanced LRC)

# Copyright / boilerplate notices injected by QQ Music that should be filtered
# from display. These are not song lyrics — they're legal disclaimers.
_COPYRIGHT_PATTERNS = [
    "未经许可,不得翻唱或使用",
    "未经许可，不得翻唱或使用",
    "未经许可,不得翻唱",
    "未经许可，不得翻唱",
    "未经许可不得翻唱",
    "不得翻唱或使用",
    "本歌词由AI生成",
    "本歌词由 AI 生成",
    "歌词由AI生成",
    "歌词由 AI 生成",
    "纯音乐，请欣赏",
    "纯音乐,请欣赏",
    "请勿商业使用",
    "禁止商业使用",
    "仅供学习交流",
    "★ 此歌词由AI生成",
]


def _is_copyright_notice(text: str) -> bool:
    """Return True if the text is a copyright/boilerplate notice, not lyrics."""
    if not text:
        return False
    t = text.strip()
    for pattern in _COPYRIGHT_PATTERNS:
        if pattern in t:
            return True
    return False


class LRCParser:
    """Parse LRC text into metadata dict and list of timed lyric lines.

    Reference: syncedlyrics LRC parsing in lrc.py
    """

    @staticmethod
    def parse(lrc_text: str) -> tuple[dict, list[tuple[int, str]]]:
        """Parse LRC format text into (metadata, [(timestamp_ms, text), ...]).

        Args:
            lrc_text: Raw LRC content (may be base64-encoded)

        Returns:
            (metadata, timed_lines) where:
              - metadata: dict with keys like 'ti', 'ar', 'al', 'by', 'offset', 're', 've'
              - timed_lines: list of (time_ms, text) sorted by time
        """
        # Decode base64 if needed
        text = lrc_text
        if lrc_text and not lrc_text.startswith('['):
            try:
                text = base64.b64decode(lrc_text).decode('utf-8', errors='replace')
            except Exception:
                text = lrc_text

        metadata: dict = {}
        timed_lines: list[tuple[int, str]] = []

        if not text:
            return metadata, timed_lines

        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue

            # Parse metadata tags: [ti:Song Title], [ar:Artist], etc.
            tag_matches = _RE_TAG.findall(line)
            for tag_key, tag_value in tag_matches:
                # Skip timestamp-like tags (these are lyric lines)
                if tag_key.isdigit():
                    continue
                metadata[tag_key] = tag_value.strip()

            # Parse timestamped lyrics
            timestamps = _RE_TIMESTAMP.findall(line)
            if not timestamps:
                continue

            # Extract the text part (everything after the last timestamp bracket)
            text_part = _RE_TIMESTAMP.sub('', line).strip()
            # Also strip word-level timing tags <mm:ss.xx>
            text_part = _RE_WORD_TIME.sub('', text_part).strip()

            # ★ Skip copyright/boilerplate notices injected by QQ Music.
            # These are legal disclaimers, not song lyrics.
            if _is_copyright_notice(text_part):
                continue

            for m in timestamps:
                mm = int(m[0])
                ss = int(m[1])
                ms_str = m[2] if len(m) > 2 and m[2] else '0'

                if len(ms_str) == 2:
                    ms = int(ms_str) * 10
                elif len(ms_str) == 3:
                    ms = int(ms_str)
                else:
                    ms = 0

                time_ms = mm * 60000 + ss * 1000 + ms
                timed_lines.append((time_ms, text_part))

        # Sort by timestamp
        timed_lines.sort(key=lambda x: x[0])

        return metadata, timed_lines

    @staticmethod
    def to_text(timed_lines: list[tuple[int, str]], metadata: dict | None = None) -> str:
        """Convert timed lines back to LRC text format.

        Args:
            timed_lines: list of (time_ms, text)
            metadata: optional dict of metadata tags

        Returns:
            LRC-formatted string
        """
        lines = []
        if metadata:
            for key, value in metadata.items():
                if key not in ('offset',):
                    lines.append(f'[{key}:{value}]')
            if 'offset' in metadata:
                lines.append(f'[offset:{metadata["offset"]}]')

        for time_ms, text in timed_lines:
            mm = time_ms // 60000
            ss = (time_ms // 1000) % 60
            ms = time_ms % 1000
            lines.append(f'[{mm:02d}:{ss:02d}.{ms:03d}]{text}')

        return '\n'.join(lines)


# ================================================================
# Bilingual Merger — merges original + translation LRC
# ================================================================

class BilingualMerger:
    """Merge original and translated LRC timed lines.

    Strategies (inspired by syncedlyrics, YesPlayMusic):
      - TIMESTAMP: Match by closest timestamp within tolerance (default, ~200ms)
      - LINEAR: Line-by-line zip (assumes matching line counts)
      - FUZZY: Timestamp matching with wider tolerance + text similarity fallback

    The merged output is a list of (time_ms, main_text, trans_text) tuples.
    """

    TIMESTAMP = "timestamp"   # Closest timestamp within tolerance
    LINEAR = "linear"         # Line-by-line zip
    FUZZY = "fuzzy"           # Wide tolerance + text similarity

    def __init__(self, strategy: str = TIMESTAMP, tolerance_ms: int = 250):
        self.strategy = strategy
        self.tolerance_ms = tolerance_ms

    def merge(
        self,
        main: list[tuple[int, str]],
        trans: list[tuple[int, str]],
    ) -> list[tuple[int, str, str]]:
        """Merge original and translation timed lines.

        Args:
            main: Original lyric lines as [(time_ms, text), ...]
            trans: Translation lyric lines as [(time_ms, text), ...]

        Returns:
            List of (time_ms, main_text, trans_text) triples.
            Lines from main without matching trans have trans_text="".
            Lines from trans without matching main have main_text="".
        """
        if not trans:
            return [(ts, txt, "") for ts, txt in main]
        if not main:
            return [(ts, "", txt) for ts, txt in trans]

        if self.strategy == self.LINEAR:
            return self._merge_linear(main, trans)
        elif self.strategy == self.FUZZY:
            return self._merge_fuzzy(main, trans)
        else:
            return self._merge_timestamp(main, trans)

    def _merge_timestamp(
        self,
        main: list[tuple[int, str]],
        trans: list[tuple[int, str]],
    ) -> list[tuple[int, str, str]]:
        """Timestamp-proximity merge — match each main line to nearest trans.

        This is the QQ Music default strategy.  Round timestamps to reduce
        micro-differences, then match within tolerance.

        Reference: syncedlyrics NetEase provider merge logic
        """
        # Round timestamps to reduce micro-differences
        round_to = 100  # ms granularity
        main_rounded = [(round(ts / round_to) * round_to, txt) for ts, txt in main]
        trans_rounded = [(round(ts / round_to) * round_to, txt) for ts, txt in trans]

        result: list[tuple[int, str, str]] = []
        used_trans: set[int] = set()

        for orig_ts, orig_txt in main:
            ts_rounded = round(orig_ts / round_to) * round_to

            # Find nearest unmatched translation within tolerance
            best_idx = -1
            best_dist = self.tolerance_ms + 1
            for i, (t_ts, _) in enumerate(trans_rounded):
                if i in used_trans:
                    continue
                dist = abs(t_ts - ts_rounded)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i

            if best_idx >= 0:
                used_trans.add(best_idx)
                result.append((orig_ts, orig_txt, trans[best_idx][1]))
            else:
                # Exceeded tolerance — no match
                result.append((orig_ts, orig_txt, ""))

        # Append unmatched trans lines (orphaned translations)
        for i, (t_ts, t_txt) in enumerate(trans):
            if i not in used_trans:
                result.append((t_ts, "", t_txt))

        result.sort(key=lambda x: x[0])
        return result

    def _merge_linear(
        self,
        main: list[tuple[int, str]],
        trans: list[tuple[int, str]],
    ) -> list[tuple[int, str, str]]:
        """Line-by-line merge — zip lines positionally.

        Reference: Mp3tag community zip script
        """
        result = []
        max_len = max(len(main), len(trans))
        for i in range(max_len):
            ts = 0
            mt = ""
            tt = ""
            if i < len(main):
                ts = main[i][0]
                mt = main[i][1]
            if i < len(trans):
                if ts == 0:
                    ts = trans[i][0]
                tt = trans[i][1]
            result.append((ts, mt, tt))
        return result

    def _merge_fuzzy(
        self,
        main: list[tuple[int, str]],
        trans: list[tuple[int, str]],
    ) -> list[tuple[int, str, str]]:
        """Fuzzy merge — timestamp proximity + text similarity fallback.

        For lines that don't match by timestamp, try matching by text
        content similarity (useful for fan-translated lyrics with
        different timestamp conventions).
        """
        # First pass: timestamp matching with wider tolerance
        from difflib import SequenceMatcher

        wide_tolerance = max(self.tolerance_ms * 2, 500)
        result: list[tuple[int, str, str]] = []
        used_trans: set[int] = set()

        for orig_ts, orig_txt in main:
            # Try timestamp match
            best_idx = -1
            best_dist = wide_tolerance + 1
            for i, (t_ts, _) in enumerate(trans):
                if i in used_trans:
                    continue
                dist = abs(t_ts - orig_ts)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i

            if best_idx >= 0:
                used_trans.add(best_idx)
                result.append((orig_ts, orig_txt, trans[best_idx][1]))
            else:
                # Try text similarity with remaining trans lines
                best_sim = 0.0
                best_sim_idx = -1
                for i, (_, t_txt) in enumerate(trans):
                    if i in used_trans:
                        continue
                    sim = SequenceMatcher(None, orig_txt.lower(), t_txt.lower()).ratio()
                    if sim > best_sim and sim > 0.3:
                        best_sim = sim
                        best_sim_idx = i

                if best_sim_idx >= 0:
                    used_trans.add(best_sim_idx)
                    result.append((orig_ts, orig_txt, trans[best_sim_idx][1]))
                else:
                    result.append((orig_ts, orig_txt, ""))

        # Append unmatched trans
        for i, (t_ts, t_txt) in enumerate(trans):
            if i not in used_trans:
                result.append((t_ts, "", t_txt))

        result.sort(key=lambda x: x[0])
        return result


# ================================================================
# Legacy merge — kept for backward compatibility with reader.py
# ================================================================

def merge_timed_legacy(
    main: list[tuple[int, str]],
    trans: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Legacy merge (reader.py original) — returns flat [(ts, combined_text)].

    Uses timestamp proximity matching (100ms rounding, 200ms tolerance).
    Combined text format: "main_text\\n  trans_text"
    """
    if not trans:
        return list(main)
    if not main:
        return list(trans)

    trans_map: dict[int, str] = {}
    for ts, txt in trans:
        rounded = round(ts / 100) * 100
        if rounded not in trans_map:
            trans_map[rounded] = txt.strip()

    result: list[tuple[int, str]] = []
    used_trans: set[int] = set()
    for ts, txt in main:
        nearby = [k for k in trans_map if abs(k - ts) < 200]
        trans_txt = ""
        if nearby:
            best_k = min(nearby, key=lambda k: abs(k - ts))
            trans_txt = trans_map[best_k]
            used_trans.add(best_k)
        combined = txt.strip()
        if trans_txt:
            combined += f"\n  {trans_txt}"
        result.append((ts, combined))

    # Append unmatched trans lines
    for ts, txt in trans:
        rounded = round(ts / 100) * 100
        if rounded not in used_trans:
            result.append((ts, f"  {txt.strip()}"))
            used_trans.add(rounded)

    result.sort(key=lambda x: x[0])
    return result


# ================================================================
# Bilingual format helper — split merged text into components
# ================================================================

def split_bilingual_line(text: str) -> tuple[str, str]:
    """Split a legacy merged line back into (main_text, trans_text).

    Legacy format: "main_text\\n  trans_text"
    """
    if '\n  ' in text:
        parts = text.split('\n  ', 1)
        return parts[0], parts[1]
    elif '\n' in text:
        parts = text.split('\n', 1)
        return parts[0], parts[1]
    return text, ""


def has_translation(text: str) -> bool:
    """Check if a merged lyrics line contains a translation."""
    return '\n' in text


# ================================================================
# Korean detection — used by reader.py
# ================================================================

_KOREAN_RE = re.compile(r'[가-힯ᄀ-ᇿ㄰-㆏]')


def has_korean(text: str) -> bool:
    """Check if text contains Korean characters (Hangul)."""
    if not text:
        return False
    count = len(_KOREAN_RE.findall(text))
    return count > max(3, len(text) * 0.05)


# ================================================================
# Convenience: parse + merge in one call
# ================================================================

def parse_and_merge(
    lyric_raw: str,
    trans_raw: str,
    strategy: str = BilingualMerger.TIMESTAMP,
    tolerance_ms: int = 250,
) -> list[tuple[int, str, str]]:
    """Parse two LRC strings and merge them.

    Args:
        lyric_raw: Original lyrics (raw or base64 encoded LRC)
        trans_raw: Translation lyrics (raw or base64 encoded LRC)
        strategy: Merge strategy (TIMESTAMP, LINEAR, FUZZY)
        tolerance_ms: Timestamp matching tolerance in ms

    Returns:
        List of (time_ms, main_text, trans_text) triples
    """
    _, main_lines = LRCParser.parse(lyric_raw)
    _, trans_lines = LRCParser.parse(trans_raw)

    merger = BilingualMerger(strategy=strategy, tolerance_ms=tolerance_ms)
    return merger.merge(main_lines, trans_lines)


# ================================================================
# Cross-platform calibration — align NE timestamps to QQ timeline
# ================================================================
# When translation lyrics come from a different platform (NetEase →
# QQ Music), their timestamps have a consistent offset caused by
# different encoder settings, silence padding, or album version
# differences.  Applying a global offset BEFORE merging lets us use
# tight tolerance matching, which produces more accurate line pairs
# and guarantees synchronous appearance/disappearance (each pair
# shares the QQ timestamp).
#
# Algorithm:
#   1. For each QQ line, find the nearest NetEase line by timestamp
#   2. Compute offset = NE_ts − QQ_ts for each candidate pair
#   3. Use the MEDIAN offset (robust against structural differences
#      where NE splits a line into two or merges two into one)
#   4. Compute confidence = fraction of offsets within ±500 ms of
#      the median (low confidence → fall back to uncalibrated merge)
#   5. Subtract offset from all NE timestamps → aligned to QQ timeline


def calibrate_cross_platform(
    qq_timed: list[tuple[int, str]],
    ne_timed: list[tuple[int, str]],
) -> tuple[list[tuple[int, str]], int, float]:
    """Compute and apply global time offset so NE timestamps align with QQ.

    Args:
        qq_timed: QQ Music timed lines [(time_ms, text), ...]
        ne_timed: NetEase timed lines [(time_ms, text), ...]

    Returns:
        (ne_calibrated, offset_ms, confidence) where:
          - ne_calibrated: NE lines with offset applied [(adj_ms, text), ...]
          - offset_ms: the median offset that was subtracted (positive = NE was ahead)
          - confidence: 0.0–1.0 fraction of offset samples within ±500 ms of median
    """
    if len(qq_timed) < 3 or len(ne_timed) < 3:
        return list(ne_timed), 0, 0.0

    # ---- Pass 1: collect per-line offsets ----
    # For each QQ line, find the single nearest NE line by absolute
    # timestamp difference.  This gives us len(qq_timed) offset samples.
    ne_ts_list = [ts for ts, _ in ne_timed]
    offsets: list[int] = []
    for qq_ts, _ in qq_timed:
        # Find index of nearest NE timestamp
        best_idx = 0
        best_dist = abs(ne_ts_list[0] - qq_ts)
        for i in range(1, len(ne_ts_list)):
            dist = abs(ne_ts_list[i] - qq_ts)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        offsets.append(ne_ts_list[best_idx] - qq_ts)

    # ---- Pass 2: median offset (robust estimator) ----
    offsets.sort()
    median_offset = offsets[len(offsets) // 2]

    # ---- Pass 3: confidence (inlier fraction) ----
    inliers = sum(1 for o in offsets if abs(o - median_offset) <= 500)
    confidence = inliers / len(offsets)

    # ---- Apply offset ----
    ne_calibrated = [(ts - median_offset, txt) for ts, txt in ne_timed]

    return ne_calibrated, median_offset, confidence


def calibrate_and_merge(
    lyric_raw: str,
    trans_raw: str,
    *,
    strategy: str = BilingualMerger.TIMESTAMP,
    tolerance_ms: int = 250,
    min_confidence: float = 0.4,
    fallback_tolerance_ms: int = 800,
) -> tuple[list[tuple[int, str, str]], dict]:
    """Parse, calibrate, and merge original + translation LRC.

    Full pipeline:
      1. Parse both LRC sources
      2. Compute cross-platform offset (calibrate_cross_platform)
      3. If confidence ≥ min_confidence → apply offset + tight merge
      4. If confidence < min_confidence → fall back to wide-tolerance
         fuzzy merge (same-platform lyrics or very different structures)

    Args:
        lyric_raw: Original lyrics (raw or base64 LRC)
        trans_raw: Translation lyrics (raw or base64 LRC)
        strategy: Merge strategy for tight pass
        tolerance_ms: Tolerance for tight pass (after calibration)
        min_confidence: Minimum confidence to trust calibration
        fallback_tolerance_ms: Tolerance for uncalibrated fallback

    Returns:
        (merged, info_dict) where:
          - merged: list of (time_ms, main_text, trans_text) triples
          - info_dict: {'offset_ms', 'confidence', 'calibrated': bool, ...}
    """
    _, main_lines = LRCParser.parse(lyric_raw)
    _, trans_lines = LRCParser.parse(trans_raw)

    info = {
        "offset_ms": 0,
        "confidence": 0.0,
        "calibrated": False,
        "strategy": strategy,
        "tolerance_ms": tolerance_ms,
        "n_main": len(main_lines),
        "n_trans": len(trans_lines),
    }

    if not trans_lines:
        merged = [(ts, txt, "") for ts, txt in main_lines]
        return merged, info

    if not main_lines:
        merged = [(ts, "", txt) for ts, txt in trans_lines]
        return merged, info

    # ---- Calibration ----
    ne_calibrated, offset_ms, confidence = calibrate_cross_platform(
        main_lines, trans_lines
    )
    info["offset_ms"] = offset_ms
    info["confidence"] = confidence

    if confidence >= min_confidence:
        # Tight merge after calibration
        info["calibrated"] = True
        info["tolerance_ms"] = tolerance_ms
        merger = BilingualMerger(strategy=strategy, tolerance_ms=tolerance_ms)
        merged = merger.merge(main_lines, ne_calibrated)
    else:
        # Low confidence — fall back to wide-tolerance fuzzy merge
        info["calibrated"] = False
        info["tolerance_ms"] = fallback_tolerance_ms
        merger = BilingualMerger(strategy="fuzzy", tolerance_ms=fallback_tolerance_ms)
        merged = merger.merge(main_lines, trans_lines)

    return merged, info
