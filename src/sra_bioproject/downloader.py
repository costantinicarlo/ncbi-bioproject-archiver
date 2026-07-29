"""Resumable, verified SRA download execution."""

from __future__ import annotations

import concurrent.futures
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Callable, Iterable

from .models import RunRecord
from .validation import verify_download

LOGGER = logging.getLogger(__name__)


def human_bytes(byte_count: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    value = float(byte_count)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PiB"


def check_command(name: str, required: bool = True) -> str | None:
    path = shutil.which(name)
    if required and path is None:
        raise RuntimeError(f"Required command not found in PATH: {name}")
    return path


def download_one(
    record: RunRecord,
    sra_dir: Path,
    curl_path: str,
    *,
    run_command: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    timestamp: Callable[[], float] = time.time,
) -> Path:
    final_path = sra_dir / record.run_accession
    part_path = sra_dir / f"{record.run_accession}.part"

    if verify_download(final_path, record):
        LOGGER.info("%s: already present and verified", record.run_accession)
        return final_path

    if final_path.exists():
        bad_path = final_path.with_name(f"{final_path.name}.bad.{int(timestamp())}")
        final_path.rename(bad_path)
        LOGGER.warning("%s: moved invalid existing file to %s", record.run_accession, bad_path)

    if part_path.exists() and record.sra_size_bytes and part_path.stat().st_size > record.sra_size_bytes:
        LOGGER.warning("%s: partial file is oversized; restarting", record.run_accession)
        part_path.unlink()

    LOGGER.info(
        "%s: downloading %s (%s)",
        record.run_accession,
        record.url,
        human_bytes(record.sra_size_bytes),
    )
    command = [
        curl_path,
        "--fail",
        "--location",
        "--continue-at",
        "-",
        "--retry",
        "20",
        "--retry-all-errors",
        "--retry-delay",
        "15",
        "--connect-timeout",
        "30",
        "--speed-limit",
        "1024",
        "--speed-time",
        "300",
        "--output",
        str(part_path),
        record.url,
    ]
    run_command(command, check=True)

    if not verify_download(part_path, record):
        bad_path = part_path.with_name(f"{part_path.name}.bad.{int(timestamp())}")
        part_path.rename(bad_path)
        raise RuntimeError(
            f"{record.run_accession}: downloaded file failed size/MD5 validation"
        )

    os.replace(part_path, final_path)
    LOGGER.info("%s: download complete and MD5 verified", record.run_accession)
    return final_path


def download_batch(
    records: Iterable[RunRecord],
    sra_dir: Path,
    logs_dir: Path,
    curl_path: str,
    jobs: int,
    batch_attempts: int,
    *,
    download: Callable[[RunRecord, Path, str], Path] = download_one,
    sleep: Callable[[float], None] = time.sleep,
) -> list[RunRecord]:
    remaining = sorted(records, key=lambda record: record.run_accession)
    failed_path = logs_dir / "failed_accessions.txt"

    for batch_attempt in range(1, batch_attempts + 1):
        if not remaining:
            break
        LOGGER.info(
            "Download pass %d/%d: %d run(s) to process",
            batch_attempt,
            batch_attempts,
            len(remaining),
        )
        failed_this_pass: list[RunRecord] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {
                executor.submit(download, record, sra_dir, curl_path): record
                for record in remaining
            }
            for future in concurrent.futures.as_completed(futures):
                record = futures[future]
                try:
                    future.result()
                except Exception:
                    LOGGER.exception(
                        "%s: download failed on pass %d; continuing with other runs",
                        record.run_accession,
                        batch_attempt,
                    )
                    failed_this_pass.append(record)

        remaining = sorted(failed_this_pass, key=lambda record: record.run_accession)
        if remaining and batch_attempt < batch_attempts:
            delay = min(300, 30 * batch_attempt)
            LOGGER.warning(
                "%d run(s) failed on pass %d; retrying them after %d seconds",
                len(remaining),
                batch_attempt,
                delay,
            )
            sleep(delay)

    logs_dir.mkdir(parents=True, exist_ok=True)
    if remaining:
        failed_path.write_text(
            "\n".join(record.run_accession for record in remaining) + "\n",
            encoding="utf-8",
        )
    elif failed_path.exists():
        failed_path.unlink()
    return remaining