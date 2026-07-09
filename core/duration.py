"""Learned song duration persistence.

Records each song's maximum observed playback position.
New songs start with learned estimate, improving accuracy over time.

All writes are asynchronous — queued to a background daemon thread to
avoid blocking the render loop with disk I/O during song transitions.
"""
import json
import os
import time
import threading
import queue
from typing import Optional

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "QQMusicOverlayNG")
os.makedirs(DATA_DIR, exist_ok=True)
# ★ Migrate from old app-directory location to APPDATA
for _f, _dest in ((".learned_durations.json", ".learned_durations.json"),
                   (".wall_snapshots.json", ".wall_snapshots.json")):
    _old = os.path.join(APP_DIR, _f)
    _new = os.path.join(DATA_DIR, _dest)
    if os.path.exists(_old) and not os.path.exists(_new):
        import shutil
        shutil.copy2(_old, _new)
DURATION_FILE = os.path.join(DATA_DIR, ".learned_durations.json")
SNAPSHOT_FILE = os.path.join(DATA_DIR, ".wall_snapshots.json")

# ★ Background write queue — avoids render-loop stalls from json.dump() I/O.
# All _save() calls are deferred to this daemon thread.
_write_queue: queue.Queue = queue.Queue()


def _writer_thread():
    """Daemon: drain write queue and persist data to disk."""
    while True:
        try:
            path, data = _write_queue.get()
            if path is None:  # sentinel to stop
                break
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


_writer = threading.Thread(target=_writer_thread, daemon=True, name="duration-writer")
_writer.start()


def _make_key(song: str, artist: str) -> str:
    """Create a normalized key from song and artist."""
    return f"{song.strip().lower()}|||{artist.strip().lower()}"


def _load(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            # Decode explicitly with error replacement — Python 3.14's
            # json.load() may bypass TextIOWrapper's errors="replace"
            # when reading from a text file object, causing UnicodeDecodeError
            # to propagate past our except clause.
            text = raw.decode("utf-8", errors="replace")
            return json.loads(text)
        except (json.JSONDecodeError, IOError, UnicodeDecodeError):
            pass
        except Exception:
            # Corrupted file that can't be recovered — delete it
            try:
                os.remove(path)
            except Exception:
                pass
    return {}


def _save(path: str, data: dict) -> None:
    """Queue a write to disk — non-blocking, returns immediately."""
    _write_queue.put((path, data))


# ---- Learned Durations ----

def record_position(song: str, artist: str, position: float,
                    duration: float = 0.0) -> None:
    """Record playback position AND actual duration for this song.

    Priority: use `duration` if provided and reasonable (>120s).
    Otherwise fall back to `position` as a lower-bound estimate.
    Only updates if the new value is closer to a realistic duration.
    """
    if not song or position <= 0:
        return
    data = _load(DURATION_FILE)
    key = _make_key(song, artist)
    # ★ Prefer actual duration over position (position is where the
    # user stopped, not necessarily the full track length).
    best = duration if duration >= 120 else position
    prev = data.get(key, 0)
    if best > prev:
        data[key] = round(best, 1)
        _save(DURATION_FILE, data)


def get_learned(song: str, artist: str) -> float:
    """Get the learned duration for a song. Returns 0 if unknown."""
    if not song:
        return 0.0
    data = _load(DURATION_FILE)
    key = _make_key(song, artist)
    return data.get(key, 0.0)


def clear_learned(song: str, artist: str) -> bool:
    """Remove a polluted learned duration entry. Returns True if deleted."""
    if not song:
        return False
    data = _load(DURATION_FILE)
    key = _make_key(song, artist)
    if key in data:
        del data[key]
        _save(DURATION_FILE, data)
        return True
    return False


def clean_suspicious_durations() -> int:
    """Remove learned duration entries that look like pollution artifacts.

    Pollution signature: value near (API_duration * 1.15) or near (SMTC cycle * 1.15)
    — these come from the runtime correction formula when SMTC was stuck.

    Returns count of removed entries.
    """
    data = _load(DURATION_FILE)
    removed = 0
    suspicious = []
    for key, val in list(data.items()):
        v = float(val)
        matched = False
        # Signature 1: runtime correction produces values near cycle*1.15
        for cycle in (177, 225, 270, 300, 376, 429):
            if abs(v - cycle * 1.15) < 8:
                suspicious.append((key, v, f"cycle*1.15≈{cycle*1.15:.0f}"))
                matched = True
                break
        if matched:
            continue
        # Signature 2: extremely high values (>600s) unlikely real
        if v > 600:
            suspicious.append((key, v, ">600s"))
            continue
        # Signature 3: value near position*1.15 (generic extrapolation from
        # the runtime correction formula during SMTC freeze).
        # e.g., position=390 → 390*1.15=448.5 → learned=452 (close match)
        est_pos = v / 1.15
        if 100 < est_pos < 700 and abs(est_pos - round(est_pos)) < 0.3:
            suspicious.append((key, v, f"pos_extrap≈{est_pos:.0f}"))

    for key, val, reason in suspicious:
        del data[key]
        removed += 1
        try:
            from core.logs import get_logger
            get_logger("reader").info(
                f"DURATION_CLEAN: removed polluted entry [{key[:60]}]={val} "
                f"reason={reason}")
        except Exception:
            pass

    if removed > 0:
        _save(DURATION_FILE, data)

    return removed


# ---- Wall-clock Snapshots ----

def save_snapshot(song: str, artist: str, position: float) -> None:
    """Save wall-clock progress snapshot for pause recovery."""
    if not song or position <= 0:
        return
    data = _load(SNAPSHOT_FILE)
    key = _make_key(song, artist)
    data[key] = {
        "position": round(position, 1),
        "wall_time": time.time(),
    }
    _save(SNAPSHOT_FILE, data)


def load_snapshot(song: str, artist: str) -> Optional[float]:
    """Load a saved snapshot position for a song. Returns None if not found."""
    if not song:
        return None
    data = _load(SNAPSHOT_FILE)
    key = _make_key(song, artist)
    snap = data.get(key)
    if snap:
        return snap.get("position", 0.0)
    return None


def clear_snapshot(song: str, artist: str) -> None:
    """Remove snapshot for a song (called when song changes)."""
    data = _load(SNAPSHOT_FILE)
    key = _make_key(song, artist)
    if key in data:
        del data[key]
        _save(SNAPSHOT_FILE, data)
