"""Command-line interface for SRA BioProject workflows."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import shutil
import sys

from .downloader import check_command, download_batch, human_bytes
from .fastq import convert_one, fastq_complete, validate_vdb
from .manifest import read_manifest, write_manifest
from .validation import validate_destination, verify_download
from .xml_parser import parse_xml


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sra-bioproject",
        description="Download and verify lossless SRA Normalized data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest", help="convert an SRA XML export to TSV")
    manifest_parser.add_argument("xml", type=Path, help="NCBI SRA XML export")
    manifest_parser.add_argument("--output", type=str, required=True, help="TSV path or - for stdout")
    manifest_parser.set_defaults(handler=run_manifest)

    download_parser = subparsers.add_parser("download", help="download runs from XML or TSV")
    download_parser.add_argument("input", type=Path, help="NCBI SRA XML export or TSV manifest")
    download_parser.add_argument("--outdir", type=Path, required=True, help="project output directory")
    download_parser.add_argument(
        "--input-format",
        choices=("auto", "xml", "tsv"),
        default="auto",
        help="input format (default: infer from .xml or .tsv extension)",
    )
    download_parser.add_argument("--mode", choices=("sra", "fastq"), default="sra")
    download_parser.add_argument("--jobs", type=positive_integer, default=2)
    download_parser.add_argument(
        "--threads",
        type=positive_integer,
        default=max(1, min(8, os.cpu_count() or 1)),
    )
    download_parser.add_argument("--batch-attempts", type=positive_integer, default=3)
    download_parser.add_argument("--delete-sra-after-fastq", action="store_true")
    download_parser.add_argument("--skip-vdb-validate", action="store_true")
    download_parser.add_argument("--dry-run", action="store_true")
    download_parser.add_argument("--verbose", action="store_true")
    download_parser.set_defaults(handler=run_download)
    return parser


def run_manifest(args: argparse.Namespace) -> int:
    if not args.xml.is_file():
        raise FileNotFoundError(args.xml)
    write_manifest(parse_xml(args.xml), args.output)
    return 0


def load_records(path: Path, input_format: str) -> tuple[list, str]:
    selected_format = input_format
    if selected_format == "auto":
        suffix = path.suffix.lower()
        if suffix == ".xml":
            selected_format = "xml"
        elif suffix == ".tsv":
            selected_format = "tsv"
        else:
            raise ValueError(
                f"Cannot infer input format from {path}; use --input-format xml or tsv"
            )
    if selected_format == "xml":
        return parse_xml(path), selected_format
    return read_manifest(path), selected_format


def run_download(args: argparse.Namespace) -> int:
    if not args.input.is_file():
        raise FileNotFoundError(args.input)

    outdir = validate_destination(args.outdir)
    sra_dir = outdir / "sra"
    fastq_dir = outdir / "fastq"
    tmp_dir = outdir / "tmp"
    logs_dir = outdir / "logs"
    outdir.mkdir(parents=True, exist_ok=True)
    sra_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(logs_dir / "download.log", args.verbose)

    records, input_format = load_records(args.input, args.input_format)
    manifest_path = outdir / "manifest.tsv"
    write_manifest(records, manifest_path)
    total_sra = sum(record.sra_size_bytes for record in records)
    total_bases = sum(record.total_bases for record in records)
    logging.info("Input: %s", args.input)
    logging.info("Runs: %d", len(records))
    logging.info("Total SRA Normalized size: %s", human_bytes(total_sra))
    logging.info("Total sequenced bases: %.3f Tbp", total_bases / 1e12)
    logging.info("Input format: %s", input_format)
    logging.info("Manifest: %s", manifest_path)

    usage = shutil.disk_usage(outdir)
    logging.info("Free space at destination: %s", human_bytes(usage.free))
    remaining_bytes = sum(
        record.sra_size_bytes
        for record in records
        if not verify_download(sra_dir / record.run_accession, record)
    )
    if usage.free < remaining_bytes:
        logging.warning(
            "Free space is below the remaining SRA download size (%s)",
            human_bytes(remaining_bytes),
        )

    if args.dry_run:
        logging.info("Dry run complete; no downloads started")
        return 0

    curl_path = check_command("curl")
    assert curl_path is not None
    records_to_download = records
    if args.mode == "fastq" and args.delete_sra_after_fastq:
        records_to_download = [
            record
            for record in records
            if not (fastq_dir / f".{record.run_accession}.complete").is_file()
        ]

    failures = download_batch(
        records_to_download,
        sra_dir,
        logs_dir,
        curl_path,
        args.jobs,
        args.batch_attempts,
    )
    if failures:
        logging.error(
            "%d run(s) still failed after %d pass(es). See %s",
            len(failures),
            args.batch_attempts,
            logs_dir / "failed_accessions.txt",
        )
        return 1
    logging.info("All %d required SRA files downloaded and MD5 verified", len(records_to_download))

    if args.mode == "sra":
        return 0

    fasterq_dump = check_command("fasterq-dump")
    gzip_path = check_command("gzip")
    pigz_path = check_command("pigz", required=False)
    vdb_validate = None if args.skip_vdb_validate else check_command("vdb-validate")
    assert fasterq_dump is not None and gzip_path is not None
    if pigz_path is None:
        logging.warning("pigz not found; using single-threaded gzip")

    for index, record in enumerate(records, start=1):
        sra_path = sra_dir / record.run_accession
        logging.info("FASTQ %d/%d: %s", index, len(records), record.run_accession)
        if fastq_complete(record.run_accession, fastq_dir, gzip_path):
            logging.info("%s: FASTQ output already complete", record.run_accession)
            if args.delete_sra_after_fastq and sra_path.exists():
                sra_path.unlink()
            continue
        validate_vdb(sra_path, vdb_validate)
        convert_one(
            record,
            sra_path,
            fastq_dir,
            tmp_dir,
            args.threads,
            fasterq_dump,
            pigz_path,
            gzip_path,
            args.delete_sra_after_fastq,
        )
    logging.info("All FASTQ conversions completed successfully")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


def entrypoint() -> None:
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        raise SystemExit(1)
