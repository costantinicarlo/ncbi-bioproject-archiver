"""Destination and downloaded-file validation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys

from .models import RunRecord

RUN_ACCESSION_RE = re.compile(r"^(SRR|ERR|DRR)\d+$")
MD5_RE = re.compile(r"^[0-9a-f]{32}$")


def md5sum(path: Path, block_size: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.md5()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def validate_run_accession(accession: str) -> str:
    value = accession.strip().upper()
    if not RUN_ACCESSION_RE.fullmatch(value):
        raise ValueError(f"Invalid run accession: {accession!r}")
    return value


def validate_md5(md5: str, accession: str) -> str:
    value = md5.strip().lower()
    if not MD5_RE.fullmatch(value):
        raise ValueError(f"{accession}: md5 must be exactly 32 hexadecimal characters")
    return value


def ensure_path_within_directory(base_dir: Path, path: Path) -> Path:
    root = base_dir.resolve()
    candidate = path.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes destination directory: {path}") from exc
    return candidate


def run_accession_path(base_dir: Path, accession: str, suffix: str = "") -> Path:
    safe_accession = validate_run_accession(accession)
    return ensure_path_within_directory(base_dir, base_dir / f"{safe_accession}{suffix}")


def verify_download(path: Path, record: RunRecord) -> bool:
    if not path.is_file():
        return False
    if record.sra_size_bytes <= 0:
        return False
    if not MD5_RE.fullmatch(record.md5.lower()):
        return False
    if path.stat().st_size != record.sra_size_bytes:
        return False
    if md5sum(path) != record.md5.lower():
        return False
    return True


def validate_destination(path: Path, platform: str | None = None) -> Path:
    requested = path.expanduser()
    resolved = requested.resolve()
    current_platform = sys.platform if platform is None else platform
    parts = resolved.parts
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
        if not volume_root.is_mount():
            raise FileNotFoundError(
                f"Destination path is under /Volumes but not on a mounted volume: {volume_root}"
            )
        if not os.access(volume_root, os.W_OK):
            raise PermissionError(f"Destination volume is not writable: {volume_root}")
    return resolved
