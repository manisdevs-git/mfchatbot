"""Replace a finished directory in one rename so a failure keeps the previous tree."""

from __future__ import annotations

import shutil
from pathlib import Path


def replace_directory(live: Path, incoming: Path) -> None:
    """Move `incoming` onto `live`. If the final rename fails, restore `live`."""
    live = Path(live)
    incoming = Path(incoming)
    if not incoming.is_dir():
        raise FileNotFoundError(f"replacement directory is missing: {incoming}")

    backup = live.with_name(live.name + ".bak")
    if backup.exists():
        shutil.rmtree(backup)

    live_existed = live.exists()
    if live_existed:
        live.rename(backup)
    try:
        incoming.rename(live)
    except Exception:
        if live_existed and backup.exists() and not live.exists():
            backup.rename(live)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
