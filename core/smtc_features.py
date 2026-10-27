"""SMTC cycle feature learning — per-song persistence.

Learns:
- SMTC cycle length per song (for long-song detection)
- Max observed position (≈real duration)
- Confirmed duration (from SMTC media_props, verified metadata)
- Whether song is known to exceed SMTC cycle (>420s)

Data: .learned_smtc_features.json
"""
import json, os, time, threading
from typing import Optional
from core.logs import get_logger

_sf_log = get_logger("smtc_features")

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "QQMusicOverlayNG")
os.makedirs(_DATA_DIR, exist_ok=True)
# ★ Migrate from old location
_OLD_FILE = os.path.join(_APP_DIR, ".learned_smtc_features.json")
_FEATURE_FILE = os.path.join(_DATA_DIR, ".learned_smtc_features.json")
if os.path.exists(_OLD_FILE) and not os.path.exists(_FEATURE_FILE):
    import shutil
    shutil.copy2(_OLD_FILE, _FEATURE_FILE)
_lock = threading.RLock()  # reentrant: record_* mutate under _lock and call _mark_dirty

# ★ In-memory cache of the features DB.
# progress.update() calls record_max_position / record_confirmed_duration /
# get_known_duration on EVERY frame (60-120 fps).  The old implementation did
# a full-file read per call plus an unconditional atomic write per record —
# up to ~240 disk writes/s.  All reads now hit this cache; mutations mark it
# dirty and a single daemon thread flushes at most once per flush interval.
_cache: Optional[dict] = None
_dirty = False
_FLUSH_INTERVAL = 2.0
_flusher_started = False


