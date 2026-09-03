"""Atomic JSON write helper — prevents config corruption on crash.

Writes to a temp file then os.replace() (atomic on Windows NTFS),
so readers never see a half-written file. Drop-in replacement for:

    with open(path, "w") as f:
        json.dump(data, f, ...)

Usage:
    from core.atomic_write import atomic_write_json
    atomic_write_json(path, data, indent=2, ensure_ascii=False)
"""
import json
import os
import tempfile


def atomic_write_json(path: str, data, **json_kwargs) -> None:
    """Write `data` as JSON to `path`, atomically.

    json_kwargs are forwarded to json.dump() (e.g. indent, ensure_ascii, default).
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **json_kwargs)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
