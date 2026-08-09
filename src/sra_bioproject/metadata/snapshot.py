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
import uuid

from .. import __version__
from .. import archive as archive_module
from .client import MetadataClient
from .entrez import retrieve
from .normalize import atomic_write, normalize, sha256sum
from .schemas import SNAPSHOT_SCHEMA_VERSION
from .validation import validate_project


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


def _archive_previous_state(
    outdir: Path,
    previous_metadata_dir: Path,
    stamp: str,
    previous_manifest: Path | None = None,
) -> None:
    archive_root = outdir / "metadata" / "archive"
    archive_destination = archive_root / stamp
    archive_destination.mkdir(parents=True, exist_ok=True)

    def copy_item(source: Path, destination: Path) -> None:
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    for child in list(previous_metadata_dir.iterdir()):
        if child.name == "archive":
            archive_root.mkdir(parents=True, exist_ok=True)
            for archived_entry in list(child.iterdir()):
                destination = archive_root / archived_entry.name
                if destination.exists():
                    destination = archive_root / f"{stamp}-{archived_entry.name}"
                copy_item(archived_entry, destination)
            continue
        copy_item(child, archive_destination / child.name)
    if previous_manifest is not None and previous_manifest.exists():
        copy_item(previous_manifest, archive_destination / "manifest.tsv")


def _unique_stamp() -> str:
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{base}-{uuid.uuid4().hex[:8]}"


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


def _build_snapshot(
    accession: str,
    metadata_dir: Path,
    outdir: Path,
    *,
    client: MetadataClient | None = None,
    include_literature_search: bool = False,
    write_download_manifest: bool = False,
    sra_xml: Path | None = None,
    command: Sequence[str] = (),
) -> tuple[Path, bool]:
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
        "application": archive_module.APPLICATION_NAME, "application_version": __version__,
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
    archive_path = archive_module.archive_metadata_path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    has_snapshot = (metadata_dir / "snapshot.json").exists()
    if has_snapshot and not refresh:
        raise FileExistsError(f"Metadata snapshot already exists at {metadata_dir}; use --refresh")

    stamp = _unique_stamp()
    staging_root = outdir / f".snapshot.staging.{stamp}"
    staging_metadata = staging_root / "metadata"
    staging_manifest = staging_root / "manifest.tsv"
    shutil.rmtree(staging_root, ignore_errors=True)

    try:
        _, partial = _build_snapshot(
            accession,
            staging_metadata,
            staging_root,
            client=client,
            include_literature_search=include_literature_search,
            write_download_manifest=write_download_manifest,
            sra_xml=sra_xml,
            command=command,
        )
        validation_errors = validate_project(staging_root)
        if validation_errors:
            details = "; ".join(validation_errors[:5])
            raise ValueError(f"Staged snapshot failed validation: {details}")

        previous_metadata = outdir / f".metadata.previous.{stamp}"
        previous_manifest = outdir / f".manifest.previous.{stamp}"
        manifest_path = outdir / "manifest.tsv"
        backed_up_metadata = False
        backed_up_manifest = False
        swapped_metadata = False
        swapped_manifest = False
        created_archive_metadata = False

        try:
            if metadata_dir.exists():
                os.replace(metadata_dir, previous_metadata)
                backed_up_metadata = True
            if write_download_manifest and manifest_path.exists():
                os.replace(manifest_path, previous_manifest)
                backed_up_manifest = True

            os.replace(staging_metadata, metadata_dir)
            swapped_metadata = True

            if write_download_manifest:
                if not staging_manifest.exists():
                    raise RuntimeError("Staged manifest missing before publish")
                os.replace(staging_manifest, manifest_path)
                swapped_manifest = True

            if archive_path.is_file():
                archive_metadata = archive_module.load_archive_metadata(outdir)
                if str(archive_metadata["bioproject"]) != accession.upper():
                    raise ValueError(
                        f"Managed archive identity {archive_metadata['bioproject']} does not match requested {accession.upper()}"
                    )
            elif not backed_up_metadata:
                archive_module.write_archive_metadata(
                    outdir,
                    archive_module.create_archive_metadata(
                        accession,
                        origin="native",
                        application_version=__version__,
                    ),
                )
                created_archive_metadata = True

            if backed_up_metadata:
                _archive_previous_state(
                    outdir,
                    previous_metadata,
                    stamp,
                    previous_manifest if backed_up_manifest else None,
                )
            elif backed_up_manifest and previous_manifest.exists():
                previous_manifest.unlink()
            return metadata_dir / "snapshot.json", partial
        except Exception:
            if swapped_manifest and manifest_path.exists():
                manifest_path.unlink()
            if swapped_metadata and metadata_dir.exists():
                shutil.rmtree(metadata_dir, ignore_errors=True)
            if created_archive_metadata and archive_path.exists():
                archive_path.unlink()
                provenance_dir = archive_module.provenance_directory(outdir)
                if provenance_dir.exists() and not any(provenance_dir.iterdir()):
                    provenance_dir.rmdir()
            if backed_up_metadata and previous_metadata.exists():
                os.replace(previous_metadata, metadata_dir)
            if backed_up_manifest and previous_manifest.exists():
                os.replace(previous_manifest, manifest_path)
            raise
        finally:
            if previous_metadata.exists():
                shutil.rmtree(previous_metadata, ignore_errors=True)
            if previous_manifest.exists():
                previous_manifest.unlink()
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def normalize_existing(metadata_dir: Path, manifest_path: Path | None = None) -> tuple[str, dict[str, object]]:
    retrieved_at = ""
    snapshot_path = metadata_dir / "snapshot.json"
    if snapshot_path.is_file():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        value = snapshot.get("retrieved_at")
        if isinstance(value, str):
            retrieved_at = value
    return normalize(metadata_dir, manifest_path, retrieved_at)