def _load_from_disk() -> dict:
    try:
        with open(_FEATURE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {}
    except Exception as e:
        _sf_log.warn(f"unexpected load error: {e}")
        return {}


def _load() -> dict:
    """Return the in-memory features DB (loads from disk on first access)."""
    global _cache
    if _cache is None:
        _cache = _load_from_disk()
    return _cache


def _save(data) -> None:
    try:
        from core.atomic_write import atomic_write_json
        atomic_write_json(_FEATURE_FILE, data, indent=2, ensure_ascii=False)
    except Exception as e:
        _sf_log.warn(f"save failed: {e}")


def _flush_if_dirty() -> None:
    """Write the cache to disk once if dirty (called by the flusher thread)."""
    global _dirty
    with _lock:
        if not _dirty:
            return
        # Values are flat dicts of scalars — shallow per-entry copy is enough
        # to serialize a consistent snapshot without holding the lock.
        snapshot = {k: (dict(v) if isinstance(v, dict) else v)
                    for k, v in _load().items()}
        _dirty = False
    _save(snapshot)


def _flusher_loop() -> None:
    while True:
        time.sleep(_FLUSH_INTERVAL)
        try:
            _flush_if_dirty()
        except Exception as e:
            _sf_log.warn(f"features flush failed: {e}")


def _ensure_flusher() -> None:
    global _flusher_started
    if _flusher_started:
        return
    with _lock:
        if _flusher_started:
            return
        _flusher_started = True
    threading.Thread(target=_flusher_loop, daemon=True,
                     name="smtc-features-flush").start()


def _mark_dirty() -> None:
    """Flag the cache for the next debounced flush (≤ _FLUSH_INTERVAL later)."""
    global _dirty
    _ensure_flusher()
    _dirty = True


def _key(song, artist):
    if not song:
        return None
    return f"{artist.strip().lower()} - {song.strip().lower()}"


def _normalize_key(k):
    return k.strip().lower() if k else ""


def get_smtc_feature(song, artist):
    k = _key(song, artist)
    if not k:
        return None
    data = _load()
    for saved_key, feat in data.items():
        if _normalize_key(saved_key) == k:
            if feat.get("confirm_count", 0) >= 1 and feat.get("cycle_len", 0) > 5:
                return feat
    return None


def record_cycle_boundary(song, artist, cycle_len, progress_pos):
    k = _key(song, artist)
    if not k or cycle_len <= 0:
        return
    now = time.time()
    with _lock:
        data = _load()
        feat = data.get(k)
        if feat:
            feat["cycle_len"] = cycle_len
            feat["last_seen"] = now
            feat["confirm_count"] = feat.get("confirm_count", 0) + 1
            if progress_pos > feat.get("max_seen", 0):
                feat["max_seen"] = progress_pos
            if progress_pos > 420:
                feat["is_long"] = True
        else:
            data[k] = {"cycle_len": cycle_len, "max_seen": max(progress_pos, 0.0),
                       "is_long": progress_pos > 420, "first_seen": now,
                       "last_seen": now, "confirm_count": 1}
        _mark_dirty()


def record_max_position(song, artist, position):
    """Record max observed SMTC position for a song.

    ★ Validated: if a confirmed_dur exists, reject positions that are
    wildly inconsistent (>2x or <0.3x) — these are stale-SMTC artifacts
    from a different song.
    """
    k = _key(song, artist)
    if not k or position <= 0:
        return
    now = time.time()
    with _lock:
        data = _load()
        feat = data.get(k)

        # ★ Validation gate: if we have a confirmed duration, reject
        # positions that can't belong to this song.
        if feat and feat.get("confirmed_dur", 0) >= 60:
            cd = feat["confirmed_dur"]
            # position > 2x confirmed → likely stale data from a longer song
            if position > cd * 2.0:
                return
            # position > 420 but confirmed_dur < 420 → short song getting
            # long-song positions → pollution
            if position > 420 and cd < 420:
                return

        if feat:
            if position > feat.get("max_seen", 0):
                feat["max_seen"] = position
                feat["last_seen"] = now
            if position > 420:
                feat["is_long"] = True
        elif position > 120:
            data[k] = {"cycle_len": 0.0, "max_seen": position, "is_long": position > 420,
                       "first_seen": now, "last_seen": now, "confirm_count": 0}
        _mark_dirty()


def record_confirmed_duration(song, artist, duration):
    """Record a confirmed duration from SMTC media_props metadata.

    This is more reliable than max_seen because it comes from the media
    file's actual duration, not from cumulative position tracking.
    Only records if duration is reasonable (60-1200s).
    """
    k = _key(song, artist)
    if not k or duration < 60 or duration > 1200:
        return
    now = time.time()
    with _lock:
        data = _load()
        feat = data.get(k)
        if feat:
            prev = feat.get("confirmed_dur", 0)
            # Only update if the new value is reasonable and consistent
            if prev <= 0 or abs(duration - prev) < max(60, prev * 0.3):
                feat["confirmed_dur"] = duration
                feat["confirmed_at"] = now
                if duration > 420:
                    feat["is_long"] = True
                if duration > feat.get("max_seen", 0):
                    feat["max_seen"] = duration
        else:
            data[k] = {"cycle_len": 0.0, "max_seen": duration,
                       "confirmed_dur": duration, "confirmed_at": now,
                       "is_long": duration > 420, "first_seen": now,
                       "last_seen": now, "confirm_count": 0}
        _mark_dirty()


def get_known_cycle_len(song, artist):
    feat = get_smtc_feature(song, artist)
    return feat.get("cycle_len", 0.0) if feat else 0.0


def get_known_duration(song, artist):
    """Get known duration for a song.

    Prefers confirmed_dur (from SMTC media_props metadata) over
    max_seen (from cumulative position tracking).  Confirmed durations
    are validated against media file metadata and aren't vulnerable
    to stale-SMTC pollution.
    """
    # Direct lookup (not through get_smtc_feature which filters by cycle)
    k = _key(song, artist)
    if not k:
        return 0.0
    data = _load()
    feat = data.get(k)
    if not feat:
        return 0.0
    cd = feat.get("confirmed_dur", 0)
    if cd >= 60:
        return cd
    return feat.get("max_seen", 0.0)


def is_known_long_song(song, artist):
    feat = get_smtc_feature(song, artist)
    return bool(feat and feat.get("cycle_len", 0) > 5)


# ---- Per-player timeline cycle cap ----
# QQ Music caps its SMTC timeline at ~429s; longer songs wrap.  Learning the
# ceiling per player replaces the hardcoded 420s threshold so a player update
# that widens/narrows the cap is adapted to automatically.

_PLAYER_CAP_PREFIX = "__player_cycle__"


def record_player_cycle_cap(player: str, end_time: float) -> None:
    """Record the max SMTC timeline end_time observed for a player."""
    if not player or end_time <= 0:
        return
    k = _PLAYER_CAP_PREFIX + player.strip().lower()
    now = time.time()
    with _lock:
        data = _load()
        feat = data.get(k)
        if feat is None or end_time > feat.get("cap", 0):
            data[k] = {"cap": float(end_time), "last_seen": now}
            _mark_dirty()


def get_player_cycle_cap(player: str) -> float:
    """Return the learned timeline ceiling for a player (0.0 if unknown)."""
    if not player:
        return 0.0
    data = _load()
    feat = data.get(_PLAYER_CAP_PREFIX + player.strip().lower())
    return feat.get("cap", 0.0) if feat else 0.0
