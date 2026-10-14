"""
Crash-safe file writes for local knowledge files (stock_db.json, wiki, logs)
and pipeline artifacts (structured_data.json).

A plain ``open(path, "w")`` truncates first; if the process dies mid-write the
file is left empty or half-written. These helpers write to a temp file in the
same directory, fsync, then ``os.replace`` so readers only ever see the old or
the new complete file.
"""

from __future__ import annotations

import contextlib
import datetime
import glob
import json
import os
import shutil
import tempfile


def _target_mode(path: str) -> int:
    """Keep the existing file mode; new files get the usual 0644 minus umask (mkstemp uses 0600)."""
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask


def backup_file_daily(path: str, backup_dir: str | None = None, keep: int = 14, today: datetime.date | None = None) -> str | None:
    """
    Copy ``path`` to ``backup_dir/<name>.<YYYY-MM-DD><ext>`` once per day (first write of the day wins).

    Keeps the newest ``keep`` backups for that file. Returns the backup path, or None when
    there was nothing to back up / backup already exists / copy failed (never raises).
    """
    try:
        if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
            return None
        base_dir = os.path.dirname(os.path.abspath(path))
        backup_dir = backup_dir or os.path.join(base_dir, "backup")
        os.makedirs(backup_dir, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(path))
        day = (today or datetime.date.today()).isoformat()
        target = os.path.join(backup_dir, f"{stem}.{day}{ext}")
        if os.path.exists(target):
            return None
        shutil.copy2(path, target)
        existing = sorted(glob.glob(os.path.join(backup_dir, f"{stem}.????-??-??{ext}")))
        for old in existing[:-keep] if keep and len(existing) > keep else []:
            try:
                os.unlink(old)
            except OSError:
                pass
        return target
    except Exception as exc:  # backups must never break the daily run
        print(f"  [Backup] 無法備份 {os.path.basename(str(path))}: {exc}")
        return None


@contextlib.contextmanager
def atomic_write(path: str, mode: str = "w", encoding: str | None = "utf-8"):
    """
    Context manager yielding a file handle; the target is replaced only when the
    block finishes without an exception. A failure leaves the previous file intact.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        kwargs = {} if "b" in mode else {"encoding": encoding}
        with os.fdopen(fd, mode, **kwargs) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, _target_mode(path))
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def atomic_write_text(path: str, content: str, encoding: str = "utf-8") -> str:
    """Write ``content`` to ``path`` atomically (the one implementation: atomic_write)."""
    with atomic_write(path, encoding=encoding) as handle:
        handle.write(content)
    return path


def write_json_atomic(path: str, data, *, trailing_newline: bool = False, **dump_kwargs) -> str:
    """
    Serialize ``data`` first (a TypeError never creates a temp file or touches the
    target), then write it atomically. Defaults: ensure_ascii=False, indent=2.
    """
    dump_kwargs.setdefault("ensure_ascii", False)
    dump_kwargs.setdefault("indent", 2)
    payload = json.dumps(data, **dump_kwargs)
    return atomic_write_text(path, payload + ("\n" if trailing_newline else ""))


def atomic_write_json(path: str, data, *, indent: int = 2) -> str:
    """Backward-compatible name: write_json_atomic with a trailing newline (stock_db, wiki JSON)."""
    return write_json_atomic(path, data, indent=indent, trailing_newline=True)


def write_text_atomic(path: str, text: str) -> str:
    """Backward-compatible alias of atomic_write_text."""
    return atomic_write_text(path, text)
