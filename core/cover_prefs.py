"""Cover preference memory — per-song cover source mapping.

User can manually select a cover and associate it with the current song.
On subsequent plays of the same song, the preferred source is automatically
used (falls back to auto-matching if the source is unavailable).

Storage: .cover_preferences.json (alongside overlay_config.json)
"""
import json
import os
import threading
from core.logs import get_logger

_cp_log = get_logger("cover_prefs")

_PREFS_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), ".cover_preferences.json")
_lock = threading.Lock()


def _load() -> dict:
    """Load preferences dict. Returns {key: {name, saved_at}}."""
    try:
        with open(_PREFS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {}
    except Exception as e:
        _cp_log.warn(f"unexpected load error: {e}")
        return {}


def _save(data: dict) -> None:
    try:
        with open(_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _cp_log.warn(f"save failed: {e}")


def _make_key(song: str, artist: str) -> str:
    """Normalized song+artist key for lookup."""
    s = (song or "").strip().lower()
    a = (artist or "").strip().lower()
    return f"{a} | {s}" if a else s


def get_preferred_source(song: str, artist: str) -> str | None:
    """Return preferred cover source name for this song, or None."""
    if not song:
        return None
    key = _make_key(song, artist)
    data = _load()
    entry = data.get(key)
    if entry:
        return entry.get("name", "")
    # Try partial key (artist only, or song-only fallback)
    for k, v in data.items():
        if k == key or k.endswith(f" | {key}") or k.startswith(f"{key} | "):
            return v.get("name", "")
    return None


def save_preference(song: str, artist: str, source_name: str) -> None:
    """Save a preferred cover source for this song."""
    if not song or not source_name:
        return
    key = _make_key(song, artist)
    data = _load()
    import time
    data[key] = {
        "name": source_name,
        "saved_at": time.time(),
        "song": song.strip(),
        "artist": artist.strip() if artist else "",
    }
    with _lock:
        _save(data)


def remove_preference(song: str, artist: str) -> bool:
    """Remove a saved preference. Returns True if removed."""
    key = _make_key(song, artist)
    data = _load()
    if key in data:
        del data[key]
        with _lock:
            _save(data)
        return True
    return False


def list_preferences() -> list[dict]:
    """List all saved preferences (for settings UI display)."""
    data = _load()
    result = []
    for k, v in data.items():
        result.append({
            "key": k,
            "song": v.get("song", ""),
            "artist": v.get("artist", ""),
            "source_name": v.get("name", ""),
            "saved_at": v.get("saved_at", 0),
        })
    result.sort(key=lambda x: x.get("saved_at", 0), reverse=True)
    return result
