"""
Cover pipeline stress test for QQMusicOverlay NG.

Monitors overlay logs while music plays, counting:
- Song changes detected
- Default cover fallback occurrences
- COVER diagnostic events

Usage:
    python cover_stress_test.py [--duration 300]

Exit codes:
    0 = no default cover fallbacks
    1 = default covers detected
"""
import sys
import os
import time
import tempfile
import argparse
from collections import defaultdict


def tail_log(log_path: str, start_pos: int = 0) -> tuple[str, int]:
    """Read new content from log file, return (content, new_position)."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start_pos)
            content = f.read()
            new_pos = f.tell()
        return content, new_pos
    except FileNotFoundError:
        return "", start_pos


def find_overlay_logs() -> dict[str, str]:
    """Find overlay log files, return {key: path}."""
    tmp = tempfile.gettempdir()
    logs = {}
    for f in os.listdir(tmp):
        if f.startswith("qqoverlay_") and f.endswith(".log"):
            key = f.replace("qqoverlay_", "").replace(".log", "")
            logs[key] = os.path.join(tmp, f)
    return logs


def main():
    parser = argparse.ArgumentParser(
        description="QQMusicOverlay NG Cover Pipeline Stress Test")
    parser.add_argument("--duration", type=int, default=300,
                        help="Monitor duration in seconds")
    args = parser.parse_args()

    print(f"Monitoring cover pipeline for {args.duration}s...")
    print("Play music and switch songs during this time.\n")

    logs = find_overlay_logs()
    reader_log = logs.get("reader")
    if not reader_log:
        print("ERROR: reader log not found — is the overlay running?")
        sys.exit(2)

    # Stats
    stats = defaultdict(int)
    positions = {}

    deadline = time.time() + args.duration
    while time.time() < deadline:
        for key, path in logs.items():
            content, pos = tail_log(path, positions.get(key, 0))
            positions[key] = pos
            if not content:
                continue
            for line in content.splitlines():
                if "COVER" in line:
                    stats["cover_diag"] += 1
                    if "DEFAULT" in line or "default" in line:
                        stats["default_cover"] += 1
                if "song_change" in line.lower() or "song changed" in line.lower():
                    stats["song_changes"] += 1
                    # Print song change for tracking
                    short = line.strip()[:120]
                    print(f"  [{time.strftime('%H:%M:%S')}] {short}")
        time.sleep(1.0)

    print(f"\n{'='*50}")
    print(f"Results ({args.duration}s):")
    print(f"  Song changes detected:  {stats['song_changes']}")
    print(f"  COVER diagnostics:      {stats['cover_diag']}")
    print(f"  Default cover fallbacks: {stats['default_cover']}")

    if stats["default_cover"] > 0:
        rate = stats["default_cover"] / max(stats["song_changes"], 1) * 100
        print(f"\n  [WARN] Default cover rate: {rate:.0f}%")
        if rate > 5:
            print("  -> Exceeds 5% threshold - investigate reader.py COVER logs")
        sys.exit(1)
    else:
        print("\n  [OK] No default cover fallbacks detected")
        sys.exit(0)


if __name__ == "__main__":
    main()
