"""Build & package script with auto-version stamping.

Usage:
    python build.py              # Full build: stamp → PyInstaller → Inno Setup
    python build.py --quick      # Quick rebuild: PyInstaller only (no version stamp)
    python build.py --stamp-only # Just update the version stamp

The version is stamped into version.py before PyInstaller runs,
ensuring every build has a unique, traceable build timestamp.
"""
import sys
import os
import subprocess
import shutil

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)


def stamp_version() -> str:
    """Stamp current timestamp into version.py."""
    from version import stamp_build
    new_ver = stamp_build()
    print(f"[build] Version stamped: {new_ver}")
    return new_ver


def clean_artifacts():
    """Remove previous build output."""
    for d in ("build", "dist"):
        path = os.path.join(PROJECT_DIR, d)
        if os.path.exists(path):
            shutil.rmtree(path)
            print(f"[build] Cleaned: {d}/")


def run_pyinstaller():
    """Run PyInstaller with the project spec file."""
    spec = os.path.join(PROJECT_DIR, "QQMusicOverlayNG.spec")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        spec,
        "--distpath", os.path.join(PROJECT_DIR, "dist"),
        "--workpath", os.path.join(PROJECT_DIR, "build"),
        "--noconfirm",
    ]
    print(f"[build] Running PyInstaller...")
    result = subprocess.run(cmd, cwd=PROJECT_DIR)
    if result.returncode != 0:
        print("[build] ERROR: PyInstaller failed")
        sys.exit(1)
    print("[build] PyInstaller OK")


def run_inno_setup():
    """Run Inno Setup 6 to create the installer."""
    iss = os.path.join(PROJECT_DIR, "setup.iss")
    iscc = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if not os.path.exists(iscc):
        iscc = r"C:\Program Files\Inno Setup 6\ISCC.exe"
    if not os.path.exists(iscc):
        print("[build] WARNING: Inno Setup not found, skipping installer")
        return

    print(f"[build] Running Inno Setup...")
    result = subprocess.run([iscc, iss], cwd=PROJECT_DIR)
    if result.returncode != 0:
        print("[build] ERROR: Inno Setup failed")
        sys.exit(1)
    print("[build] Inno Setup OK")


def create_backup():
    """Create a safe version backup using the versioning utility."""
    from core.versioning import backup_version, cleanup_artifacts
    from version import BUILD_VERSION
    tag = f"v{BUILD_VERSION.replace('.', '')}"
    try:
        dest = backup_version(tag)
        print(f"[build] Backup: {dest}")
    except ValueError:
        print(f"[build] Backup skipped: tag '{tag}' already exists")

    # Clean build artifacts from project (they get rebuilt each time)
    freed = cleanup_artifacts()
    if freed > 0:
        print(f"[build] Cleaned {freed / 1_048_576:.1f} MB artifacts")


def main():
    quick = "--quick" in sys.argv
    stamp_only = "--stamp-only" in sys.argv

    print("=" * 60)
    print("QQMusicOverlay NG — Build Script")
    print("=" * 60)

    if stamp_only:
        stamp_version()
        print("[build] Stamp-only done.")
        return

    if not quick:
        stamp_version()

    clean_artifacts()
    run_pyinstaller()
    run_inno_setup()

    if not quick:
        create_backup()

    # Final summary
    setup = os.path.join(PROJECT_DIR, "QQMusicOverlayNG_Setup.exe")
    if os.path.exists(setup):
        size_mb = os.path.getsize(setup) / 1_048_576
        print(f"\n[build] Done -- {setup} ({size_mb:.1f} MB)")
    else:
        exe = os.path.join(PROJECT_DIR, "dist", "QQMusicOverlayNG",
                          "QQMusicOverlayNG.exe")
        if os.path.exists(exe):
            print(f"\n[build] ✓ Done — {exe}")
        else:
            print("\n[build] ✗ Build may have failed — no output found")


if __name__ == "__main__":
    main()
