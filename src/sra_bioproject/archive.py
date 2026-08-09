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
_BYTE_PROVENANCE_VALUES = {
    "fresh_download",
    "mixed_or_unknown",
    "unknown_promoted_partial",
    "unknown",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_filename_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


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


def _validate_uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty UUID string")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid UUID string") from exc


def _validate_utc_timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty timestamp")
    raw = value.strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    return raw


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


def _is_benign_scaffold_entry(entry: Path) -> bool:
    if entry.name in {"logs", "tmp"}:
        return entry.is_dir()
    if entry.name in {"sra", "fastq", "metadata", "provenance"}:
        return entry.is_dir() and not any(entry.iterdir())
    return False


def classify_destination(project_dir: Path) -> tuple[bool, bool, bool]:
    managed = archive_metadata_path(project_dir).is_file()
    if managed:
        return True, False, False
    if not project_dir.exists():
        return False, False, True
    if not project_dir.is_dir():
        return False, False, False
    legacy = (
        (project_dir / "manifest.tsv").is_file()
        or (project_dir / "metadata" / "snapshot.json").is_file()
        or ((project_dir / "sra").exists() and any((project_dir / "sra").iterdir()))
        or ((project_dir / "fastq").exists() and any((project_dir / "fastq").iterdir()))
    )
    if legacy:
        return False, True, False
    entries = [child for child in project_dir.iterdir() if not _is_benign_scaffold_entry(child)]
    return False, False, not entries


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
    metadata["archive_id"] = _validate_uuid(metadata.get("archive_id"), "archive.json archive_id")
    metadata["bioproject"] = validate_bioproject(str(metadata.get("bioproject", "")))
    origin = metadata.get("origin")
    if origin not in {"native", "legacy"}:
        raise ValueError(f"Invalid archive origin: {origin!r}")
    metadata["created_at"] = _validate_utc_timestamp(metadata.get("created_at"), "archive.json created_at")
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
    record["event_id"] = _validate_uuid(event_id, "acquisition record event_id")
    archive_id = record.get("archive_id")
    record["archive_id"] = _validate_uuid(archive_id, "acquisition record archive_id")
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
    record["admitted_at"] = _validate_utc_timestamp(admitted_at, "acquisition record admitted_at")
    for key in ("admitted_by_application", "admitted_by_version"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"acquisition record must have a non-empty {key}")
    for key in ("expected_md5", "observed_md5"):
        if key not in record:
            raise ValueError(f"acquisition record missing required field: {key}")
        value = record.get(key)
        if not isinstance(value, str) or not MD5_RE.fullmatch(value.lower()):
            raise ValueError(f"acquisition record has invalid {key}")
    observed_sha256 = record.get("observed_sha256")
    if observed_sha256 is None:
        raise ValueError("acquisition record missing required field: observed_sha256")
    if not isinstance(observed_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", observed_sha256.lower()):
        raise ValueError("acquisition record has invalid observed_sha256")
    for key in ("expected_size_bytes", "observed_size_bytes"):
        if key not in record:
            raise ValueError(f"acquisition record missing required field: {key}")
        value = record.get(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"acquisition record has invalid {key}")
    byte_acquisition = record.get("byte_acquisition")
    if not isinstance(byte_acquisition, dict):
        raise ValueError("acquisition record byte_acquisition must be an object")
    provenance = byte_acquisition.get("provenance")
    if not isinstance(provenance, str) or provenance not in _BYTE_PROVENANCE_VALUES:
        raise ValueError("acquisition record byte_acquisition.provenance is invalid")
    if admission_method == "downloaded_fresh":
        application = byte_acquisition.get("application")
        version = byte_acquisition.get("version")
        if provenance != "fresh_download":
            raise ValueError("downloaded_fresh acquisition record must use fresh_download provenance")
        if not isinstance(application, str) or not application.strip():
            raise ValueError("downloaded_fresh acquisition record must include byte_acquisition.application")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("downloaded_fresh acquisition record must include byte_acquisition.version")
    if admission_method == "resumed_download":
        if provenance != "mixed_or_unknown":
            raise ValueError("resumed_download acquisition record must use mixed_or_unknown provenance")
        initial_partial_size = byte_acquisition.get("initial_partial_size")
        if not isinstance(initial_partial_size, int) or initial_partial_size < 0:
            raise ValueError("resumed_download acquisition record must include non-negative initial_partial_size")
    if admission_method == "promoted_partial":
        if provenance != "unknown_promoted_partial":
            raise ValueError("promoted_partial acquisition record must use unknown_promoted_partial provenance")
        initial_partial_size = byte_acquisition.get("initial_partial_size")
        if not isinstance(initial_partial_size, int) or initial_partial_size < 0:
            raise ValueError("promoted_partial acquisition record must include non-negative initial_partial_size")
    if admission_method in {"existing", "legacy_observation"} and provenance != "unknown":
        raise ValueError("existing and legacy_observation acquisition records must use unknown provenance")
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


def publish_provenance_bundle(
    project_dir: Path,
    archive_metadata: dict[str, object],
    admissions: list[dict[str, object]],
    attestations: list[dict[str, object]],
) -> Path:
    target = provenance_directory(project_dir)
    if target.exists():
        raise FileExistsError(target)
    validated_archive = _validate_archive_metadata(dict(archive_metadata))
    validated_admissions = [_validate_admission_record(dict(record)) for record in admissions]
    staging = project_dir / f".provenance.staging.{uuid.uuid4().hex[:8]}"
    try:
        _atomic_replace(staging / "archive.json", _canonical_json_bytes(validated_archive))
        _atomic_replace(staging / "acquisitions.jsonl", _canonical_jsonl_bytes(validated_admissions))
        for attestation in attestations:
            _atomic_replace(
                staging / "validations" / f"{_utc_filename_stamp()}-{uuid.uuid4().hex[:8]}.json",
                _canonical_json_bytes(attestation),
            )
        os.replace(staging, target)
        _fsync_directory(project_dir)
    finally:
        if staging.exists():
            for child in sorted(staging.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            if staging.exists():
                staging.rmdir()
    return target


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