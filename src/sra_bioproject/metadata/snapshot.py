"""Create, archive, and rebuild BioProject metadata snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import platform
from pathlib import Path
import shutil
import sys
from typing import Sequence

from .. import __version__
from .client import MetadataClient
from .entrez import retrieve
from .normalize import atomic_write, normalize, sha256sum
from .schemas import SNAPSHOT_SCHEMA_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _describe(path: Path, root: Path, record_count: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256sum(path)}
    if record_count is not None:
        result["record_count"] = record_count
    return result


def _archive(metadata_dir: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = metadata_dir / "archive" / stamp
    destination.mkdir(parents=True)
    for child in list(metadata_dir.iterdir()):
        if child.name != "archive":
            shutil.move(str(child), destination / child.name)


def _archive_previous_state(outdir: Path, previous_metadata_dir: Path, stamp: str) -> None:
    archive_destination = outdir / "metadata" / "archive" / stamp
    archive_destination.mkdir(parents=True, exist_ok=True)
    for child in list(previous_metadata_dir.iterdir()):
        shutil.move(str(child), archive_destination / child.name)
    previous_metadata_dir.rmdir()


def _safe_command(command: Sequence[str]) -> list[str]:
    sanitized = []
    hide_next = False
    for argument in command:
        if hide_next:
            sanitized.append("REDACTED")
            hide_next = False
        elif argument == "--api-key":
            sanitized.append(argument)
            hide_next = True
        elif argument.startswith("--api-key="):
            sanitized.append("--api-key=REDACTED")
        else:
            sanitized.append(argument)
    return sanitized


def _build_snapshot(accession: str, metadata_dir: Path, outdir: Path, *, client: MetadataClient | None = None, include_literature_search: bool = False, write_download_manifest: bool = False, sra_xml: Path | None = None, command: Sequence[str] = ()) -> tuple[Path, bool]:
    raw_dir = metadata_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    started = _now()
    records, warnings = retrieve(
        client or MetadataClient(), accession, include_literature_search,
        require_sra=sra_xml is None,
    )
    raw_details = []
    for record in records:
        path = raw_dir / record.filename
        atomic_write(path, record.content)
        detail = _describe(path, metadata_dir)
        detail.update({"database": record.database, "operation": record.operation, "linkname": record.linkname, "query": record.query, "content_type": record.content_type, "http_status": record.status, "retrieved_at": record.retrieved_at})
        raw_details.append(detail)
    if sra_xml is not None:
        atomic_write(raw_dir / "sra_experiments.xml", sra_xml.read_bytes())
        raw_details = [item for item in raw_details if item["path"] != "raw/sra_experiments.xml"]
        detail = _describe(raw_dir / "sra_experiments.xml", metadata_dir)
        detail.update({"database": "sra", "operation": "supplied_file", "content_type": "application/xml"})
        raw_details.append(detail)
    manifest_path = outdir / "manifest.tsv" if write_download_manifest else None
    actual_accession, counts = normalize(metadata_dir, manifest_path, started)
    if actual_accession != accession.upper():
        raise ValueError(f"Requested {accession} but retrieved {actual_accession}")
    derived_details = [_describe(path, metadata_dir, int(counts.get(path.stem, 0))) for path in sorted((metadata_dir / "derived").iterdir())]
    if manifest_path is not None:
        derived_details.append(_describe(manifest_path, outdir, counts.get("runs")))
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION, "bioproject": actual_accession,
        "retrieved_at": started, "completed_at": _now(), "status": "partial" if warnings else "complete",
        "application": "sra-bioproject", "application_version": __version__,
        "python_version": platform.python_version(), "platform": platform.platform(),
        "command": _safe_command(command), "include_literature_search": include_literature_search,
        "sources": sorted({item.database for item in records}), "queries": [{"database": item.database, "operation": item.operation, "query": item.query, "linkname": item.linkname} for item in records],
        "raw_files": sorted(raw_details, key=lambda item: str(item["path"])),
        "derived_files": sorted(derived_details, key=lambda item: str(item["path"])),
        "warnings": warnings, "record_counts": counts,
    }
    atomic_write(metadata_dir / "snapshot.json", (json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode())
    return metadata_dir / "snapshot.json", bool(warnings)


def create_snapshot(accession: str, outdir: Path, *, client: MetadataClient | None = None, refresh: bool = False, include_literature_search: bool = False, write_download_manifest: bool = False, sra_xml: Path | None = None, command: Sequence[str] = ()) -> tuple[Path, bool]:
    metadata_dir = outdir / "metadata"
    has_snapshot = (metadata_dir / "snapshot.json").exists()
    if has_snapshot and not refresh:
        raise FileExistsError(f"Metadata snapshot already exists at {metadata_dir}; use --refresh")

    if refresh and metadata_dir.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        staging_dir = outdir / f".metadata.staging.{stamp}"
        shutil.rmtree(staging_dir, ignore_errors=True)
        snapshot_path: Path | None = None
        partial = False
        try:
            snapshot_path, partial = _build_snapshot(
                accession,
                staging_dir,
                outdir,
                client=client,
                include_literature_search=include_literature_search,
                write_download_manifest=write_download_manifest,
                sra_xml=sra_xml,
                command=command,
            )
            previous_dir = outdir / f".metadata.previous.{stamp}"
            if previous_dir.exists():
                shutil.rmtree(previous_dir)
            os.replace(metadata_dir, previous_dir)
            os.replace(staging_dir, metadata_dir)
            _archive_previous_state(outdir, previous_dir, stamp)
            return metadata_dir / "snapshot.json", partial
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    return _build_snapshot(
        accession,
        metadata_dir,
        outdir,
        client=client,
        include_literature_search=include_literature_search,
        write_download_manifest=write_download_manifest,
        sra_xml=sra_xml,
        command=command,
    )


def normalize_existing(metadata_dir: Path, manifest_path: Path | None = None) -> tuple[str, dict[str, object]]:
    retrieved_at = ""
    snapshot_path = metadata_dir / "snapshot.json"
    if snapshot_path.is_file():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        value = snapshot.get("retrieved_at")
        if isinstance(value, str):
            retrieved_at = value
    return normalize(metadata_dir, manifest_path, retrieved_at)