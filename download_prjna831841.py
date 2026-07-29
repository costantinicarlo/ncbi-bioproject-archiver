#!/usr/bin/env python3
"""Resumable downloader for NCBI SRA runs listed in an SRA XML export.

The script deliberately selects the lossless "SRA Normalized" object for each
run, not SRA Lite. It can stop after verified SRA download, or convert each run
to split paired FASTQ and compress it before moving to the next run. Individual
network failures do not abort the batch; failed runs are retried in later passes.

Requires:
  - Python 3.9+
  - curl (included with macOS)
Optional for FASTQ mode:
  - NCBI SRA Toolkit: fasterq-dump and vdb-validate
  - pigz (otherwise gzip is used)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class RunRecord:
    accession: str
    experiment_alias: str
    biosample: str
    total_bases: int
    total_spots: int
    url: str
    size: int
    md5: str


def human_bytes(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PiB"


def md5sum(path: Path, block_size: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        while chunk := fh.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def parse_xml(xml_path: Path) -> list[RunRecord]:
    root = ET.parse(xml_path).getroot()
    records: list[RunRecord] = []

    for package in root.findall("./EXPERIMENT_PACKAGE"):
        experiment = package.find("./EXPERIMENT")
        run = package.find("./RUN_SET/RUN")
        if run is None:
            continue

        accession = run.get("accession", "").strip()
        if not accession:
            raise ValueError("A RUN element lacks an accession")

        experiment_alias = ""
        if experiment is not None:
            experiment_alias = experiment.get("alias", "").strip()

        biosample = ""
        sample = package.find("./SAMPLE")
        if sample is not None:
            external = sample.find("./IDENTIFIERS/EXTERNAL_ID[@namespace='BioSample']")
            if external is not None and external.text:
                biosample = external.text.strip()
            else:
                biosample = sample.get("accession", "").strip()

        normalized = [
            sf
            for sf in run.findall("./SRAFiles/SRAFile")
            if sf.get("semantic_name") == "SRA Normalized"
            and sf.get("supertype") == "Primary ETL"
        ]
        if len(normalized) != 1:
            raise ValueError(
                f"{accession}: expected one SRA Normalized file, found {len(normalized)}"
            )

        sf = normalized[0]
        url = (sf.get("url") or "").strip()
        if not url.startswith(("https://", "http://")):
            for alt in sf.findall("./Alternatives"):
                candidate = (alt.get("url") or "").strip()
                if candidate.startswith(("https://", "http://")):
                    url = candidate
                    break
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"{accession}: no HTTP(S) URL for SRA Normalized file")

        records.append(
            RunRecord(
                accession=accession,
                experiment_alias=experiment_alias,
                biosample=biosample,
                total_bases=int(run.get("total_bases", "0")),
                total_spots=int(run.get("total_spots", "0")),
                url=url,
                size=int(sf.get("size", "0")),
                md5=(sf.get("md5") or "").lower(),
            )
        )

    if not records:
        raise ValueError("No runs with SRA Normalized files were found")

    accessions = [r.accession for r in records]
    if len(accessions) != len(set(accessions)):
        raise ValueError("Duplicate run accessions found in XML")

    return sorted(records, key=lambda r: r.accession)


def write_manifest(records: Iterable[RunRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "run_accession",
                "experiment_alias",
                "biosample",
                "total_bases",
                "total_spots",
                "sra_size_bytes",
                "md5",
                "url",
            ]
        )
        for r in records:
            writer.writerow(
                [
                    r.accession,
                    r.experiment_alias,
                    r.biosample,
                    r.total_bases,
                    r.total_spots,
                    r.size,
                    r.md5,
                    r.url,
                ]
            )


def check_command(name: str, required: bool = True) -> str | None:
    path = shutil.which(name)
    if required and path is None:
        raise RuntimeError(f"Required command not found in PATH: {name}")
    return path


def verify_download(path: Path, record: RunRecord) -> bool:
    if not path.is_file():
        return False
    if record.size and path.stat().st_size != record.size:
        return False
    if record.md5:
        observed = md5sum(path)
        if observed != record.md5:
            logging.error(
                "%s: MD5 mismatch: expected %s, observed %s",
                record.accession,
                record.md5,
                observed,
            )
            return False
    return True


def download_one(record: RunRecord, sra_dir: Path, curl_path: str) -> Path:
    final_path = sra_dir / record.accession
    part_path = sra_dir / f"{record.accession}.part"

    if verify_download(final_path, record):
        logging.info("%s: already present and verified", record.accession)
        return final_path

    if final_path.exists():
        bad = final_path.with_name(f"{final_path.name}.bad.{int(time.time())}")
        final_path.rename(bad)
        logging.warning("%s: moved invalid existing file to %s", record.accession, bad)

    if part_path.exists() and record.size and part_path.stat().st_size > record.size:
        logging.warning("%s: partial file is oversized; restarting", record.accession)
        part_path.unlink()

    logging.info(
        "%s: downloading %s (%s)", record.accession, record.url, human_bytes(record.size)
    )
    cmd = [
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
    subprocess.run(cmd, check=True)

    if not verify_download(part_path, record):
        bad = part_path.with_name(f"{part_path.name}.bad.{int(time.time())}")
        part_path.rename(bad)
        raise RuntimeError(f"{record.accession}: downloaded file failed size/MD5 validation")

    os.replace(part_path, final_path)
    logging.info("%s: download complete and MD5 verified", record.accession)
    return final_path


def validate_vdb(sra_path: Path, vdb_validate: str | None) -> None:
    if vdb_validate is None:
        return
    logging.info("%s: running vdb-validate", sra_path.name)
    subprocess.run([vdb_validate, str(sra_path)], check=True)


def gzip_test(path: Path, gzip_path: str) -> None:
    subprocess.run([gzip_path, "-t", str(path)], check=True)


def compress_fastq(path: Path, threads: int, pigz_path: str | None, gzip_path: str) -> Path:
    if pigz_path:
        subprocess.run([pigz_path, "-p", str(threads), "-f", str(path)], check=True)
    else:
        subprocess.run([gzip_path, "-f", str(path)], check=True)
    gz_path = path.with_suffix(path.suffix + ".gz")
    gzip_test(gz_path, gzip_path)
    return gz_path


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
    if fastq_complete(record.accession, fastq_dir, gzip_path):
        logging.info("%s: FASTQ output already complete", record.accession)
        if delete_sra and sra_path.exists():
            sra_path.unlink()
        return

    stage = tmp_dir / f"{record.accession}.stage"
    scratch = tmp_dir / f"{record.accession}.scratch"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)
    stage.mkdir(parents=True)
    scratch.mkdir(parents=True)

    logging.info("%s: converting to split FASTQ", record.accession)
    cmd = [
        fasterq_dump,
        "--split-files",
        "--threads",
        str(threads),
        "--temp",
        str(scratch),
        "--outdir",
        str(stage),
        str(sra_path),
    ]
    subprocess.run(cmd, check=True)

    fastqs = sorted(stage.glob(f"{record.accession}*.fastq"))
    if not fastqs:
        raise RuntimeError(f"{record.accession}: fasterq-dump produced no FASTQ files")

    gz_files: list[Path] = []
    for fastq in fastqs:
        if fastq.stat().st_size == 0:
            raise RuntimeError(f"{record.accession}: empty FASTQ file: {fastq}")
        logging.info("%s: compressing %s", record.accession, fastq.name)
        gz_files.append(compress_fastq(fastq, threads, pigz_path, gzip_path))

    fastq_dir.mkdir(parents=True, exist_ok=True)
    final_files: list[Path] = []
    for gz_path in gz_files:
        destination = fastq_dir / gz_path.name
        os.replace(gz_path, destination)
        final_files.append(destination)

    marker = fastq_dir / f".{record.accession}.complete"
    marker.write_text(
        "\n".join(f"{p.name}\t{p.stat().st_size}" for p in final_files) + "\n",
        encoding="utf-8",
    )

    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)

    if delete_sra:
        sra_path.unlink()
        logging.info("%s: removed verified SRA after FASTQ conversion", record.accession)

    logging.info("%s: FASTQ conversion complete", record.accession)


def configure_logging(log_path: Path, verbose: bool) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s\t%(levelname)s\t%(message)s")

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download lossless NCBI SRA Normalized files listed in an XML export."
    )
    parser.add_argument("xml", type=Path, help="NCBI SRA XML export")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("/Volumes/Bioinfo-1/PRJNA831841"),
        help="Project output directory (default: /Volumes/Bioinfo-1/PRJNA831841)",
    )
    parser.add_argument(
        "--mode",
        choices=("sra", "fastq"),
        default="sra",
        help="sra: verified downloads only; fastq: also convert and gzip (default: sra)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=2,
        help="Concurrent SRA downloads (default: 2)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=max(1, min(8, os.cpu_count() or 1)),
        help="Threads for fasterq-dump/pigz (default: up to 8)",
    )
    parser.add_argument(
        "--batch-attempts",
        type=int,
        default=3,
        help=(
            "Number of complete passes over runs that still failed after curl's "
            "own retries (default: 3)"
        ),
    )
    parser.add_argument(
        "--delete-sra-after-fastq",
        action="store_true",
        help="Delete each SRA only after successful compressed FASTQ creation",
    )
    parser.add_argument(
        "--skip-vdb-validate",
        action="store_true",
        help="Skip vdb-validate in FASTQ mode (MD5 is always checked)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse XML, write manifest, report volumes, and exit",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.jobs < 1 or args.threads < 1 or args.batch_attempts < 1:
        raise ValueError(
            "--jobs, --threads, and --batch-attempts must be positive integers"
        )
    if not args.xml.is_file():
        raise FileNotFoundError(args.xml)

    requested_outdir = args.outdir.expanduser()

    # On macOS, fail clearly if a destination under /Volumes refers to a
    # misspelled or currently unmounted volume. Otherwise mkdir() may fail
    # before logging has been configured, or a command-line redirection may
    # prevent the program from starting at all.
    parts = requested_outdir.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        volume_root = Path("/Volumes") / parts[2]
        if not volume_root.is_dir():
            raise FileNotFoundError(
                f"Destination volume is not mounted: {volume_root}. "
                "Check the spelling with: ls -la /Volumes"
            )
        if not os.access(volume_root, os.W_OK):
            raise PermissionError(f"Destination volume is not writable: {volume_root}")

    outdir = requested_outdir.resolve()
    sra_dir = outdir / "sra"
    fastq_dir = outdir / "fastq"
    tmp_dir = outdir / "tmp"
    logs_dir = outdir / "logs"
    manifest_path = outdir / "manifest.tsv"

    outdir.mkdir(parents=True, exist_ok=True)
    sra_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(logs_dir / "download.log", args.verbose)

    records = parse_xml(args.xml)
    write_manifest(records, manifest_path)

    total_sra = sum(r.size for r in records)
    total_bases = sum(r.total_bases for r in records)
    logging.info("XML: %s", args.xml)
    logging.info("Runs: %d", len(records))
    logging.info("Total SRA Normalized size: %s", human_bytes(total_sra))
    logging.info("Total sequenced bases: %.3f Tbp", total_bases / 1e12)
    logging.info("Manifest: %s", manifest_path)

    usage = shutil.disk_usage(outdir)
    logging.info("Free space at destination: %s", human_bytes(usage.free))
    remaining = sum(
        r.size
        for r in records
        if not verify_download(sra_dir / r.accession, r)
    )
    if usage.free < remaining:
        logging.warning(
            "Free space is below the remaining SRA download size (%s)",
            human_bytes(remaining),
        )

    if args.dry_run:
        logging.info("Dry run complete; no downloads started")
        return 0

    curl_path = check_command("curl", required=True)
    assert curl_path is not None

    records_to_download = records
    if args.mode == "fastq" and args.delete_sra_after_fastq:
        records_to_download = [
            record
            for record in records
            if not (fastq_dir / f".{record.accession}.complete").is_file()
        ]
        already_complete = len(records) - len(records_to_download)
        if already_complete:
            logging.info(
                "Skipping download for %d run(s) already marked as completed FASTQ",
                already_complete,
            )

    logging.info("Starting downloads with %d concurrent job(s)", args.jobs)
    downloaded: dict[str, Path] = {}
    remaining = list(records_to_download)

    for batch_attempt in range(1, args.batch_attempts + 1):
        if not remaining:
            break

        logging.info(
            "Download pass %d/%d: %d run(s) to process",
            batch_attempt,
            args.batch_attempts,
            len(remaining),
        )
        failed_this_pass: list[RunRecord] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
            future_to_record = {
                executor.submit(download_one, record, sra_dir, curl_path): record
                for record in remaining
            }
            for future in concurrent.futures.as_completed(future_to_record):
                record = future_to_record[future]
                try:
                    downloaded[record.accession] = future.result()
                except Exception:
                    logging.exception(
                        "%s: download failed on pass %d; continuing with other runs",
                        record.accession,
                        batch_attempt,
                    )
                    failed_this_pass.append(record)

        remaining = sorted(failed_this_pass, key=lambda r: r.accession)
        if remaining and batch_attempt < args.batch_attempts:
            delay = min(300, 30 * batch_attempt)
            logging.warning(
                "%d run(s) failed on pass %d; retrying them after %d seconds",
                len(remaining),
                batch_attempt,
                delay,
            )
            time.sleep(delay)

    if remaining:
        failed_path = logs_dir / "failed_accessions.txt"
        failed_path.write_text(
            "\n".join(record.accession for record in remaining) + "\n",
            encoding="utf-8",
        )
        logging.error(
            "%d run(s) still failed after %d pass(es). See %s",
            len(remaining),
            args.batch_attempts,
            failed_path,
        )
        return 1

    logging.info(
        "All %d required SRA files downloaded and MD5 verified",
        len(records_to_download),
    )

    if args.mode == "sra":
        return 0

    fasterq_dump = check_command("fasterq-dump", required=True)
    gzip_path = check_command("gzip", required=True)
    pigz_path = check_command("pigz", required=False)
    vdb_validate = None
    if not args.skip_vdb_validate:
        vdb_validate = check_command("vdb-validate", required=True)
    assert fasterq_dump is not None and gzip_path is not None

    if pigz_path is None:
        logging.warning("pigz not found; using single-threaded gzip")

    # Convert sequentially to limit temporary-space and I/O pressure.
    for index, record in enumerate(records, start=1):
        sra_path = sra_dir / record.accession
        logging.info("FASTQ %d/%d: %s", index, len(records), record.accession)
        try:
            if fastq_complete(record.accession, fastq_dir, gzip_path):
                logging.info("%s: FASTQ output already complete", record.accession)
                if args.delete_sra_after_fastq and sra_path.exists():
                    sra_path.unlink()
                continue
            validate_vdb(sra_path, vdb_validate)
            convert_one(
                record=record,
                sra_path=sra_path,
                fastq_dir=fastq_dir,
                tmp_dir=tmp_dir,
                threads=args.threads,
                fasterq_dump=fasterq_dump,
                pigz_path=pigz_path,
                gzip_path=gzip_path,
                delete_sra=args.delete_sra_after_fastq,
            )
        except Exception:
            logging.exception("%s: FASTQ conversion failed", record.accession)
            return 1

    logging.info("All FASTQ conversions completed successfully")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        raise SystemExit(1)
