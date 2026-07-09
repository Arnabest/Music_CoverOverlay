"""QQ Music data reader — full original logic with all strategies.

Cover matching (multi-strategy):
  1. SMTC thumbnail (verified against current song)
  2. Perceptual hash matching → local high-res
  3. SMTC album title → filename matching
  4. Web API album art → local cache
  5. Local QQMusicPicture directory

Lyrics: QQ Music Web API + LRC parsing + bilingual merge
"""
import re, os, time, threading, fnmatch
from typing import Optional
from PIL import Image
from core.lyrics import parse_and_merge, merge_timed_legacy, LRCParser, has_korean as lyrics_has_korean
from core.logs import get_logger

_reader_logger = get_logger("reader")


def _reader_log(msg: str) -> None:
    """Backward-compat wrapper."""
    _reader_logger.info(msg)

from core.window_detect import (
    _is_process_running, _is_qqmusic_running, _is_netease_running,
    _is_player_running, find_qqmusic_window, find_netease_window,
    find_player_window, parse_window_title, _schedule_window_refresh,
    _do_window_refresh, _do_full_window_enum, _enum_windows_callback,
    _is_valid_window,
)

from core.web_api import (
    _search_qqmusic, _get_albummid_from_song, _fetch_album_art,
    _fetch_lyrics, _search_netease, _fetch_lyrics_netease,
    parse_lrc_timestamps,
)


# ========== Main Reader ==========

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))


