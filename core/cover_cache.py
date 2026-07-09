"""Cover Cache — persistent local storage for album art.

Every cover discovered by _gather_cover_candidates is saved to disk.
A settings tab lets users browse, select, and export cached covers.
Oldest entries are evicted when the cache exceeds max_count (default 50).
"""
import os, time, threading, hashlib
from typing import Optional

try:
    from PIL import Image
except ImportError:
    Image = None

_CACHE_DIR: Optional[str] = None
_MAX_COVERS: int = 100
_LOCK = threading.Lock()
_INDEX: list[dict] = []  # [{path, song, artist, timestamp, size}]


def _ensure_dir() -> str:
    global _CACHE_DIR
    if _CACHE_DIR is not None:
        return _CACHE_DIR
    # Use project-local .cache/covers/
    _proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _CACHE_DIR = os.path.join(_proj, ".cache", "covers")
    os.makedirs(_CACHE_DIR, exist_ok=True)
    _load_index()
    return _CACHE_DIR


def _load_index() -> None:
    """Load existing cover entries from disk."""
    global _INDEX
    _dir = _CACHE_DIR
    if _dir is None:
        return
    entries = []
    try:
        for fname in os.listdir(_dir):
            if not fname.endswith((".jpg", ".png")):
                continue
            fpath = os.path.join(_dir, fname)
            try:
                stat = os.stat(fpath)
                # Parse filename: hash_song_artist.jpg
                parts = fname.rsplit(".", 1)[0].split("_", 2)
                song = parts[1] if len(parts) > 1 else ""
                artist = parts[2] if len(parts) > 2 else ""
            except Exception:
                song, artist = "", ""
            entries.append({
                "path": fpath,
                "filename": fname,
                "song": song,
                "artist": artist,
                "timestamp": stat.st_mtime,
                "size": stat.st_size,
            })
    except OSError:
        pass
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    _INDEX = entries


def set_max_covers(n: int) -> None:
    global _MAX_COVERS
    _MAX_COVERS = max(10, min(500, n))


def get_covers() -> list[dict]:
    """Return list of cached cover entries, newest first."""
    with _LOCK:
        return list(_INDEX)


def save_cover(img, song: str = "", artist: str = "") -> Optional[str]:
    """Save a PIL Image to the cover cache. Returns path or None."""
    if img is None or Image is None:
        return None
    _dir = _ensure_dir()
    try:
        # Safe filename from song+artist
        safe_song = "".join(c for c in (song or "unknown") if c.isalnum() or c in "._- ()（）")[:40]
        safe_artist = "".join(c for c in (artist or "unknown") if c.isalnum() or c in "._- ()（）")[:40]
        h = hashlib.md5(img.tobytes()[:4096]).hexdigest()[:8]
        fname = f"{h}_{safe_song}_{safe_artist}.jpg"
        fpath = os.path.join(_dir, fname)
        # Don't overwrite existing
        if os.path.exists(fpath):
            return fpath
        # Save as JPEG
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(fpath, "JPEG", quality=85)
        with _LOCK:
            _INDEX.insert(0, {
                "path": fpath, "filename": fname,
                "song": song, "artist": artist,
                "timestamp": time.time(),
                "size": os.path.getsize(fpath),
            })
        _evict_if_needed()
        return fpath
    except Exception:
        return None


def _evict_if_needed() -> None:
    """Remove oldest entries if cache exceeds _MAX_COVERS."""
    with _LOCK:
        while len(_INDEX) > _MAX_COVERS:
            oldest = _INDEX.pop()
            try:
                os.remove(oldest["path"])
            except OSError:
                pass


def clear_cache() -> int:
    """Delete all cached covers. Returns count removed."""
    _dir = _ensure_dir()
    removed = 0
    with _LOCK:
        for entry in _INDEX:
            try:
                os.remove(entry["path"])
                removed += 1
            except OSError:
                pass
        _INDEX.clear()
    return removed


def load_cover(path: str) -> Optional[Image.Image]:
    """Load a cached cover image from disk."""
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None
