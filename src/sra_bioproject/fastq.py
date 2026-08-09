"""Sequential, storage-conscious conversion of verified SRA files to FASTQ."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil
import subprocess

from .models import RunRecord
from .validation import ensure_path_within_directory, run_accession_path, validate_run_accession

LOGGER = logging.getLogger(__name__)

MARKER_LINE_RE = re.compile(r"^([^\t]+)\t(\d+)$")


def validate_vdb(sra_path: Path, vdb_validate: str | None) -> None:
    if vdb_validate is not None:
        LOGGER.info("%s: running vdb-validate", sra_path.name)
        try:
            subprocess.run([vdb_validate, str(sra_path)], check=True)
        except FileNotFoundError:
            LOGGER.warning("%s: vdb-validate is unavailable; skipping validation", sra_path.name)


def gzip_test(path: Path, gzip_path: str) -> None:
    subprocess.run([gzip_path, "-t", str(path)], check=True)


def compress_fastq(
    path: Path,
    threads: int,
    pigz_path: str | None,
    gzip_path: str,
) -> Path:
    if pigz_path:
        subprocess.run([pigz_path, "-p", str(threads), "-f", str(path)], check=True)
    else:
        subprocess.run([gzip_path, "-f", str(path)], check=True)
    compressed_path = path.with_suffix(path.suffix + ".gz")
    gzip_test(compressed_path, gzip_path)
    return compressed_path


def _marker_entries(accession: str, marker: Path) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    for line in marker.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        match = MARKER_LINE_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"{accession}: invalid completion marker line: {line!r}")
        filename, size_str = match.groups()
        if "/" in filename or "\\" in filename:
            raise ValueError(f"{accession}: completion marker contains unsafe filename: {filename!r}")
        if not filename.startswith(accession) or not filename.endswith(".fastq.gz"):
            raise ValueError(f"{accession}: completion marker contains unexpected filename: {filename!r}")
        size = int(size_str)
        if size <= 0:
            raise ValueError(f"{accession}: completion marker recorded non-positive size for {filename!r}")
        entries.append((filename, size))
    if not entries:
        raise ValueError(f"{accession}: completion marker is empty")
    return entries


def _expected_fastq_paths(accession: str, fastq_dir: Path) -> list[tuple[Path, int]]:
    marker = ensure_path_within_directory(fastq_dir, fastq_dir / f".{accession}.complete")
    entries = _marker_entries(accession, marker)
    paths = [
        (ensure_path_within_directory(fastq_dir, fastq_dir / filename), size)
        for filename, size in entries
    ]
    names = [path.name for path, _ in paths]
    if len(names) != len(set(names)):
        raise ValueError(f"{accession}: completion marker contains duplicate filenames")
    return paths


def fastq_complete(accession: str, fastq_dir: Path, gzip_path: str) -> bool:
    accession = validate_run_accession(accession)
    marker = ensure_path_within_directory(fastq_dir, fastq_dir / f".{accession}.complete")
    if not marker.is_file():
        return False
    try:
        expected = _expected_fastq_paths(accession, fastq_dir)
    except ValueError:
        return False
    existing = sorted(path.name for path in fastq_dir.glob(f"{accession}*.fastq.gz"))
    if sorted(path.name for path, _ in expected) != existing:
        return False
    try:
        for path, expected_size in expected:
            if not path.is_file() or path.stat().st_size != expected_size:
                return False
            gzip_test(path, gzip_path)
    except subprocess.CalledProcessError:
        return False
    return True


def convert_one(
    record: RunRecord,
    sra_path: Path,
    fastq_dir: Path,
    tmp_dir: Path,
    threads: int,
    fasterq_dump: str,
    pigz_path: str | None,
    gzip_path: str,
    delete_sra: bool,
) -> None:
    accession = validate_run_accession(record.run_accession)
    sra_path = run_accession_path(sra_path.parent, accession)
    fastq_dir = ensure_path_within_directory(fastq_dir.parent, fastq_dir)
    tmp_dir = ensure_path_within_directory(tmp_dir.parent, tmp_dir)

    if fastq_complete(record.run_accession, fastq_dir, gzip_path):
        LOGGER.info("%s: FASTQ output already complete", record.run_accession)
        if delete_sra and sra_path.exists():
            sra_path.unlink()
        return

    stage = run_accession_path(tmp_dir, accession, ".stage")
    scratch = run_accession_path(tmp_dir, accession, ".scratch")
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)
    stage.mkdir(parents=True)
    scratch.mkdir(parents=True)

    LOGGER.info("%s: converting to split FASTQ", record.run_accession)
    subprocess.run(
        [
            fasterq_dump,
            "--split-files",
            "--threads",
            str(threads),
            "--temp",
            str(scratch),
            "--outdir",
            str(stage),
            str(sra_path),
        ],
        check=True,
    )

    fastqs = sorted(stage.glob(f"{record.run_accession}*.fastq"))
    if not fastqs:
        raise RuntimeError(f"{record.run_accession}: fasterq-dump produced no FASTQ files")

    compressed_files: list[Path] = []
    for fastq in fastqs:
        if fastq.stat().st_size == 0:
            raise RuntimeError(f"{record.run_accession}: empty FASTQ file: {fastq}")
        LOGGER.info("%s: compressing %s", record.run_accession, fastq.name)
        compressed_files.append(compress_fastq(fastq, threads, pigz_path, gzip_path))

    fastq_dir.mkdir(parents=True, exist_ok=True)
    final_files: list[Path] = []
    for compressed_path in compressed_files:
        destination = ensure_path_within_directory(fastq_dir, fastq_dir / compressed_path.name)
        os.replace(compressed_path, destination)
        final_files.append(destination)

    marker = ensure_path_within_directory(fastq_dir, fastq_dir / f".{accession}.complete")
    marker.write_text(
        "\n".join(f"{path.name}\t{path.stat().st_size}" for path in final_files) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)

    if delete_sra:
        sra_path.unlink()
        LOGGER.info("%s: removed verified SRA after FASTQ conversion", record.run_accession)
    LOGGER.info("%s: FASTQ conversion complete", record.run_accession)
