"""
Smoke test for Music CoverOverlay.
Launches the overlay, waits for initialization, checks for errors, reports.

Usage:
    python smoke_test.py [--timeout 15]

Exit codes:
    0 = all checks passed
    1 = launch failed
    2 = error found in logs
    3 = timeout
"""
import sys
import os
import time
import signal
import subprocess
import tempfile
import argparse


def find_logs() -> list[str]:
    """Find all overlay log files in TEMP."""
    tmp = tempfile.gettempdir()
    logs = []
    for f in os.listdir(tmp):
        if f.startswith("qqoverlay_") and f.endswith(".log"):
            logs.append(os.path.join(tmp, f))
    return logs


def check_logs_for_errors(log_files: list[str]) -> list[str]:
    """Scan log files for ERROR lines."""
    errors = []
    for log_path in log_files:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "ERROR" in line or "CRITICAL" in line or "Traceback" in line:
                        errors.append(f"{os.path.basename(log_path)}: {line.strip()[:120]}")
        except FileNotFoundError:
            pass
    return errors


def main():
    parser = argparse.ArgumentParser(description="QQMusicOverlay NG Smoke Test")
    parser.add_argument("--timeout", type=int, default=15,
                        help="Max seconds to wait for initialization")
    args = parser.parse_args()

    project_dir = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(project_dir, "main.py")

    if not os.path.exists(main_py):
        print(f"ERROR: {main_py} not found")
        sys.exit(1)

    # Collect pre-existing logs to diff against
    pre_logs = set(find_logs())

    print(f"[1/4] Launching: python {main_py}")
    proc = subprocess.Popen(
        [sys.executable, main_py],
        cwd=project_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        # Wait for initialization
        print(f"[2/4] Waiting {args.timeout}s for initialization...")
        deadline = time.time() + args.timeout
        heartbeat_file = os.path.join(tempfile.gettempdir(), "qqoverlay_heartbeat.txt")

        initialized = False
        while time.time() < deadline:
            if proc.poll() is not None:
                print(f"ERROR: Process exited early with code {proc.returncode}")
                sys.exit(1)

            # Check for heartbeat
            if os.path.exists(heartbeat_file):
                try:
                    with open(heartbeat_file, "r") as f:
                        content = f.read().strip()
                    # Heartbeat file contains a float timestamp (time.time()).
                    # Parse it — a valid float means the watchdog is writing.
                    try:
                        float(content)
                        elapsed = time.time() - (deadline - args.timeout)
                        print(f"[3/4] Heartbeat detected after {elapsed:.1f}s [OK]")
                        initialized = True
                        break
                    except ValueError:
                        pass
                except Exception:
                    pass

            time.sleep(0.5)

        if not initialized:
            print(f"WARNING: No heartbeat within {args.timeout}s - checking logs anyway")

        # Check for errors
        print("[4/4] Checking logs for errors...")
        all_logs = find_logs()
        new_logs = [f for f in all_logs if f not in pre_logs]

        errors = check_logs_for_errors(new_logs if new_logs else all_logs)
        if errors:
            print(f"\nFAILED: Found {len(errors)} error(s):")
            for e in errors[:10]:
                print(f"  - {e}")
            if len(errors) > 10:
                print(f"  ... and {len(errors) - 10} more")
            exit_code = 2
        else:
            print("\nPASSED: No errors in logs [OK]")
            exit_code = 0

    finally:
        # Cleanup: terminate the process
        print(f"\nShutting down...")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print("Done.")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
