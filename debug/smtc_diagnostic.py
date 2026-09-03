"""SMTC Diagnostic — standalone tool to analyze Windows SMTC data flow.

Captures raw SMTC session data to understand:
1. How Windows SMTC integration works at the protocol level
2. QQ Music's specific data characteristics vs browsers/other players
3. Session lifecycle (appear/disappear/change events)
4. Timeline and media properties update patterns

Usage: python debug/smtc_diagnostic.py [--duration 60]
"""
import sys, os, time, json, ctypes, threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUTPUT_DIR = os.path.join(os.environ.get("TEMP", "."), "smtc_diag")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line)
    with open(os.path.join(OUTPUT_DIR, "smtc_diag.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def dump_session(s, label: str = "") -> dict:
    """Extract all available data from a SMTC session."""
    info = {"label": label}
    try:
        info["source_app_user_model_id"] = s.source_app_user_model_id or ""
    except Exception as e:
        info["source_app_user_model_id"] = f"<error: {e}>"

    # Playback info
    try:
        pi = s.get_playback_info()
        if pi:
            info["playback_status"] = str(pi.playback_status.name) if pi.playback_status else "None"
            info["playback_type"] = str(pi.playback_type.name) if pi.playback_type else "None"
            info["is_shuffle_active"] = str(pi.is_shuffle_active) if hasattr(pi, 'is_shuffle_active') else "N/A"
            info["is_repeat_active"] = str(pi.is_repeat_active) if hasattr(pi, 'is_repeat_active') else "N/A"
            info["playback_rate"] = str(pi.playback_rate) if hasattr(pi, 'playback_rate') else "N/A"
            # All available attributes
            info["playback_attrs"] = [a for a in dir(pi) if not a.startswith('_')]
    except Exception as e:
        info["playback_info"] = f"<error: {e}>"

    # Timeline properties
    try:
        tp = s.get_timeline_properties()
        if tp:
            info["timeline_position"] = tp.position.total_seconds() if tp.position else 0.0
            info["timeline_end_time"] = tp.end_time.total_seconds() if tp.end_time else 0.0
            info["timeline_min_seek"] = tp.min_seek_time.total_seconds() if hasattr(tp, 'min_seek_time') and tp.min_seek_time else 0.0
            info["timeline_max_seek"] = tp.max_seek_time.total_seconds() if hasattr(tp, 'max_seek_time') and tp.max_seek_time else 0.0
            info["timeline_attrs"] = [a for a in dir(tp) if not a.startswith('_')]
    except Exception as e:
        info["timeline"] = f"<error: {e}>"

    # Media properties
    try:
        mp = s.try_get_media_properties_async().get()
        if mp:
            info["media_title"] = str(mp.title or "")
            info["media_artist"] = str(mp.artist or "")
            info["media_album_title"] = str(mp.album_title or "")
            info["media_album_artist"] = str(mp.album_artist or "") if hasattr(mp, 'album_artist') else "N/A"
            info["media_track_number"] = str(mp.track_number) if hasattr(mp, 'track_number') else "N/A"
            info["media_genre"] = str(mp.genres) if hasattr(mp, 'genres') else "N/A"
            info["media_subtitle"] = str(mp.subtitle) if hasattr(mp, 'subtitle') else "N/A"
            if hasattr(mp, 'duration') and mp.duration:
                info["media_duration"] = mp.duration.total_seconds()
            info["media_has_thumbnail"] = str(mp.thumbnail is not None) if hasattr(mp, 'thumbnail') else "N/A"
            info["media_attrs"] = [a for a in dir(mp) if not a.startswith('_')]
    except Exception as e:
        info["media_props"] = f"<error: {e}>"

    return info


def try_enumerate_sessions(mgr) -> list:
    """Try multiple approaches to enumerate SMTC sessions."""
    results = []

    # Approach 1: direct list()
    try:
        sessions = list(mgr.get_sessions())
        results.append(("direct list()", len(sessions), True))
    except Exception as e:
        results.append(("direct list()", str(e)[:80], False))

    # Approach 2: iterate without list()
    try:
        count = 0
        for s in mgr.get_sessions():
            count += 1
            if count > 20:
                break
        results.append(("for-in loop", count, True))
    except Exception as e:
        results.append(("for-in loop", str(e)[:80], False))

    # Approach 3: try to access as sequence
    try:
        ss = mgr.get_sessions()
        if hasattr(ss, '__len__'):
            results.append(("__len__", len(ss), True))
        if hasattr(ss, '__getitem__'):
            s0 = ss[0]
            results.append(("__getitem__[0]", s0.source_app_user_model_id, True))
    except Exception as e:
        results.append(("sequence access", str(e)[:80], False))

    # Approach 4: check available attributes
    try:
        ss = mgr.get_sessions()
        attrs = [a for a in dir(ss) if not a.startswith('_')]
        results.append(("get_sessions attrs", attrs, True))
    except Exception as e:
        results.append(("get_sessions attrs", str(e)[:80], False))

    return results


def main():
    duration = 60
    for a in sys.argv[1:]:
        if a.startswith("--duration="):
            duration = int(a.split("=")[1])

    log("=== SMTC Diagnostic Tool ===")
    log(f"Output: {OUTPUT_DIR}")
    log(f"Duration: {duration}s")

    # Initialize COM
    ctypes.windll.ole32.CoInitializeEx(0, 0x0)
    log("COM initialized (COINIT_MULTITHREADED)")

    # Get SMTC manager
    from winrt.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as SM

    mgr = SM.request_async().get()
    log(f"SMTC Manager obtained: {mgr}")

    # ---- PHASE 1: Session enumeration approaches ----
    log("\n--- PHASE 1: Session Enumeration Approaches ---")
    enum_results = try_enumerate_sessions(mgr)
    for name, result, ok in enum_results:
        status = "[OK]" if ok else "[FAIL]"
        log(f"  {status} {name}: {result}")

    # ---- PHASE 2: Current session deep dive ----
    log("\n--- PHASE 2: Current Session Deep Dive ---")
    try:
        cur = mgr.get_current_session()
        if cur:
            data = dump_session(cur, "current")
            log(f"  Current session: {json.dumps(data, indent=2, ensure_ascii=False, default=str)}")
        else:
            log("  No current session")
    except Exception as e:
        log(f"  get_current_session error: {e}")

    # ---- PHASE 3: Session changed event monitoring ----
    log("\n--- PHASE 3: Session Change Monitor (ongoing) ---")
    session_log = []
    session_lock = threading.Lock()

    def on_session_changed(sender, args):
        with session_lock:
            ts = time.time()
            try:
                cur = mgr.get_current_session()
                aid = cur.source_app_user_model_id if cur else "None"
            except Exception:
                aid = "<error>"
            session_log.append((ts, aid))
            log(f"  SESSION CHANGED → current: {aid}")

    try:
        token = mgr.add_current_session_changed(
            lambda s, a: on_session_changed(s, a))
        log("  Subscribed to current_session_changed event")
    except Exception as e:
        token = None
        log(f"  Could not subscribe to session_changed: {e}")

    # ---- PHASE 4: Continuous monitoring ----
    log(f"\n--- PHASE 4: Continuous Monitor ({duration}s) ---")
    start = time.time()
    last_state = None
    poll_count = 0

    while time.time() - start < duration:
        time.sleep(0.5)
        poll_count += 1

        try:
            cur = mgr.get_current_session()
            if cur:
                aid = cur.source_app_user_model_id or ""
                tp = cur.get_timeline_properties()
                pos = tp.position.total_seconds() if tp and tp.position else 0.0
                dur = tp.end_time.total_seconds() if tp and tp.end_time else 0.0
                pi = cur.get_playback_info()
                pst = str(pi.playback_status.name) if pi and pi.playback_status else "?"

                state_key = f"{aid}|{pst}|{int(dur)}"
                if state_key != last_state:
                    last_state = state_key
                    # Get title on state change
                    try:
                        mp = cur.try_get_media_properties_async().get()
                        title = str(mp.title or "")[:40] if mp else "?"
                    except Exception:
                        title = "?"
                    log(f"  [{aid.split('.')[-1]}] {pst} pos={pos:.1f}s dur={dur:.0f}s title={title!r}")

            else:
                if last_state != "NONE":
                    last_state = "NONE"
                    log(f"  [no session]")
        except Exception as e:
            log(f"  poll error: {e}")

    # ---- SUMMARY ----
    log(f"\n=== SUMMARY ===")
    log(f"  Polls: {poll_count}")
    with session_lock:
        log(f"  Session changes: {len(session_log)}")
        for ts, aid in session_log:
            log(f"    {datetime.fromtimestamp(ts).strftime('%H:%M:%S')} → {aid}")

    # Save full session change log
    with open(os.path.join(OUTPUT_DIR, "session_changes.json"), "w") as f:
        json.dump(session_log, f, indent=2, default=str)

    # Cleanup
    if token:
        try:
            mgr.remove_current_session_changed(token)
        except Exception:
            pass

    log("Diagnostic complete.")


if __name__ == "__main__":
    main()
