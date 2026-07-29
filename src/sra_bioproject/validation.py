"""Destination and downloaded-file validation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

from .models import RunRecord


def md5sum(path: Path, block_size: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.md5()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_download(path: Path, record: RunRecord) -> bool:
    if not path.is_file():
        return False
    if record.sra_size_bytes and path.stat().st_size != record.sra_size_bytes:
        return False
    if record.md5 and md5sum(path) != record.md5.lower():
        return False
    return True


def validate_destination(path: Path, platform: str | None = None) -> Path:
    requested = path.expanduser()
    current_platform = sys.platform if platform is None else platform
    parts = requested.parts
    if (
        current_platform == "darwin"
        and len(parts) >= 3
        and parts[0] == "/"
        and parts[1] == "Volumes"
    ):
        volume_root = Path("/Volumes") / parts[2]
        if not volume_root.is_dir():
            raise FileNotFoundError(
                f"Destination volume is not mounted: {volume_root}. "
                "Check the spelling with: ls -la /Volumes"
            )
        if not os.access(volume_root, os.W_OK):
            raise PermissionError(f"Destination volume is not writable: {volume_root}")
    return requested.resolve()
