"""Sequential, storage-conscious conversion of verified SRA files to FASTQ."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import subprocess

from .models import RunRecord

LOGGER = logging.getLogger(__name__)


def validate_vdb(sra_path: Path, vdb_validate: str | None) -> None:
    if vdb_validate is not None:
        LOGGER.info("%s: running vdb-validate", sra_path.name)
        subprocess.run([vdb_validate, str(sra_path)], check=True)


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


def fastq_complete(accession: str, fastq_dir: Path, gzip_path: str) -> bool:
    marker = fastq_dir / f".{accession}.complete"
    if not marker.is_file():
        return False
    files = sorted(fastq_dir.glob(f"{accession}*.fastq.gz"))
    if not files:
        return False
    try:
        for path in files:
            if path.stat().st_size == 0:
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
    if fastq_complete(record.run_accession, fastq_dir, gzip_path):
        LOGGER.info("%s: FASTQ output already complete", record.run_accession)
        if delete_sra and sra_path.exists():
            sra_path.unlink()
        return

    stage = tmp_dir / f"{record.run_accession}.stage"
    scratch = tmp_dir / f"{record.run_accession}.scratch"
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
        destination = fastq_dir / compressed_path.name
        os.replace(compressed_path, destination)
        final_files.append(destination)

    marker = fastq_dir / f".{record.run_accession}.complete"
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
