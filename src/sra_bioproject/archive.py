"""Archive lifecycle primitives for durable provenance state."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .validation import MD5_RE, RUN_ACCESSION_RE

ARCHIVE_SCHEMA_VERSION = "1.0"
ACQUISITION_SCHEMA_VERSION = "1.0"
ATTESTATION_SCHEMA_VERSION = "1.0"
VALIDATION_POLICY_VERSION = 1
APPLICATION_NAME = "ncbi-bioproject-archiver"

_BIOPROJECT_RE = re.compile(r"^PRJ[A-Z]{2,4}\d+$")
_SUPPORTED_ADMISSION_METHODS = {
    "downloaded_fresh",
    "resumed_download",
    "promoted_partial",
    "existing",
    "legacy_observation",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_jsonl_bytes(records: list[dict[str, object]]) -> bytes:
    lines = [json.dumps(record, sort_keys=True) for record in records]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _safe_relative_path(path_value: str) -> bool:
    if not path_value:
        return False
    path = Path(path_value)
    if path.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in path.parts)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def validate_bioproject(accession: str) -> str:
    value = accession.strip().upper()
    if not _BIOPROJECT_RE.fullmatch(value):
        raise ValueError(f"Invalid BioProject accession: {accession!r}")
    return value


def provenance_directory(project_dir: Path) -> Path:
    return project_dir / "provenance"


def archive_metadata_path(project_dir: Path) -> Path:
    return provenance_directory(project_dir) / "archive.json"


def admission_records_path(project_dir: Path) -> Path:
    return provenance_directory(project_dir) / "acquisitions.jsonl"


def create_archive_metadata(
    bioproject: str,
    *,
    origin: str,
    application_version: str,
    archive_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, object]:
    if origin not in {"native", "legacy"}:
        raise ValueError(f"Invalid archive origin: {origin!r}")
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "archive_id": archive_id or str(uuid.uuid4()),
        "bioproject": validate_bioproject(bioproject),
        "origin": origin,
        "created_at": created_at or _utc_now(),
        "created_by": {
            "application": APPLICATION_NAME,
            "version": application_version,
        },
    }


def create_admission_record(
    archive_id: str,
    record: dict[str, object],
    *,
    relative_path: str,
    application_version: str,
) -> dict[str, object]:
    admission_method = str(record["admission_method"])
    byte_acquisition: dict[str, object] = {"provenance": "unknown"}
    if admission_method == "downloaded_fresh":
        byte_acquisition = {
            "provenance": "fresh_download",
            "application": APPLICATION_NAME,
            "version": application_version,
        }
    elif admission_method == "resumed_download":
        byte_acquisition = {
            "provenance": "mixed_or_unknown",
            "initial_partial_size": record["initial_partial_size"],
        }
    elif admission_method == "promoted_partial":
        byte_acquisition = {
            "provenance": "unknown_promoted_partial",
            "initial_partial_size": record["initial_partial_size"],
        }
    return {
        "schema_version": ACQUISITION_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "archive_id": archive_id,
        "accession": str(record["accession"]),
        "relative_path": relative_path,
        "admission_method": admission_method,
        "admitted_at": _utc_now(),
        "admitted_by_application": APPLICATION_NAME,
        "admitted_by_version": application_version,
        "byte_acquisition": byte_acquisition,
        "expected_size_bytes": int(record["expected_size_bytes"]),
        "expected_md5": str(record["expected_md5"]),
        "observed_size_bytes": int(record["observed_size_bytes"]),
        "observed_md5": str(record["observed_md5"]),
        "observed_sha256": str(record["observed_sha256"]),
    }


def _validate_archive_metadata(metadata: dict[str, object]) -> dict[str, object]:
    required_keys = {"schema_version", "archive_id", "bioproject", "origin", "created_at", "created_by"}
    missing_keys = sorted(required_keys - set(metadata))
    if missing_keys:
        raise ValueError(f"archive.json missing required keys: {', '.join(missing_keys)}")
    schema_version = metadata.get("schema_version")
    if not isinstance(schema_version, str) or schema_version.split(".", 1)[0] != ARCHIVE_SCHEMA_VERSION.split(".", 1)[0]:
        raise ValueError(f"Unsupported archive schema major version: {schema_version}")
    metadata["bioproject"] = validate_bioproject(str(metadata.get("bioproject", "")))
    origin = metadata.get("origin")
    if origin not in {"native", "legacy"}:
        raise ValueError(f"Invalid archive origin: {origin!r}")
    created_by = metadata.get("created_by")
    if not isinstance(created_by, dict):
        raise ValueError("archive.json created_by must be an object")
    if not isinstance(created_by.get("application"), str) or not created_by["application"].strip():
        raise ValueError("archive.json created_by.application must be a non-empty string")
    if not isinstance(created_by.get("version"), str) or not created_by["version"].strip():
        raise ValueError("archive.json created_by.version must be a non-empty string")
    return metadata


def write_archive_metadata(project_dir: Path, metadata: dict[str, object]) -> Path:
    validated = _validate_archive_metadata(dict(metadata))
    path = archive_metadata_path(project_dir)
    if path.exists():
        raise FileExistsError(path)
    return _atomic_replace(path, _canonical_json_bytes(validated))


def load_archive_metadata(project_dir: Path) -> dict[str, object]:
    path = archive_metadata_path(project_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("archive.json must be a JSON object")
    return _validate_archive_metadata(payload)


def _validate_admission_record(record: dict[str, object]) -> dict[str, object]:
    schema_version = record.get("schema_version")
    if not isinstance(schema_version, str) or schema_version.split(".", 1)[0] != ACQUISITION_SCHEMA_VERSION.split(".", 1)[0]:
        raise ValueError(f"Unsupported acquisition schema major version: {schema_version}")
    event_id = record.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("acquisition record must have a non-empty event_id")
    archive_id = record.get("archive_id")
    if not isinstance(archive_id, str) or not archive_id.strip():
        raise ValueError("acquisition record must have a non-empty archive_id")
    accession = record.get("accession")
    if not isinstance(accession, str) or not RUN_ACCESSION_RE.fullmatch(accession.strip().upper()):
        raise ValueError(f"Invalid run accession: {accession!r}")
    relative_path = record.get("relative_path")
    if not isinstance(relative_path, str) or not _safe_relative_path(relative_path):
        raise ValueError(f"acquisition record contains unsafe relative path: {relative_path!r}")
    admission_method = record.get("admission_method")
    if admission_method not in _SUPPORTED_ADMISSION_METHODS:
        raise ValueError(f"Unsupported admission method: {admission_method!r}")
    admitted_at = record.get("admitted_at")
    if not isinstance(admitted_at, str) or not admitted_at.strip():
        raise ValueError("acquisition record must have a non-empty admitted_at")
    for key in ("admitted_by_application", "admitted_by_version"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"acquisition record must have a non-empty {key}")
    for key in ("expected_md5", "observed_md5"):
        if key not in record:
            continue
        value = record.get(key)
        if value is not None and (not isinstance(value, str) or not MD5_RE.fullmatch(value.lower())):
            raise ValueError(f"acquisition record has invalid {key}")
    observed_sha256 = record.get("observed_sha256")
    if observed_sha256 is not None and (
        not isinstance(observed_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", observed_sha256.lower())
    ):
        raise ValueError("acquisition record has invalid observed_sha256")
    for key in ("expected_size_bytes", "observed_size_bytes"):
        if key not in record:
            continue
        value = record.get(key)
        if value is not None and (not isinstance(value, int) or value < 0):
            raise ValueError(f"acquisition record has invalid {key}")
    byte_acquisition = record.get("byte_acquisition")
    if not isinstance(byte_acquisition, dict):
        raise ValueError("acquisition record byte_acquisition must be an object")
    return record


def replace_admission_records(project_dir: Path, records: list[dict[str, object]]) -> Path:
    event_ids: set[str] = set()
    validated_records: list[dict[str, object]] = []
    for record in records:
        validated = _validate_admission_record(dict(record))
        event_id = str(validated["event_id"])
        if event_id in event_ids:
            raise ValueError("acquisition record set contains duplicate event IDs")
        event_ids.add(event_id)
        validated_records.append(validated)
    path = admission_records_path(project_dir)
    return _atomic_replace(path, _canonical_jsonl_bytes(validated_records))


def load_admission_records(project_dir: Path) -> list[dict[str, object]]:
    path = admission_records_path(project_dir)
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    event_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("acquisition record must be a JSON object")
        record = _validate_admission_record(payload)
        event_id = str(record["event_id"])
        if event_id in event_ids:
            raise ValueError("acquisition record set contains duplicate event IDs")
        event_ids.add(event_id)
        records.append(record)
    return records


def control_fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def quick_payload_fingerprint(entries: list[dict[str, object]]) -> str:
    payload_entries: list[dict[str, object]] = []
    for entry in sorted(entries, key=lambda item: str(item["path"])):
        path = entry["file_path"]
        if not isinstance(path, Path):
            raise TypeError("quick payload entries require a pathlib.Path file_path")
        exists = path.exists()
        stat_result = path.stat() if exists else None
        payload_entries.append(
            {
                "path": entry["path"],
                "exists": exists,
                "size": stat_result.st_size if stat_result is not None else None,
                "mtime_ns": stat_result.st_mtime_ns if stat_result is not None else None,
            }
        )
    return hashlib.sha256(_canonical_json_bytes(payload_entries)).hexdigest()