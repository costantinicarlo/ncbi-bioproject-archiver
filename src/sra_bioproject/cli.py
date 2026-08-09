"""Command-line interface for SRA BioProject workflows."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from . import archive as archive_module
from .downloader import check_command, download_batch, human_bytes
from .fastq import convert_one, fastq_complete, validate_vdb
from .manifest import read_manifest, write_manifest
from .metadata.client import MetadataClient
from .metadata.snapshot import create_snapshot, normalize_existing
from .metadata.validation import validate_project
from .validation import run_accession_path, validate_destination, verify_download
from .verification import status_project, verify_project
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
        prog="ncbi-bioproject",
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
    download_parser.add_argument("--bioproject", help="NCBI BioProject accession for provenance initialization")
    download_parser.add_argument("--delete-sra-after-fastq", action="store_true")
    download_parser.add_argument("--skip-vdb-validate", action="store_true")
    download_parser.add_argument("--dry-run", action="store_true")
    download_parser.add_argument("--verbose", action="store_true")
    download_parser.set_defaults(handler=run_download)

    for name, help_text, write_manifest_output in (
        ("metadata", "retrieve and normalize BioProject metadata", False),
        ("snapshot", "retrieve metadata and create a download manifest", True),
    ):
        metadata_parser = subparsers.add_parser(name, help=help_text)
        metadata_parser.add_argument("accession", help="NCBI BioProject accession")
        metadata_parser.add_argument("--outdir", type=Path, required=True, help="project output directory")
        metadata_parser.add_argument("--refresh", action="store_true", help="archive and replace an existing snapshot")
        metadata_parser.add_argument("--include-literature-search", action="store_true", help="search Europe PMC for accession mentions")
        metadata_parser.add_argument("--sra-xml", type=Path, help="reuse an existing SRA experiment-package XML file")
        metadata_parser.add_argument("--email", default=os.getenv("NCBI_EMAIL", ""), help="NCBI contact email (or NCBI_EMAIL)")
        metadata_parser.add_argument("--tool", default=os.getenv("NCBI_TOOL", "ncbi-bioproject"), help="NCBI tool identifier (or NCBI_TOOL)")
        metadata_parser.add_argument("--api-key", default=os.getenv("NCBI_API_KEY", ""), help=argparse.SUPPRESS)
        metadata_parser.add_argument("--timeout", type=positive_integer, default=60, help="request timeout in seconds")
        metadata_parser.add_argument("--attempts", type=positive_integer, default=4, help="request attempts for transient failures")
        metadata_parser.set_defaults(handler=run_metadata, write_download_manifest=write_manifest_output)

    normalize_parser = subparsers.add_parser("metadata-normalize", help="rebuild derived files from stored raw metadata")
    normalize_parser.add_argument("--metadata-dir", type=Path, required=True)
    normalize_parser.add_argument("--manifest", type=Path, help="also write a download manifest")
    normalize_parser.set_defaults(handler=run_metadata_normalize)

    validate_parser = subparsers.add_parser("validate", help="validate a local BioProject snapshot")
    validate_parser.add_argument("project_dir", type=Path)
    validate_parser.set_defaults(handler=run_validate)

    verify_parser = subparsers.add_parser("verify", help="verify a local BioProject archive")
    verify_parser.add_argument("project_dir", type=Path)
    verify_parser.add_argument("--bioproject", help="NCBI BioProject accession for legacy bootstrap")
    verify_parser.add_argument("--deep", action="store_true")
    verify_parser.set_defaults(handler=run_verify)

    status_parser = subparsers.add_parser("status", help="report archive lifecycle status")
    status_parser.add_argument("project_dir", type=Path)
    status_parser.set_defaults(handler=run_status)
    return parser


def run_metadata(args: argparse.Namespace) -> int:
    if args.sra_xml is not None and not args.sra_xml.is_file():
        raise FileNotFoundError(args.sra_xml)
    outdir = validate_destination(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    client = MetadataClient(
        email=args.email, tool=args.tool, api_key=args.api_key,
        timeout=args.timeout, attempts=args.attempts,
    )
    try:
        _, partial = create_snapshot(
            args.accession, outdir, client=client, refresh=args.refresh,
            include_literature_search=args.include_literature_search,
            write_download_manifest=args.write_download_manifest,
            sra_xml=args.sra_xml, command=sys.argv,
        )
    except RuntimeError as exc:
        logging.error("Required metadata retrieval incomplete: %s", exc)
        return 3
    except FileExistsError:
        raise
    except Exception as exc:
        logging.error("Metadata normalization or validation failed: %s", exc)
        return 5
    return 4 if partial else 0


def run_metadata_normalize(args: argparse.Namespace) -> int:
    normalize_existing(args.metadata_dir, args.manifest)
    return 0


def run_validate(args: argparse.Namespace) -> int:
    errors = validate_project(args.project_dir)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 5
    print(f"Snapshot is valid: {args.project_dir}")
    return 0


def run_verify(args: argparse.Namespace) -> int:
    return verify_project(args.project_dir, bioproject=args.bioproject, deep=args.deep)


def run_status(args: argparse.Namespace) -> int:
    result = status_project(args.project_dir)
    state = result["state"]
    bioproject = result.get("bioproject")
    if bioproject:
        print(f"BioProject: {bioproject}")
    print(f"Archive status: {state}")
    if state == "VERIFIED":
        return 0
    if state == "INVALID":
        return 5
    return 6


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


def _snapshot_bioproject(outdir: Path) -> str | None:
    snapshot_path = outdir / "metadata" / "snapshot.json"
    if not snapshot_path.is_file():
        return None
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("snapshot.json must be a JSON object")
    bioproject = payload.get("bioproject")
    if not isinstance(bioproject, str) or not bioproject.strip():
        raise ValueError("snapshot.json bioproject must be a non-empty string")
    return archive_module.validate_bioproject(bioproject)


def _resolve_download_bioproject(outdir: Path, explicit_bioproject: str | None) -> str:
    candidates: list[str] = []
    if explicit_bioproject:
        candidates.append(archive_module.validate_bioproject(explicit_bioproject))
    archive_path = archive_module.archive_metadata_path(outdir)
    if archive_path.is_file():
        candidates.append(str(archive_module.load_archive_metadata(outdir)["bioproject"]))
    snapshot_bioproject = _snapshot_bioproject(outdir)
    if snapshot_bioproject is not None:
        candidates.append(snapshot_bioproject)
    unique = sorted(set(candidates))
    if not unique:
        raise ValueError(
            "download requires --bioproject when identity cannot be inferred from an existing managed archive or valid snapshot"
        )
    if len(unique) != 1:
        raise ValueError(f"Conflicting BioProject identities for download: {', '.join(unique)}")
    return unique[0]


def _append_native_admission(outdir: Path, archive_id: str, record, result) -> None:
    admissions = archive_module.load_admission_records(outdir)
    if result.admission_method == "existing":
        for item in admissions:
            if (
                item.get("accession") == record.run_accession
                and item.get("relative_path") == f"sra/{record.run_accession}"
                and item.get("observed_sha256") == result.observed_sha256
                and item.get("admission_method") == "existing"
            ):
                return
    payload = archive_module.create_admission_record(
        archive_id,
        {
            "accession": record.run_accession,
            "admission_method": result.admission_method,
            "initial_partial_size": result.initial_partial_size,
            "expected_size_bytes": record.sra_size_bytes,
            "expected_md5": record.md5,
            "observed_size_bytes": result.observed_size_bytes,
            "observed_md5": result.observed_md5,
            "observed_sha256": result.observed_sha256,
        },
        relative_path=f"sra/{record.run_accession}",
        application_version=__version__,
    )
    admissions.append(payload)
    archive_module.replace_admission_records(outdir, admissions)


def _is_recognizable_legacy_destination(outdir: Path) -> bool:
    return (
        (outdir / "manifest.tsv").is_file()
        or (outdir / "metadata" / "snapshot.json").is_file()
        or ((outdir / "sra").exists() and any((outdir / "sra").iterdir()))
        or ((outdir / "fastq").exists() and any((outdir / "fastq").iterdir()))
    )


def _is_native_new_destination(outdir: Path) -> bool:
    if archive_module.archive_metadata_path(outdir).is_file():
        return False
    if _is_recognizable_legacy_destination(outdir):
        return False
    if not outdir.exists():
        return True
    return not any(outdir.iterdir())


def run_download(args: argparse.Namespace) -> int:
    if args.delete_sra_after_fastq:
        print(
            "--delete-sra-after-fastq is incompatible with the v0.3 archival contract because "
            "SRA is the authoritative archived payload.",
            file=sys.stderr,
        )
        return 2

    if not args.input.is_file():
        raise FileNotFoundError(args.input)

    outdir = validate_destination(args.outdir)
    try:
        bioproject = _resolve_download_bioproject(outdir, args.bioproject)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    managed_archive = archive_module.archive_metadata_path(outdir).is_file()
    legacy_destination = (not managed_archive) and _is_recognizable_legacy_destination(outdir)
    new_destination = _is_native_new_destination(outdir)

    if args.dry_run:
        records, input_format = load_records(args.input, args.input_format)
        total_sra = sum(record.sra_size_bytes for record in records)
        total_bases = sum(record.total_bases for record in records)
        logging.info("Input: %s", args.input)
        logging.info("Runs: %d", len(records))
        logging.info("Total SRA Normalized size: %s", human_bytes(total_sra))
        logging.info("Total sequenced bases: %.3f Tbp", total_bases / 1e12)
        logging.info("Input format: %s", input_format)
        logging.info("BioProject: %s", bioproject)
        logging.info("Dry run complete; no downloads started")
        return 0

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
    previous_manifest = manifest_path.read_bytes() if legacy_destination and manifest_path.is_file() else None
    write_manifest(records, manifest_path)
    if new_destination:
        archive_module.write_archive_metadata(
            outdir,
            archive_module.create_archive_metadata(
                bioproject,
                origin="native",
                application_version=__version__,
            ),
        )
    archive_id = None if legacy_destination else str(archive_module.load_archive_metadata(outdir)["archive_id"])
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
        if not verify_download(run_accession_path(sra_dir, record.run_accession), record)
    )
    if usage.free < remaining_bytes:
        logging.warning(
            "Free space is below the remaining SRA download size (%s)",
            human_bytes(remaining_bytes),
        )

    curl_path = check_command("curl")
    assert curl_path is not None
    records_to_download = records
    if args.mode == "fastq" and args.delete_sra_after_fastq:
        gzip_path = check_command("gzip")
        assert gzip_path is not None
        records_to_download = [
            record
            for record in records
            if not fastq_complete(record.run_accession, fastq_dir, gzip_path)
        ]

    failures = download_batch(
        records_to_download,
        sra_dir,
        logs_dir,
        curl_path,
        args.jobs,
        args.batch_attempts,
        on_success=(
            None
            if legacy_destination
            else lambda record, result: _append_native_admission(outdir, archive_id, record, result)
        ),
    )
    if failures:
        if legacy_destination:
            if previous_manifest is None and manifest_path.exists():
                manifest_path.unlink()
            elif previous_manifest is not None:
                manifest_path.write_bytes(previous_manifest)
        logging.error(
            "%d run(s) still failed after %d pass(es). See %s",
            len(failures),
            args.batch_attempts,
            logs_dir / "failed_accessions.txt",
        )
        return 1
    logging.info("All %d required SRA files downloaded and MD5 verified", len(records_to_download))

    if legacy_destination:
        verification_exit = verify_project(outdir, bioproject=bioproject)
        if verification_exit != 0:
            if previous_manifest is None and manifest_path.exists():
                manifest_path.unlink()
            elif previous_manifest is not None:
                manifest_path.write_bytes(previous_manifest)
        return verification_exit

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
        sra_path = run_accession_path(sra_dir, record.run_accession)
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
    except (argparse.ArgumentError, ValueError, FileExistsError) as exc:
        logging.error("Invalid input: %s", exc)
        raise SystemExit(2)
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        raise SystemExit(1)


def legacy_entrypoint() -> None:
    print(
        "Warning: 'sra-bioproject' is a legacy command name. Use 'ncbi-bioproject' instead.",
        file=sys.stderr,
    )
    entrypoint()