class QQMusicReader:
    def __init__(self):
        self._lock = threading.RLock()
        self._last_song = ""
        self._last_artist = ""
        self._last_albummid = None
        self._last_songmid = None
        self._last_duration_secs = 0
        self._cover_image = None
        self._lyrics_timed = []
        self._lyrics_merged = []  # New: list of (time_ms, main_text, trans_text) triples
        self._lyrics_plain = ""
        self._lyrics_trans = ""
        self._loading = False
        self._loading_timestamp = 0
        self._playing_cache = False
        self._playing_cache_time = 0.0
        self._cache_dir = os.path.join(os.path.dirname(CONFIG_DIR), ".cache")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._cover_candidates = []
        self._smtc_gathered = False
        self._smtc_verified = False
        self._first_candidate_time = 0.0
        self._override_picture_dir = ""
        self._playing_cache = False
        self._playing_cache_time = 0.0
        self._auto_cover_index = -1
        # Stale-result prevention: generation counter incremented on each song change.
        # Background threads check this before applying results — discards late
        # arrivals from previous songs.
        self._search_generation = 0
        # ★ Track whether _cover_image is stale (from a previous song). When True,
        # _gather_cover_candidates() skips _cover_image to prevent the old cover
        # from appearing during the fade-in phase of a song-change transition.
        self._cover_is_stale = False
        # SMTC fallback lockout: prevent window-title oscillation after SMTC override.
        self._smtc_lockout_until = 0.0
        self._smtc_lockout_prev_song = ""
        # SmtcEngine reference (set externally before use, isolated thread)
        self._smtc_engine = None
        # Detection priority: ordered list ["qqmusic", "netease"]
        self._detection_priority: list[str] = ["qqmusic", "netease"]
        # Lyrics source priority: qq_first | ne_first | qq_only | ne_only
        self._lyrics_source_priority: str = "qq_first"

    def set_picture_dir(self, path):
        self._override_picture_dir = path

    def set_smtc_engine(self, engine) -> None:
        """Set the SmtcEngine instance for reading SMTC data.

        Replaces all direct core.progress imports with thread-safe
        property reads from the isolated SmtcEngine daemon thread.
        """
        self._smtc_engine = engine

    def set_detection_priority(self, priority: list[str]) -> None:
        """Set the detection priority order. First = highest priority.

        Args:
            priority: e.g. ["qqmusic", "netease"] or ["netease"]
        """
        priority = [p for p in priority if p in ("qqmusic", "netease")]
        if not priority:
            priority = ["qqmusic", "netease"]
        if priority != self._detection_priority:
            _reader_log(f"detection_priority: {self._detection_priority} → {priority}")
        self._detection_priority = priority

    def set_lyrics_source_priority(self, priority: str) -> None:
        """Set the lyrics source priority strategy.

        Args:
            priority: "qq_first" | "ne_first" | "qq_only" | "ne_only"
        """
        valid = ("qq_first", "ne_first", "qq_only", "ne_only")
        if priority not in valid:
            priority = "qq_first"
        if priority != self._lyrics_source_priority:
            _reader_log(f"lyrics_source_priority: {self._lyrics_source_priority} → {priority}")
        self._lyrics_source_priority = priority

    # ---- Properties (thread-safe) ----
    @property
    def song_name(self):
        return self._last_song

    @property
    def artist_name(self):
        return self._last_artist

    @property
    def cover_image(self):
        with self._lock:
            img = self._cover_image
            if img is not None:
                return img
            # Fallback: use best candidate from local cache
            candidates = getattr(self, "_cover_candidates", None)
            if candidates:
                idx = getattr(self, "_auto_cover_index", 0)
                if idx < 0 or idx >= len(candidates):
                    idx = 0
                return candidates[idx][1] if candidates[idx][1] else None
        return None

    @property
    def lyrics(self):
        with self._lock:
            return self._lyrics_plain

    @property
    def lyrics_timed(self):
        with self._lock:
            return list(self._lyrics_timed)

    @property
    def lyrics_merged(self):
        """New bilingual format: list of (time_ms, main_text, trans_text) triples."""
        with self._lock:
            return list(self._lyrics_merged)

    @property
    def lyrics_trans(self):
        with self._lock:
            return self._lyrics_trans

    @property
    def duration_seconds(self):
        with self._lock:
            return self._last_duration_secs

    @property
    def is_playing(self):
        if self._playing_cache_time > time.time() - 0.5:
            return self._playing_cache
        priority = self._detection_priority
        hwnd, _ = find_player_window(priority)
        if hwnd is not None:
            self._playing_cache = True
            self._playing_cache_time = time.time()
            return True
        # ★ Fallback: SMTC playback status. Only accept players in the
        # configured priority list.
        try:
            _s = self._smtc_engine
            if _s is not None:
                status = _s.playback_status
                if status in ("PLAYING", "PAUSED", "OPENED"):
                    result = _s.player in priority
                else:
                    result = False
            else:
                result = False
        except Exception:
            result = False
        self._playing_cache = result
        self._playing_cache_time = time.time()
        return result

    @property
    def is_loading(self):
        with self._lock:
            return self._loading

    @property
    def live_cover(self):
        """Return the current live SMTC thumbnail directly.

        Independent of _cover_candidates / _cover_is_stale / _cover_image.
        Used by settings UI — never touches main overlay cover pipeline.
        """
        try:
            _s = self._smtc_engine
            if _s is not None:
                _thumb = _s.thumbnail
                if _thumb is not None:
                    return _thumb  # returns a copy (SmtcEngine.thumbnail makes a copy)
        except Exception:
            pass
        return None

    @property
    def cover_candidates(self):
        with self._lock:
            candidates = getattr(self, "_cover_candidates", None)
            if candidates:
                return [(n, img.copy() if hasattr(img, 'copy') else img) for n, img in candidates]
            ci = self._cover_image
            _is_stale = self._cover_is_stale
        # ★ Skip stale _cover_image — belongs to previous song
        if ci and not _is_stale:
            return [("QQ音乐在线", ci.copy() if hasattr(ci, 'copy') else ci)]
        # ★ Last resort: SMTC thumbnail directly (bypasses title match for NetEase)
        # ★ Respect _cover_is_stale — after a song change, SMTC may still
        # hold the previous song's thumbnail. Returning it here would cause
        # has_cover_ready() to return True prematurely, making the fade-in
        # show the old cover briefly before switching to the new one.
        if not _is_stale:
            try:
                _s = self._smtc_engine
                if _s is not None:
                    _thumb = _s.thumbnail
                    if _thumb is not None:
                        return [("SMTC封面", _thumb.copy() if hasattr(_thumb, 'copy') else _thumb)]
            except Exception as e:
                _reader_logger.warn(f"cover_candidates SMTC fallback failed: {e}")
        return []

    @property
    def auto_cover_index(self):
        self._refresh_smtc_if_needed()
        with self._lock:
            return self._auto_cover_index

    def get_selected_cover(self, index=0):
        """Return the selected cover image WITHOUT copying.

        ★ HOT PATH — called every frame (~60fps) from _provide_data().
        Returns the original PIL Image to keep the texture cache (id-based)
        stable across frames. Callers MUST NOT mutate the returned image.
        The public cover_candidates property returns copies for safety;
        use that for non-rendering consumers (settings UI, etc.).
        """
        with self._lock:
            candidates = getattr(self, "_cover_candidates", None)
            # ★ Skip stale cover — candidates from before a song change
            # may still reference the previous song's images.
            if candidates and not self._cover_is_stale:
                if 0 <= index < len(candidates):
                    return candidates[index][1]
                return candidates[0][1]
            img = self._cover_image
            # ★ Also skip stale _cover_image
            if img is not None and not self._cover_is_stale:
                return img
        # ★ SMTC thumbnail as last resort (handles NetEase without web API cover)
        # ★ Skip if stale — after a song change, SMTC may still hold the
        # previous song's thumbnail. Returning it would flash the old cover
        # briefly before the new cover data arrives.
        if not self._cover_is_stale:
            try:
                _s = self._smtc_engine
                if _s is not None:
                    _thumb = _s.thumbnail
                    if _thumb is not None:
                        return _thumb  # return original, not copy
            except Exception as e:
                _reader_logger.warn(f"get_selected_cover live SMTC failed: {e}")
        # ★ No cover at all — use default placeholder
        return self._get_default_cover()

    _default_cover_img = None

    @classmethod
    def _get_default_cover(cls):
        if cls._default_cover_img is not None:
            return cls._default_cover_img
        try:
            import os
            _proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(_proj, "assets", "default_cover.png")
            from PIL import Image as PILImage
            cls._default_cover_img = PILImage.open(path).convert("RGBA")
        except Exception:
            cls._default_cover_img = False
        return cls._default_cover_img if cls._default_cover_img is not False else None

    # ---- SMTC text sanity ----
    @staticmethod
    def _smtc_text_sane(text: str) -> bool:
        """Reject SMTC text that is clearly garbled (encoding mojibake).

        WinRT string transcoding can produce garbage for non-ASCII titles.
        Garbled text typically has a high density of rare CJK characters,
        replacement chars (U+FFFD), or control characters.
        """
        if not text:
            return False
        # Replacement character — always bad
        if '�' in text:
            return False
        # Control characters (except common whitespace)
        control_count = sum(1 for c in text if ord(c) < 0x20 and c not in '\t\r\n')
        if control_count > 0:
            return False
        total = len(text)
        if total == 0:
            return False
        # High density of CJK Unified Ideographs Extension B+ (U+20000–U+2FFFF)
        # and CJK Compatibility Ideographs Supplement (U+2F800–U+2FA1F).
        # These are extremely rare in real song titles — heavy presence = mojibake.
        rare_cjk = sum(1 for c in text
                       if 0x20000 <= ord(c) <= 0x2FFFF
                       or 0x2F800 <= ord(c) <= 0x2FA1F)
        if rare_cjk / total > 0.3:
            return False
        # Private Use Area (U+E000–U+F8FF) — also rare in real metadata
        pua = sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF)
        if pua / total > 0.3:
            return False
        # Unassigned/surrogate codepoints (U+D800–U+DFFF) — never valid in Python str
        surrogates = sum(1 for c in text if 0xD800 <= ord(c) <= 0xDFFF)
        if surrogates > 0:
            return False
        return True

    # ---- Update ----
    def update(self):
        """Poll for song changes from window title / SMTC sources.

        Orchestrates: source resolution → song-change detection → state update.
        Returns True if the song changed this frame.
        """
        priority = self._detection_priority
        hwnd, title = find_player_window(priority)
        song, artist = self._resolve_song_info(priority, hwnd, title)
        if not song:
            return False
        return self._apply_song_change(song, artist)

    # ================================================================
    # Song-info resolution (extracted from update() for readability)
    # ================================================================

    def _resolve_song_info(self, priority: list[str], hwnd: int | None,
                           title: str | None) -> tuple[str | None, str | None]:
        """Resolve current (song, artist) from window title and/or SMTC.

        Multi-source strategy ordered by priority:
          1. QQ Music window title   (authoritative, "Song - Artist")
          2. NetEase window + SMTC   (window has no song info)
          3. SMTC fallback           (when no window is available)
        Returns (None, None) if no valid song info is available.
        """
        if not hwnd or not title:
            return self._resolve_from_smtc_only(priority)

        return self._resolve_from_window(priority, hwnd, title)

    def _resolve_from_smtc_only(self, priority: list[str]):
        """SMTC fallback when no player window is available."""
        _update_diag = getattr(self.__class__, "_update_diag_last", 0)
        if time.time() - _update_diag > 30:
            self.__class__._update_diag_last = time.time()
            _reader_log(f"update: NO player window (priority={priority}) — "
                       f"trying SMTC fallback")

        try:
            _s = self._smtc_engine
            if _s is None:
                return (None, None)
            song = (_s.title or "").strip()
            artist = (_s.artist or "").strip()
        except Exception:
            return (None, None)

        if not (song and artist
                and self._smtc_text_sane(song)
                and self._smtc_text_sane(artist)):
            return (None, None)

        return (song, artist)

    def _resolve_from_window(self, priority: list[str], hwnd: int, title: str):
        """Resolve song info from a found player window."""
        # Identify the actual player source for diagnostics
        _found_player = "unknown"
        for p in priority:
            if p == "qqmusic":
                _hw, _ = find_qqmusic_window()
                if _hw == hwnd:
                    _found_player = "qqmusic"
                    break
            elif p == "netease":
                _hw, _ = find_netease_window()
                if _hw == hwnd:
                    _found_player = "netease"
                    break
        _diag_key = getattr(self.__class__, "_update_source_diag", "")
        if _diag_key != _found_player:
            self.__class__._update_source_diag = _found_player
            _reader_log(f"update: active source = {_found_player} "
                       f"(hwnd={hwnd}, title={title!r})")

        # NetEase window has no song info — always use SMTC
        if " - " not in (title or ""):
            return self._resolve_from_smtc(title)

        # QQ Music window — parse title, fall back to SMTC if needed
        song, artist = parse_window_title(title)
        if not song or not artist:
            # Title can't be parsed — fall back to SMTC
            _update_diag = getattr(self.__class__, "_update_diag_parse", 0)
            if time.time() - _update_diag > 30:
                self.__class__._update_diag_parse = time.time()
                _reader_log(f"update: can't parse title={title!r} "
                           f"— trying SMTC fallback")
            return self._resolve_from_smtc(title)

        # SMTC cross-check for NetEase (window title never updates)
        if _found_player == "netease":
            song, artist = self._smtc_cross_check(song, artist)

        return (song, artist)

    def _resolve_from_smtc(self, _fallback_label: str = ""):
        """Try to get song info from SMTC. Returns (None, None) on failure."""
        try:
            _s = self._smtc_engine
            if _s is None:
                return (None, None)
            song = (_s.title or "").strip()
            artist = (_s.artist or "").strip()
        except Exception:
            return (None, None)

        if not (song and artist
                and self._smtc_text_sane(song)
                and self._smtc_text_sane(artist)):
            return (None, None)

        return (song, artist)

    def _smtc_cross_check(self, song: str, artist: str):
        """SMTC cross-check for NetEase: window title may be stale."""
        try:
            _s = self._smtc_engine
            if _s is not None:
                _smtc_song = (_s.title or "").strip()
                _smtc_artist = (_s.artist or "").strip()
                if (_smtc_song and _smtc_artist
                        and self._smtc_text_sane(_smtc_song)
                        and self._smtc_text_sane(_smtc_artist)):
                    _smtc_key = (_smtc_song, _smtc_artist)
                    _window_key = (song, artist)
                    if _smtc_key != _window_key:
                        _reader_log(f"update: SMTC override "
                                   f"stale_window={_window_key} → "
                                   f"smtc={_smtc_key}")
                        return (_smtc_song, _smtc_artist)
        except Exception as e:
            _reader_logger.warn(f"SMTC override in update failed: {e}")
        return (song, artist)

    # ================================================================
    # Song-change state transition
    # ================================================================

    @staticmethod
    def _normalize_artist(a: str) -> str:
        """Normalize artist name for comparison: strip parenthetical suffixes,
        normalize whitespace around separators.  This prevents false-positive
        song changes when SMTC provides slightly different formatting than
        the window title (e.g. 'TRUE' vs 'TRUE (唐沢美帆)')."""
        import re
        a = a.strip()
        a = re.sub(r'\s*\(.*?\)\s*$', '', a)  # trailing (suffix)
        a = re.sub(r'\s*/\s*', '/', a)         # normalize spaces around /
        a = re.sub(r'\s+', ' ', a)             # collapse whitespace
        return a

    def _apply_song_change(self, song: str, artist: str) -> bool:
        """Apply a song change: update cached state and launch data fetches.

        Returns True if the song actually changed (different from _last_song/_last_artist).
        ★ Uses normalized artist comparison to suppress false song_changes
        caused by SMTC↔window title formatting differences.
        """
        song_changed = (song != self._last_song or artist != self._last_artist)
        # ★ Bug #174: suppress same-name song changes (formatting differences only)
        if song_changed and song == self._last_song:
            norm_old = self._normalize_artist(self._last_artist)
            norm_new = self._normalize_artist(artist)
            if norm_old == norm_new:
                _reader_log(f"update: SUPPRESSED same-song change "
                           f"(artist fmt: {self._last_artist!r}→{artist!r}, "
                           f"normalized: {norm_old!r})")
                song_changed = False
            else:
                _reader_log(f"update: SONG CHANGE {self._last_song!r}→{song!r} "
                           f"({self._last_artist!r}→{artist!r})")
        elif song_changed:
            _reader_log(f"update: SONG CHANGE {self._last_song!r}→{song!r} "
                       f"({self._last_artist!r}→{artist!r})")

        if song_changed:
            self._last_song = song
            self._last_artist = artist
            self._last_songmid = None
            self._last_albummid = None
            self._last_duration_secs = 0
            # ★ Clear old cover data — the old _cover_image belongs to the
            # previous song.  Keeping it risks leaking it into the new song's
            # _gather_cover_candidates() once _cover_is_stale is cleared.
            # The background search thread will fetch the new cover asynchronously.
            self._cover_image = None
            self._cover_candidates = []
            self._cover_is_stale = True   # old _cover_image belongs to previous song
            self._smtc_verified = False
            self._smtc_gathered = False   # ★ allow _refresh_smtc_if_needed() for new song
            self._first_candidate_time = 0.0
            self._lyrics_timed = []
            self._lyrics_merged = []
            self._lyrics_plain = ""
            self._lyrics_trans = ""
            # ★ Increment generation counter — background threads check this
            # before applying results, discarding stale data from old songs.
            self._search_generation += 1
            self._search_song_info(song, artist)

        return song_changed

    def update_api_results(self):
        """No-op in this implementation — results are applied in background threads."""
        pass

    # ---- Background search ----
    def _search_song_info(self, song, artist):
        with self._lock:
            self._loading = True
            gen = self._search_generation  # capture current generation
        self._loading_timestamp = time.time()

        def _do_search():
            try:
                songmid, albummid, interval = _search_qqmusic(song, artist)
                # Check if song changed while we were searching
                if self._search_generation != gen:
                    _reader_log(f"_do_search: STALE gen={gen} current={self._search_generation} — discarding")
                    self._gather_cover_candidates()
                    with self._lock:
                        self._loading = False
                    return
                if not songmid:
                    self._gather_cover_candidates()
                    with self._lock:
                        self._loading = False
                    return
                with self._lock:
                    self._last_songmid = songmid
                    self._last_albummid = albummid
                    # ★ Use interval from search result immediately.
                    # No need to wait for a separate API call.
                    if interval > 0:
                        self._last_duration_secs = interval
                        _reader_log(f"search: interval={interval}s "
                                   f"({interval//60}:{interval%60:02d}) — from search result")

                done = [False, False, False]

                def _check_done():
                    if all(done):
                        with self._lock:
                            self._loading = False

                def _fetch_lyrics_task():
                    # ★ Lyrics source priority strategy (configurable).
                    #   qq_first — QQ Music primary, NetEase fallback (no QQ lyrics)
                    #   ne_first — NetEase primary, QQ fallback (better translation)
                    #   qq_only  — QQ Music only
                    #   ne_only  — NetEase Cloud Music only
                    lyric, trans = "", ""
                    strategy = self._lyrics_source_priority

                    if strategy == "ne_only":
                        # NetEase only
                        ne_result = _search_netease(song, artist)
                        if ne_result:
                            ne_id, _, _, _ = ne_result
                            ne_lyric, ne_trans = _fetch_lyrics_netease(ne_id)
                            lyric, trans = ne_lyric, ne_trans
                    elif strategy == "qq_only":
                        # QQ Music only
                        lyric, trans = _fetch_lyrics(songmid)
                    elif strategy == "qq_first":
                        # QQ first — try QQ, fall back to NetEase if empty or unparseable
                        lyric, trans = _fetch_lyrics(songmid)
                        if lyric:
                            # Pre-parse to check if QQ returned real LRC content.
                            # Short responses (<100 bytes) like "暂无歌词" parse
                            # to 0 lines — treat as empty and fall back to NetEase.
                            _, _timed_check = LRCParser.parse(lyric)
                            if len(_timed_check) < 2:
                                _reader_log(f"_fetch_lyrics_task: QQ lyric unparseable "
                                           f"(raw_len={len(lyric)} parsed_lines={len(_timed_check)}) "
                                           f"— trying NetEase fallback")
                                lyric = ""
                        if not lyric:
                            ne_result = _search_netease(song, artist)
                            if ne_result:
                                ne_id, _, _, _ = ne_result
                                ne_lyric, ne_trans = _fetch_lyrics_netease(ne_id)
                                lyric, trans = ne_lyric, ne_trans
                    else:  # ne_first (default) — NetEase first, QQ fallback
                        ne_result = _search_netease(song, artist)
                        if ne_result:
                            ne_id, _, _, _ = ne_result
                            ne_lyric, ne_trans = _fetch_lyrics_netease(ne_id)
                            if ne_lyric:
                                # Pre-parse to check if NetEase returned real LRC
                                _, _timed_check = LRCParser.parse(ne_lyric)
                                if len(_timed_check) < 2:
                                    _reader_log(f"_fetch_lyrics_task: NetEase lyric unparseable "
                                               f"(raw_len={len(ne_lyric)} parsed_lines={len(_timed_check)}) "
                                               f"— trying QQ fallback")
                                    lyric, trans = _fetch_lyrics(songmid)
                                else:
                                    lyric = ne_lyric
                                    # Also try QQ for original lyrics with translation
                                    qq_lyric, qq_trans = _fetch_lyrics(songmid)
                                    if qq_trans:
                                        lyric = qq_lyric
                                        trans = qq_trans
                                    elif ne_trans:
                                        lyric = qq_lyric or ne_lyric
                                        trans = ne_trans
                                    else:
                                        trans = ""
                            else:
                                lyric, trans = _fetch_lyrics(songmid)
                        else:
                            lyric, trans = _fetch_lyrics(songmid)

                    # ★ Check generation BEFORE applying — discard if song changed
                    if self._search_generation != gen:
                        _reader_log(f"_fetch_lyrics_task: STALE gen={gen} current={self._search_generation} — discarding")
                        done[0] = True
                        _check_done()
                        return
                    with self._lock:
                        self._lyrics_plain = lyric
                        self._lyrics_trans = trans
                        # Use new lyrics.py parser and merger
                        if trans and lyrics_has_korean(lyric or ""):
                            # Korean song — translation is better than original
                            merged = parse_and_merge(lyric, trans, strategy="fuzzy", tolerance_ms=500)
                            # Use trans as main text for Korean songs
                            self._lyrics_merged = merged
                            self._lyrics_timed = [(ts, tt or mt) for ts, mt, tt in merged]
                            _reader_log(f"_fetch_lyrics_task: OK korean merged "
                                       f"len={len(merged)} "
                                       f"first={merged[0] if merged else 'nil'}")
                        elif lyric and trans:
                            merged = parse_and_merge(lyric, trans, strategy="fuzzy", tolerance_ms=500)
                            self._lyrics_merged = merged
                            # Legacy format for backward compat
                            self._lyrics_timed = merge_timed_legacy(
                                [(ts, mt) for ts, mt, tt in merged],
                                [(ts, tt) for ts, mt, tt in merged if tt],
                            )
                        elif lyric:
                            _, timed_main = LRCParser.parse(lyric)
                            self._lyrics_merged = [(ts, txt, "") for ts, txt in timed_main]
                            self._lyrics_timed = timed_main
                            _reader_log(f"_fetch_lyrics_task: OK lyric_only "
                                       f"len={len(timed_main)} "
                                       f"first={timed_main[0] if timed_main else 'nil'} "
                                       f"last={timed_main[-1] if timed_main else 'nil'}")
                        else:
                            self._lyrics_merged = []
                            self._lyrics_timed = []
                    done[0] = True
                    _check_done()

                def _fetch_duration():
                    # ★ If search already provided the interval, skip the
                    # separate API call — it would return the same value.
                    if interval > 0:
                        _reader_log(f"fetch_duration: SKIPPED — already have "
                                   f"interval={interval}s from search result")
                    else:
                        sm = songmid
                        if sm:
                            try:
                                result = _get_albummid_from_song(sm, get_interval=True)
                                if isinstance(result, tuple):
                                    dur = result[1] if len(result) > 1 else 0
                                    if dur > 0 and self._search_generation == gen:
                                        with self._lock:
                                            self._last_duration_secs = dur
                                    _reader_log(f"fetch_duration: songmid={sm} interval={dur}s "
                                               f"({dur//60}:{dur%60:02d})")
                                else:
                                    _reader_log(f"fetch_duration: unexpected result type {type(result)}")
                            except Exception as e:
                                _reader_log(f"fetch_duration ERROR: {e}")
                        else:
                            _reader_log("fetch_duration: SKIPPED — no songmid")
                    done[1] = True
                    _check_done()

                def _fetch_cover_task():
                    try:
                        am = albummid
                        _reader_log(f"fetch_cover: albummid={am} song={song!r} artist={artist!r}")
                        if am:
                            cached = self._load_cached_cover(am)
                            if cached:
                                _reader_log(f"fetch_cover: CACHE HIT albummid={am}")
                                if self._search_generation == gen:
                                    with self._lock:
                                        self._cover_image = cached
                                        self._cover_is_stale = False
                            else:
                                cover = _fetch_album_art(am)
                                if cover:
                                    _reader_log(f"fetch_cover: WEB FETCH OK albummid={am} size={cover.size}")
                                    if self._search_generation == gen:
                                        with self._lock:
                                            self._cover_image = cover
                                            self._cover_is_stale = False
                                    self._save_cached_cover(am, cover)
                                else:
                                    _reader_log(f"fetch_cover: WEB FETCH FAILED albummid={am}")
                        else:
                            _reader_log(f"fetch_cover: NO albummid → trying local-only")
                        self._gather_cover_candidates()
                    except Exception as e:
                        _reader_log(f"fetch_cover ERROR: {e}")
                        pass
                    finally:
                        done[2] = True
                        _check_done()

                for t_fn in [_fetch_lyrics_task, _fetch_duration, _fetch_cover_task]:
                    threading.Thread(target=t_fn, daemon=True).start()
            except Exception:
                with self._lock:
                    self._loading = False

        threading.Thread(target=_do_search, daemon=True).start()

    # ---- Cover cache ----
    def _get_cached_cover_path(self, albummid):
        return os.path.join(self._cache_dir, f"cover_{albummid}.jpg") if albummid else ""

    def _load_cached_cover(self, albummid):
        path = self._get_cached_cover_path(albummid)
        if path and os.path.exists(path):
            try:
                return Image.open(path)
            except Exception as e:
                _reader_logger.warn(f"cached cover load failed {path!r}: {e}")
        return None

    def _save_cached_cover(self, albummid, image):
        path = self._get_cached_cover_path(albummid)
        if path and image:
            try:
                image.save(path, "JPEG", quality=85)
            except Exception as e:
                _reader_logger.warn(f"cached cover save failed {path!r}: {e}")

    # ---- Local cover detection & cover candidates ----
    def _detect_qqmusic_picture_dir(self):
        if self._override_picture_dir and os.path.isdir(self._override_picture_dir):
            return self._override_picture_dir
        ini = os.path.join(os.environ.get("APPDATA", ""), "Tencent", "QQMusic", "WebkitCachePath.ini")
        if os.path.exists(ini):
            try:
                with open(ini, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("Path="):
                            cache_root = os.path.dirname(line[5:].strip())
                            candidate = os.path.join(cache_root, "QQMusicPicture")
                            if os.path.isdir(candidate):
                                return candidate
            except Exception as e:
                _reader_logger.warn(f"QQMusicPicture Config.ini parse failed: {e}")
        for base in [os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", ""),
                     os.path.join(os.environ.get("USERPROFILE", ""), "Documents")]:
            candidate = os.path.join(base, "QQMusicPicture")
            if os.path.isdir(candidate):
                return candidate
        return None

    def _parse_filename_artist(self, filename):
        name = filename.rsplit("_", 1)[0]
        idx = name.find("_")
        if idx > 0:
            return name[:idx]
        return name

    def _find_local_cover_candidates(self):
        candidates = []
        pic_dir = self._detect_qqmusic_picture_dir()
        if not pic_dir:
            return candidates
        artist_lower = self._last_artist.lower().strip() if self._last_artist else ""
        album_lower = ""
        if not artist_lower:
            try:
                _s = self._smtc_engine
                if _s is not None:
                    album_lower = (_s.album_title or "").lower().strip()
            except Exception:
                pass
        has_artist = bool(artist_lower)
        try:
            for fn in os.listdir(pic_dir):
                if not fn.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                fn_lower = fn.lower()
                matched = False
                if has_artist:
                    fn_artist = self._parse_filename_artist(fn).lower()
                    matched = (artist_lower in fn_artist or fn_artist in artist_lower)
                if not matched and album_lower:
                    matched = album_lower in fn_lower
                if matched:
                    path = os.path.join(pic_dir, fn)
                    try:
                        candidates.append((fn, Image.open(path)))
                    except Exception:
                        pass
        except Exception as e:
            _reader_logger.warn(f"QQMusicPicture dir scan failed: {e}")
        return candidates

    def _gather_cover_candidates(self):
        """Build cover candidate list and run auto-matching.

        ★ Build everything in local variables, then atomically swap under
        _lock. This prevents races where _provide_data() (main thread) reads
        _auto_cover_index and _cover_candidates while a background thread is
        mid-way through rebuilding them.
        """
        try:
            candidates = []
            smtc_gathered = False
            smtc_verified = False

            # Candidate 0: SMTC thumbnail (taskbar preview) — first priority
            _s = None  # ★ ensure defined for _is_netease_player check below
            try:
                _s = self._smtc_engine
                if _s is not None:
                    thumb = _s.thumbnail
                    if thumb is not None:
                        smtc_title = (_s.title or "").lower().strip()
                        smtc_artist = (_s.artist or "").lower().strip()
            except Exception:
                thumb = None
                smtc_title = ""
                smtc_artist = ""

            try:
                if thumb is not None:
                    cur_song = (self._last_song or "").lower().strip()
                    cur_artist = (self._last_artist or "").lower().strip()
                    title_match = cur_song and smtc_title and (cur_song in smtc_title or smtc_title in cur_song)
                    artist_match = cur_artist and smtc_artist and (cur_artist in smtc_artist or smtc_artist in cur_artist)
                    # ★ NetEase: WinRT encoding may produce different garbled
                    # output each time _s.title is accessed. Since NetEase has
                    # no alternative cover source (window title carries no song
                    # info), always accept the SMTC thumbnail.
                    _is_netease = (_s is not None and _s.player == "netease")
                    if title_match or artist_match or _is_netease:
                        if _is_netease and not (title_match or artist_match):
                            _reader_log(f"SMTC ACCEPTED (netease fallback) "
                                       f"title={smtc_title!r} cur={cur_song!r}")
                        candidates.append(("SMTC封面 ✓", thumb.copy()))
                        smtc_gathered = True
                        smtc_verified = True
                        if title_match or artist_match:
                            _reader_log(f"SMTC ACCEPTED title={smtc_title!r} artist={smtc_artist!r} "
                                       f"cur_song={cur_song!r} cur_artist={cur_artist!r}")
                    else:
                        _reader_log(f"SMTC REJECTED (stale) smtc_title={smtc_title!r} smtc_artist={smtc_artist!r} "
                                   f"cur_song={cur_song!r} cur_artist={cur_artist!r}")
                else:
                    _reader_log("SMTC no thumbnail")
            except Exception as e:
                _reader_log(f"SMTC error: {e}")

            # Candidate 1: Web API (skip if stale — belongs to previous song).
            # ★ When NetEase is the current SMTC player, skip QQ Music web API
            # covers entirely.  QQ Music API only serves QQ Music catalog —
            # covers downloaded from it for NetEase songs are likely wrong.
            # NetEase's only reliable cover source is the SMTC thumbnail.
            _is_netease_player = (_s is not None and _s.player == "netease")
            if self._cover_image and not self._cover_is_stale and not _is_netease_player:
                candidates.append(("QQ音乐在线", self._cover_image))

            # Candidate 2+: Local
            local_count = 0
            for name, img in self._find_local_cover_candidates():
                candidates.append((name, img))
                local_count += 1

            if not candidates and self._cover_image and not self._cover_is_stale and not _is_netease_player:
                candidates.append(("QQ音乐在线", self._cover_image))

            if candidates and self._first_candidate_time == 0.0:
                self._first_candidate_time = time.time()

            # Auto-match on the SAME candidates list we just built.
            # _automatically_match_cover() reads self._cover_candidates which
            # is the OLD list — we must pass our local list explicitly.
            auto_idx = self._automatically_match_cover(candidates)

            # Check saved preference
            try:
                from core.cover_prefs import get_preferred_source
                pref_name = get_preferred_source(self._last_song, self._last_artist)
                if pref_name and candidates:
                    for i, (name, _) in enumerate(candidates):
                        if name == pref_name:
                            auto_idx = i
                            _reader_log(f"gather: preference match {pref_name!r} → auto_idx={i}")
                            break
            except Exception as e:
                _reader_log(f"gather: preference lookup error: {e}")

            # ★ NetEase: when SMTC is verified and no QQ Music API covers are in
            # the candidate list (they were skipped above), force auto_idx to 0
            # (SMTC thumbnail).  For QQ Music, keep the auto-match result — the
            # hash verification ensures API/local covers only win when they match
            # the SMTC thumbnail (same album art, higher resolution).
            if smtc_verified and _is_netease_player and auto_idx != 0:
                _reader_log(f"gather: netease SMTC verified, auto_idx {auto_idx} → 0")
                auto_idx = 0

            # ★ Save discovered covers to persistent cache for the cover library
            _song = self._last_song or ""
            _artist = self._last_artist or ""
            for _name, _img in candidates:
                try:
                    from core.cover_cache import save_cover
                    save_cover(_img, _song, _artist)
                except Exception as e:
                    _reader_logger.warn(f"cover_cache save failed: {e}")

            # ★ Atomic swap — prevent _provide_data() from seeing inconsistent state.
            # ★ NEVER overwrite with an empty list if we had candidates before.
            # An empty gather (e.g. SMTC thumbnail temporarily unavailable after a
            # song change) should not clear the previous valid candidates — it
            # would drop the cover on the main overlay AND in the settings UI.
            if not candidates:
                with self._lock:
                    old = self._cover_candidates
                if old and len(old) > 0:
                    _reader_log(f"gather: keeping {len(old)} old candidates (new gather produced 0)")
                    return
            with self._lock:
                # ★ Clear stale flag BEFORE setting candidates — prevents
                # a race where has_cover_ready() (lock-free read) sees
                # non-empty _cover_candidates but get_selected_cover()
                # (locked read) still sees _cover_is_stale=True.
                # Order: stale=False first → then candidates set.
                if candidates:
                    self._cover_is_stale = False
                self._cover_candidates = candidates
                self._smtc_gathered = smtc_gathered
                self._smtc_verified = smtc_verified
                self._auto_cover_index = auto_idx

            _reader_log(f"gather: {len(candidates)} candidates "
                       f"(smtc_verified={smtc_verified}, local={local_count}) "
                       f"→ auto_idx={auto_idx} "
                       f"names={[c[0] for c in candidates[:5]]}")
        except Exception:
            with self._lock:
                self._cover_candidates = self._cover_candidates or []

    def _refresh_smtc_if_needed(self):
        if self._smtc_gathered:
            return
        try:
            _s = self._smtc_engine
            if _s is None:
                return
            thumb = _s.thumbnail
            if thumb is not None:
                # ★ Verify the SMTC thumbnail actually belongs to the CURRENT song
                # before gathering.  If it's stale (from a previous song), gathering
                # prematurely locks in the wrong cover and prevents self-correction.
                smtc_title = (_s.title or "").lower()
                smtc_artist = (_s.artist or "").lower()
                cur_song = (self._last_song or "").lower()
                cur_artist = (self._last_artist or "").lower()
                title_ok = cur_song and smtc_title and (cur_song in smtc_title or smtc_title in cur_song)
                artist_ok = cur_artist and smtc_artist and (cur_artist in smtc_artist or smtc_artist in cur_artist)
                if not title_ok and not artist_ok:
                    # SMTC still has the previous song — skip, wait for refresh
                    return
                self._gather_cover_candidates()
        except Exception as e:
            _reader_logger.warn(f"_refresh_smtc_if_needed failed: {e}")

    def has_cover_ready(self) -> bool:
        """Return True when a cover is selected and ready for fade-in.

        Priority chain (aligned with fade-out duration ~900ms):
        1. SMTC verified (perceptual-hash matched) → best, use immediately
        2. SMTC wait window (~1.5s, covers fade-out + one extra poll cycle)
        3. SMTC timeout → local match by artist+song filename
        4. No local → web API cover (slower but always eventually available)
        5. Nothing at all → keep waiting
        """
        # Already verified → ready
        if self._smtc_verified:
            return True

        # Try refreshing SMTC (re-checks cache, re-gathers if fresh)
        self._refresh_smtc_if_needed()
        if self._smtc_verified:
            return True

        candidates = getattr(self, "_cover_candidates", None)
        # No candidates gathered yet → keep waiting
        if not candidates:
            return False

        # ★ SMTC wait window: give SMTC time to refresh during fade-out.
        # If SMTC catches up in this window, we get a verified high-res match.
        if self._first_candidate_time > 0:
            elapsed = time.time() - self._first_candidate_time
            if elapsed < 1.5:
                return False  # still waiting for SMTC

        # ★ SMTC timeout — check if auto-match found a local cover by name.
        # _automatically_match_cover() tries artist+song filename scoring
        # (score ≥ 3 = both artist and song match) when SMTC not verified.
        auto_idx = getattr(self, "_auto_cover_index", -1)
        if auto_idx >= 1:
            # Non-zero index means a local/web cover was matched by name
            return True

        # ★ Web API cover is the last resort.  Wait for it if not yet available.
        # Candidates[0] at this point is typically from web API (or SMTC thumb).
        if candidates[0][1] is not None:
            return True

        # No cover available yet — keep waiting
        return False

    # ---- Perceptual hash auto-matching ----
    @staticmethod
    def _image_hash(img, size=16):
        img = img.convert('L').resize((size, size), Image.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / max(len(pixels), 1)
        return ''.join('1' if p > avg else '0' for p in pixels)

    @staticmethod
    def _hash_distance(h1, h2):
        return sum(1 for a, b in zip(h1, h2) if a != b)

    def _automatically_match_cover(self, candidates=None):
        if candidates is None:
            candidates = self._cover_candidates
        if not candidates:
            return -1

        first_name = str(candidates[0][0]) if candidates else ""
        if first_name.startswith("SMTC封面"):
            smtc_img = candidates[0][1]
            smtc_hash = self._image_hash(smtc_img)
            best_idx = -1
            best_dist = 999
            # ★ Only hash-match LOCAL files — QQ Music API covers are
            # priority 3 (below SMTC缩略图, priority 2).  A hash-matched
            # local file = SMTC高清 (priority 1, same cover, higher res).
            for i in range(1, len(candidates)):
                name = candidates[i][0]
                if name.startswith("QQ音乐") or name.startswith("QQ闊充箰"):
                    continue  # skip API covers — lower priority than SMTC thumb
                try:
                    cand_hash = self._image_hash(candidates[i][1])
                    dist = self._hash_distance(smtc_hash, cand_hash)
                    if dist < best_dist:
                        best_dist = dist
                        best_idx = i
                except Exception:
                    pass
            if best_idx >= 0 and best_dist < 8:
                _reader_log(f"auto_match: SMTC hash → SMTC高清 local[{best_idx}] "
                           f"{candidates[best_idx][0]!r} (dist={best_dist})")
                return best_idx
            # ★ Primary hash failed — retry with smaller hash (8×8, less
            # sensitive to scaling differences) and more lenient threshold.
            smtc_hash8 = self._image_hash(smtc_img, size=8)
            best8 = -1; best8_dist = 999
            for i in range(1, len(candidates)):
                name = candidates[i][0]
                if name.startswith("QQ音乐") or name.startswith("QQ闊充箰"):
                    continue  # skip API covers
                try:
                    d = self._hash_distance(smtc_hash8, self._image_hash(candidates[i][1], size=8))
                    if d < best8_dist:
                        best8_dist = d; best8 = i
                except Exception:
                    pass
            if best8 >= 0 and best8_dist < 3:
                _reader_log(f"auto_match: SMTC hash (8×8) → SMTC高清 local[{best8}] "
                           f"{candidates[best8][0]!r} (dist={best8_dist})")
                return best8
            # ★ No local high-res match — keep SMTC thumbnail (priority 2).
            _reader_log(f"auto_match: SMTC hash → no local match, keeping SMTC thumb")
            return 0

        # ★ SMTC thumbnail NOT available (stale/rejected).
        # Without SMTC perceptual-hash verification, local files matched only
        # by artist name are unreliable — a prolific artist may have dozens of
        # covers, only one of which is correct for this song.
        #
        # Strategy:
        #  1. SMTC album title → filename  (only if SMTC is verified)
        #  2. Artist + song both in filename (score >= 3, requires BOTH)
        #  3. Fall back to web API cover (Candidate 0 — from QQ Music's own
        #     search API, almost always correct for the current song)

        # SMTC album title → filename (only trust if SMTC is verified for this song)
        if self._smtc_verified:
            try:
                _s = self._smtc_engine
                album_title = _s.album_title if _s is not None else ""
                if album_title:
                    album_lower = album_title.lower().strip()
                    for i, (name, _) in enumerate(candidates):
                        name_lower = name.lower()
                        if album_lower and album_lower in name_lower:
                            _reader_log(f"auto_match: SMTC album '{album_title}' → candidate[{i}] {name!r}")
                            return i
                        base = name_lower.rsplit("_", 1)[0]
                        if album_lower in base:
                            _reader_log(f"auto_match: SMTC album '{album_title}' → candidate[{i}] {name!r} (base match)")
                            return i
            except Exception as e:
                _reader_logger.warn(f"SMTC album match failed: {e}")

        # ★ Priority when SMTC is not available:
        #   1. QQ Music API cover (reliable for QQ Music catalog)
        #   2. Local file matched by artist+song name
        #   3. Whatever is at candidates[0]
        for i, (name, _) in enumerate(candidates):
            if name.startswith("QQ音乐"):
                _reader_log(f"auto_match: no SMTC → QQ Music API candidate[{i}]")
                return i

        # Artist+song filename matching — require BOTH artist AND song in filename.
        # score=1: artist only (unreliable)  → ignore
        # score=2: song only (uncommon)      → ignore
        # score=3: artist + song match       → use it
        song_lower = (self._last_song or "").lower()
        artist_lower = (self._last_artist or "").lower()
        best_score = 0
        best_idx = -1
        for i, (name, _) in enumerate(candidates):
            name_lower = name.lower()
            score = 0
            if artist_lower and artist_lower in name_lower:
                score += 1
            if song_lower and song_lower.replace(" ", "_") in name_lower:
                score += 2
            if score > best_score:
                best_score = score
                best_idx = i
        # ★ Require BOTH artist AND song (score >= 3) — prevents picking
        # a random cover from the same artist for a different song.
        if best_score >= 3:
            _reader_log(f"auto_match: artist+song → local[{best_idx}] {candidates[best_idx][0]!r} "
                       f"(score={best_score})")
            return best_idx

        # ★ Fallback: first available candidate
        _reader_log(f"auto_match: fallback → Candidate[0] {candidates[0][0]!r} "
                   f"(no SMTC, no API, best_local_score={best_score})")
        return 0 if candidates else -1

    # ---- Lyrics helpers ----
    @staticmethod
    def _has_korean(text):
        if not text:
            return False
        korean = re.compile(r'[가-힯ᄀ-ᇿ㄰-㆏]')
        count = len(korean.findall(text))
        return count > max(3, len(text) * 0.05)

    @staticmethod
    def _merge_timed(main, trans):
        if not trans:
            return list(main)
        if not main:
            return list(trans)
        trans_map = {}
        for ts, txt in trans:
            rounded = round(ts / 100) * 100
            if rounded not in trans_map:
                trans_map[rounded] = txt.strip()
        result, used_trans = [], set()
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
        for ts, txt in trans:
            rounded = round(ts / 100) * 100
            if rounded not in used_trans:
                result.append((ts, f"  {txt.strip()}"))
                used_trans.add(rounded)
        result.sort(key=lambda x: x[0])
        return result
