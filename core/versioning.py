"""Safe version backup and cleanup utilities.

Prevents the recursive-nesting disk-space explosion (June 2026 incident:
4 snapshots contained nested versions/ directories → ~70 GB wasted).

Usage:
    from core.versioning import backup_version, cleanup_artifacts

    # Create a safe source-only backup
    backup_version("v20260627-some-fix")

    # Clean up build artifacts
    cleanup_artifacts()
"""
import os
import re
import shutil
import datetime
from pathlib import Path
from typing import Optional

from core.logs import get_logger

_vlog = get_logger("versioning")

# ---- What gets INCLUDED in version backups (source code only) ----
SOURCE_EXTS: set[str] = {
    ".py", ".pyw",          # Python source
    ".json",                # Config
    ".iss",                 # Inno Setup installer script
    ".spec",                # PyInstaller spec
    ".md", ".txt",          # Documentation
    ".png", ".ico", ".svg", # Assets
    ".ttf", ".ttc", ".otf", # Fonts (in assets/)
    ".tcl",                 # Tcl theme files (in assets/)
}

# ---- What gets EXCLUDED from version backups ----
EXCLUDE_DIRS: set[str] = {
    "build",            # PyInstaller work files (can be 100+ MB)
    "dist",             # PyInstaller output (can be 100+ MB)
    "versions",         # Other version snapshots → RECURSIVE NESTING!
    ".cache",           # PyInstaller / cover cache
    "__pycache__",      # Python bytecode
    ".git",             # Git metadata
    ".claude",          # Claude session data
}

EXCLUDE_FILE_PATTERNS: list[str] = [
    r".*\.pyc$",           # Python bytecode
    r".*\.pyo$",           # Python optimized bytecode
    r".*\.log$",           # Log files
    r".*\.exe$",           # Compiled executables
    r".*\.pkg$",           # PyInstaller package archives
    r".*\.toc$",           # PyInstaller table of contents
    r".*\.manifest$",      # PyInstaller manifests
    r".*\.dll$",           # DLLs (redistributables)
    r".*\.pyd$",           # Python C extensions
    r".*\.zip$",           # Archives
    r"QQMusicOverlayNG_Setup\.exe$",  # Installer
]

_EXCLUDE_RE = re.compile("|".join(f"({p})" for p in EXCLUDE_FILE_PATTERNS))


def _get_project_root() -> Path:
    """Find the project root (parent of core/)."""
    this_file = Path(__file__).resolve()
    return this_file.parent.parent


def _should_include(file_path: Path, root: Path) -> bool:
    """Return True if file should be included in a source backup."""
    # Check extension
    if file_path.suffix.lower() not in SOURCE_EXTS:
        return False

    # Check file name patterns
    if _EXCLUDE_RE.match(file_path.name):
        return False

    # Check parent directories against EXCLUDE_DIRS
    rel = file_path.relative_to(root)
    for part in rel.parts[:-1]:  # all directory parts
        if part in EXCLUDE_DIRS:
            return False

    return True


def backup_version(tag: str, root: Optional[Path] = None) -> Path:
    """Create a safe source-only version snapshot.

    Only copies source files (.py, .json, .md, .png, etc.) — NEVER
    build artifacts, caches, or nested version directories.

    Args:
        tag: Version tag, e.g. "v20260627-cool-feature"
        root: Project root (auto-detected if None)

    Returns:
        Path to the created backup directory.

    Raises:
        ValueError: If the tag already exists (prevents overwrites).
    """
    if root is None:
        root = _get_project_root()

    dest = root / "versions" / tag
    if dest.exists():
        raise ValueError(
            f"Version '{tag}' already exists at {dest}. "
            f"Use a different tag or delete the existing backup first."
        )

    dest.mkdir(parents=True, exist_ok=False)
    file_count = 0

    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        if not _should_include(file_path, root):
            continue

        rel = file_path.relative_to(root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, target)
        file_count += 1

    _vlog.info(
        f"backup: {tag} → {file_count} files "
        f"(excluded: build dist versions .cache __pycache__)"
    )
    return dest


def list_backups(root: Optional[Path] = None) -> list[Path]:
    """List all existing version backups sorted by name."""
    if root is None:
        root = _get_project_root()
    versions_dir = root / "versions"
    if not versions_dir.exists():
        return []
    backups = sorted(
        [d for d in versions_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )
    return backups


def cleanup_artifacts(root: Optional[Path] = None, *,
                      remove_build: bool = True,
                      remove_dist: bool = True,
                      remove_cache: bool = False,
                      ) -> int:
    """Remove build/dist artifacts to reclaim disk space.

    Args:
        root: Project root (auto-detected if None)
        remove_build: Remove build/ directory
        remove_dist: Remove dist/ directory
        remove_cache: Remove .cache/ directory

    Returns:
        Total bytes freed.
    """
    if root is None:
        root = _get_project_root()

    to_remove: list[Path] = []
    if remove_build:
        to_remove.append(root / "build")
    if remove_dist:
        to_remove.append(root / "dist")
    if remove_cache:
        to_remove.append(root / ".cache")

    # Also clean __pycache__ directories
    pycache_dirs = list(root.rglob("__pycache__"))

    total_freed = 0

    for path in to_remove + pycache_dirs:
        if not path.exists():
            continue
        try:
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            total_freed += size
            _vlog.info(f"cleanup: removed {path.relative_to(root)} "
                       f"({size / 1_048_576:.1f} MB)")
        except Exception as e:
            _vlog.warn(f"cleanup: failed to remove {path}: {e}")

    return total_freed


def verify_backups(root: Optional[Path] = None) -> dict[str, list[str]]:
    """Check all backups for forbidden directories (recursive nesting).

    Returns:
        Dict of {backup_name: [list_of_violations]}. Empty violations = clean.
    """
    if root is None:
        root = _get_project_root()

    results: dict[str, list[str]] = {}
    for backup in list_backups(root):
        violations: list[str] = []
        for forbidden in EXCLUDE_DIRS:
            if (backup / forbidden).exists():
                violations.append(f"contains {forbidden}/ directory")
        results[backup.name] = violations

    if any(v for v in results.values()):
        bad = [f"  {k}: {', '.join(v)}" for k, v in results.items() if v]
        _vlog.warn(f"backup violations found:\n" + "\n".join(bad))
    else:
        _vlog.info(f"verify: all {len(results)} backups clean")

    return results


# ---- CLI entry point ----
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m core.versioning <backup|cleanup|verify|list> [tag]")
        print("  backup <tag>   Create a safe source-only backup")
        print("  cleanup        Remove build/ dist/ __pycache__/")
        print("  verify         Check all backups for nesting violations")
        print("  list           List all backups")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "backup":
        if len(sys.argv) < 3:
            print("ERROR: backup requires a tag name")
            sys.exit(1)
        tag = sys.argv[2]
        dest = backup_version(tag)
        print(f"Backup created: {dest}")
    elif cmd == "cleanup":
        freed = cleanup_artifacts()
        print(f"Freed {freed / 1_048_576:.1f} MB")
    elif cmd == "verify":
        results = verify_backups()
        bad = {k: v for k, v in results.items() if v}
        if bad:
            print(f"VIOLATIONS in {len(bad)} backups:")
            for name, violations in bad.items():
                print(f"  {name}: {', '.join(violations)}")
        else:
            print(f"All {len(results)} backups are clean.")
    elif cmd == "list":
        for b in list_backups():
            size = sum(f.stat().st_size for f in b.rglob("*") if f.is_file())
            print(f"  {b.name}: {size / 1_048_576:.1f} MB")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
