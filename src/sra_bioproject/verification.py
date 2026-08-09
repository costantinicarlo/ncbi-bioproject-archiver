"""Archive verification and lifecycle status."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from . import __version__
from . import archive as archive_module
from .downloader import DownloadResult, check_command
from .manifest import read_manifest
from .metadata.normalize import sha256sum
from .metadata.validation import validate_project
from .validation import describe_file_integrity, run_accession_path


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_json_write(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _manifest_path(project_dir: Path) -> Path:
    return project_dir / "manifest.tsv"


def _snapshot_path(project_dir: Path) -> Path:
    return project_dir / "metadata" / "snapshot.json"




def _validations_dir(project_dir: Path) -> Path:
    return archive_module.provenance_directory(project_dir) / "validations"


def _recognizable_legacy(project_dir: Path) -> bool:
    sra_has_entries = (project_dir / "sra").exists() and any((project_dir / "sra").glob("*"))
    return (
        _manifest_path(project_dir).is_file()
        or _snapshot_path(project_dir).is_file()
        or sra_has_entries
    )


def _snapshot_bioproject(project_dir: Path) -> str | None:
    snapshot_path = _snapshot_path(project_dir)
    if not snapshot_path.is_file():
        return None
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("snapshot.json must be a JSON object")
    value = payload.get("bioproject")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("snapshot.json bioproject must be a non-empty string")
    return archive_module.validate_bioproject(value)


def _current_control_state(
    project_dir: Path,
    archive_metadata: dict[str, object] | None,
    admissions: list[dict[str, object]],
) -> dict[str, object]:
    manifest_path = _manifest_path(project_dir)
    snapshot_path = _snapshot_path(project_dir)
    records = read_manifest(manifest_path) if manifest_path.is_file() else []
    return {
        "archive": archive_metadata,
        "manifest": {
            "exists": manifest_path.is_file(),
            "sha256": sha256sum(manifest_path) if manifest_path.is_file() else None,
            "runs": [
                {
                    "run_accession": record.run_accession,
                    "size": record.sra_size_bytes,
                    "md5": record.md5,
                }
                for record in records
            ],
        },
        "snapshot": {
            "exists": snapshot_path.is_file(),
            "sha256": sha256sum(snapshot_path) if snapshot_path.is_file() else None,
            "tracked_files": _snapshot_tracked_files_state(project_dir),
        },
        "admissions": admissions,
    }


def _snapshot_tracked_path(project_dir: Path, path_value: str) -> Path:
    base = project_dir if path_value == "manifest.tsv" else project_dir / "metadata"
    candidate = (base / path_value).resolve(strict=False)
    base_resolved = base.resolve(strict=False)
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"snapshot tracked path escapes archive root: {path_value}") from exc
    return candidate


def _snapshot_tracked_files_state(project_dir: Path) -> list[dict[str, object]]:
    snapshot_path = _snapshot_path(project_dir)
    if not snapshot_path.is_file():
        return []
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("snapshot.json must be a JSON object")
    tracked: list[dict[str, object]] = []
    for group in ("raw_files", "derived_files"):
        items = payload.get(group, [])
        if not isinstance(items, list):
            raise ValueError(f"snapshot {group} must be a list")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"snapshot {group} must contain objects")
            path_value = item.get("path")
            if not isinstance(path_value, str) or not path_value:
                raise ValueError(f"snapshot {group} contains invalid path")
            path = _snapshot_tracked_path(project_dir, path_value)
            exists = path.is_file()
            tracked.append(
                {
                    "group": group,
                    "path": path_value,
                    "exists": exists,
                    "size_bytes": path.stat().st_size if exists else None,
                    "sha256": sha256sum(path) if exists else None,
                }
            )
    return tracked


def _quick_payload_entries(project_dir: Path) -> list[dict[str, object]]:
    manifest_path = _manifest_path(project_dir)
    if not manifest_path.is_file():
        return []
    return [
        {
            "path": f"sra/{record.run_accession}",
            "file_path": run_accession_path(project_dir / "sra", record.run_accession),
        }
        for record in read_manifest(manifest_path)
    ]


def _load_attestations(project_dir: Path) -> list[dict[str, object]]:
    validations_dir = _validations_dir(project_dir)
    if not validations_dir.exists():
        return []
    payloads: list[dict[str, object]] = []
    for path in sorted(validations_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Attestation must be a JSON object: {path}")
        payloads.append(payload)
    return payloads


def _validate_attestation(
    payload: dict[str, object],
    *,
    archive_id: str,
    bioproject: str,
) -> dict[str, object]:
    required_keys = {
        "schema_version",
        "validation_policy_version",
        "application",
        "application_version",
        "mode",
        "started_at",
        "completed_at",
        "archive_id",
        "bioproject",
        "control_fingerprint",
        "quick_payload_fingerprint",
        "result",
        "runs_checked",
        "per_run",
        "failures",
    }
    missing_keys = sorted(required_keys - set(payload))
    if missing_keys:
        raise ValueError(f"Attestation missing required keys: {', '.join(missing_keys)}")
    if payload["schema_version"] != archive_module.ATTESTATION_SCHEMA_VERSION:
        raise ValueError("Attestation schema_version is unsupported")
    if payload["validation_policy_version"] != archive_module.VALIDATION_POLICY_VERSION:
        return payload
    if not isinstance(payload["application"], str) or not payload["application"].strip():
        raise ValueError("Attestation application must be a non-empty string")
    if not isinstance(payload["application_version"], str) or not payload["application_version"].strip():
        raise ValueError("Attestation application_version must be a non-empty string")
    if payload["mode"] not in {"standard", "deep"}:
        raise ValueError("Attestation mode must be standard or deep")
    for key in ("started_at", "completed_at"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ValueError(f"Attestation {key} must be a non-empty string")
    if str(payload["archive_id"]) != archive_id:
        raise ValueError("Attestation archive_id does not match archive.json")
    if str(payload["bioproject"]) != bioproject:
        raise ValueError("Attestation bioproject does not match archive.json")
    for key in ("control_fingerprint", "quick_payload_fingerprint"):
        value = payload[key]
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"Attestation {key} must be a SHA-256 hex digest")
        try:
            int(value, 16)
        except ValueError as exc:
            raise ValueError(f"Attestation {key} must be a SHA-256 hex digest") from exc
    if payload["result"] not in {"pass", "fail"}:
        raise ValueError("Attestation result must be pass or fail")
    runs_checked = payload["runs_checked"]
    if not isinstance(runs_checked, int) or runs_checked < 0:
        raise ValueError("Attestation runs_checked must be a non-negative integer")
    if not isinstance(payload["per_run"], list):
        raise ValueError("Attestation per_run must be a list")
    if not isinstance(payload["failures"], list):
        raise ValueError("Attestation failures must be a list")
    return payload


def _validate_managed_identity(
    archive_metadata: dict[str, object],
    admissions: list[dict[str, object]],
) -> None:
    expected_archive_id = str(archive_metadata["archive_id"])
    for admission in admissions:
        if str(admission.get("archive_id")) != expected_archive_id:
            raise ValueError("acquisitions.jsonl contains archive_id values that do not match archive.json")


def _write_attestation(project_dir: Path, payload: dict[str, object]) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _atomic_json_write(_validations_dir(project_dir) / f"{stamp}-{uuid.uuid4().hex[:8]}.json", payload)


def _run_vdb_validate(sra_path: Path, executable: str) -> None:
    subprocess.run([executable, str(sra_path)], check=True)


def status_project(project_dir: Path) -> dict[str, object]:
    archive_path = archive_module.archive_metadata_path(project_dir)
    if not archive_path.exists():
        return {"state": "LEGACY" if _recognizable_legacy(project_dir) else "UNINITIALIZED"}

    try:
        archive_metadata = archive_module.load_archive_metadata(project_dir)
        admissions = archive_module.load_admission_records(project_dir)
        _validate_managed_identity(archive_metadata, admissions)
    except Exception as exc:
        return {"state": "INVALID", "reason": str(exc)}

    try:
        _current_control_state(project_dir, archive_metadata, admissions)
    except Exception as exc:
        return {"state": "INVALID", "bioproject": archive_metadata["bioproject"], "reason": str(exc)}
    try:
        attestations = _load_attestations(project_dir)
    except Exception as exc:
        return {"state": "INVALID", "bioproject": archive_metadata["bioproject"], "reason": str(exc)}
    if not attestations:
        return {"state": "UNVERIFIED", "bioproject": archive_metadata["bioproject"]}

    try:
        attestation = _validate_attestation(
            attestations[-1],
            archive_id=str(archive_metadata["archive_id"]),
            bioproject=str(archive_metadata["bioproject"]),
        )
    except Exception as exc:
        return {"state": "INVALID", "bioproject": archive_metadata["bioproject"], "reason": str(exc)}

    try:
        current_control_fingerprint = archive_module.control_fingerprint(
            _current_control_state(project_dir, archive_metadata, admissions)
        )
        current_quick_fingerprint = archive_module.quick_payload_fingerprint(_quick_payload_entries(project_dir))
    except Exception as exc:
        return {"state": "INVALID", "bioproject": archive_metadata["bioproject"], "reason": str(exc)}
    if attestation.get("validation_policy_version") != archive_module.VALIDATION_POLICY_VERSION:
        return {"state": "STALE", "bioproject": archive_metadata["bioproject"]}
    if attestation.get("control_fingerprint") != current_control_fingerprint:
        return {"state": "STALE", "bioproject": archive_metadata["bioproject"]}
    if attestation.get("quick_payload_fingerprint") != current_quick_fingerprint:
        return {"state": "STALE", "bioproject": archive_metadata["bioproject"]}
    if attestation.get("result") == "pass":
        return {"state": "VERIFIED", "bioproject": archive_metadata["bioproject"]}
    if attestation.get("result") == "fail":
        return {"state": "FAILED", "bioproject": archive_metadata["bioproject"]}
    return {"state": "INVALID", "bioproject": archive_metadata["bioproject"]}


def verify_project(
    project_dir: Path,
    *,
    bioproject: str | None = None,
    deep: bool = False,
    admission_provenance: dict[str, DownloadResult] | None = None,
) -> int:
    archive_path = archive_module.archive_metadata_path(project_dir)
    managed = archive_path.exists()
    snapshot_bioproject = _snapshot_bioproject(project_dir)
    explicit_bioproject = archive_module.validate_bioproject(bioproject) if bioproject is not None else None

    if managed:
        archive_metadata = archive_module.load_archive_metadata(project_dir)
        resolved_bioproject = str(archive_metadata["bioproject"])
        if explicit_bioproject is not None and explicit_bioproject != resolved_bioproject:
            return 2
        if snapshot_bioproject is not None and snapshot_bioproject != resolved_bioproject:
            return 2
    else:
        if not _recognizable_legacy(project_dir):
            return 2
        if explicit_bioproject is not None:
            resolved_bioproject = explicit_bioproject
        elif snapshot_bioproject is not None:
            resolved_bioproject = snapshot_bioproject
        else:
            return 2
        if snapshot_bioproject is not None and snapshot_bioproject != resolved_bioproject:
            return 2
        archive_metadata = None

    manifest_path = _manifest_path(project_dir)
    if not manifest_path.is_file():
        return 2
    vdb_validate = None
    if deep:
        vdb_validate = check_command("vdb-validate", required=False)
        if vdb_validate is None:
            return 2
    records = read_manifest(manifest_path)
    if _snapshot_path(project_dir).is_file() and validate_project(project_dir):
        if managed:
            admissions = archive_module.load_admission_records(project_dir)
            control_fingerprint = archive_module.control_fingerprint(
                _current_control_state(project_dir, archive_metadata, admissions)
            )
            quick_fingerprint = archive_module.quick_payload_fingerprint(_quick_payload_entries(project_dir))
            _write_attestation(
                project_dir,
                {
                    "schema_version": archive_module.ATTESTATION_SCHEMA_VERSION,
                    "validation_policy_version": archive_module.VALIDATION_POLICY_VERSION,
                    "application": archive_module.APPLICATION_NAME,
                    "application_version": __version__,
                    "mode": "deep" if deep else "standard",
                    "started_at": _now(),
                    "completed_at": _now(),
                    "archive_id": archive_metadata["archive_id"],
                    "bioproject": resolved_bioproject,
                    "control_fingerprint": control_fingerprint,
                    "quick_payload_fingerprint": quick_fingerprint,
                    "result": "fail",
                    "runs_checked": 0,
                    "per_run": [],
                    "failures": ["metadata snapshot validation failed"],
                },
            )
            return 5
        return 5

    admissions = archive_module.load_admission_records(project_dir) if managed else []
    prior_by_accession = {item["accession"]: item for item in admissions}
    pending: list[dict[str, object]] = []
    per_run: list[dict[str, object]] = []
    failures: list[str] = []
    verified_integrities: dict[str, object] = {}
    for record in records:
        path = run_accession_path(project_dir / "sra", record.run_accession)
        result = {
            "run_accession": record.run_accession,
            "path": f"sra/{record.run_accession}",
        }
        if not path.is_file():
            result["result"] = "fail"
            result["reason"] = "missing authoritative SRA"
            failures.append(f"{record.run_accession}: missing authoritative SRA")
            per_run.append(result)
            continue
        integrity = describe_file_integrity(path)
        if integrity.size_bytes != record.sra_size_bytes:
            result["result"] = "fail"
            result["reason"] = "size mismatch"
            failures.append(f"{record.run_accession}: size mismatch")
            per_run.append(result)
            continue
        if integrity.md5 != record.md5.lower():
            result["result"] = "fail"
            result["reason"] = "MD5 mismatch"
            failures.append(f"{record.run_accession}: MD5 mismatch")
            per_run.append(result)
            continue
        verified_integrities[record.run_accession] = integrity
        if deep and vdb_validate is not None:
            try:
                _run_vdb_validate(path, vdb_validate)
            except subprocess.CalledProcessError:
                result["result"] = "fail"
                result["reason"] = "vdb-validate failed"
                failures.append(f"{record.run_accession}: vdb-validate failed")
                per_run.append(result)
                continue
        previous = prior_by_accession.get(record.run_accession)
        if previous is None:
            baseline = "baseline_established"
            pending.append(
                archive_module.create_admission_record(
                    str(archive_metadata["archive_id"]) if archive_metadata is not None else str(uuid.uuid4()),
                    {
                        "accession": record.run_accession,
                        "admission_method": "existing" if managed else "legacy_observation",
                        "initial_partial_size": 0,
                        "expected_size_bytes": record.sra_size_bytes,
                        "expected_md5": record.md5,
                        "observed_size_bytes": integrity.size_bytes,
                        "observed_md5": integrity.md5,
                        "observed_sha256": integrity.sha256,
                    },
                    relative_path=f"sra/{record.run_accession}",
                    application_version=__version__,
                )
            )
        else:
            baseline = "baseline_matched" if previous.get("observed_sha256") == integrity.sha256 else "baseline_mismatch"
            if baseline == "baseline_mismatch":
                failures.append(f"{record.run_accession}: SHA-256 baseline mismatch")
        result.update({"result": "pass" if baseline != "baseline_mismatch" else "fail", "baseline": baseline})
        per_run.append(result)

    if not managed and failures:
        return 5

    if managed:
        control_fingerprint = archive_module.control_fingerprint(
            _current_control_state(project_dir, archive_metadata, admissions)
        )
        quick_fingerprint = archive_module.quick_payload_fingerprint(_quick_payload_entries(project_dir))
        if failures:
            _write_attestation(
                project_dir,
                {
                    "schema_version": archive_module.ATTESTATION_SCHEMA_VERSION,
                    "validation_policy_version": archive_module.VALIDATION_POLICY_VERSION,
                    "application": archive_module.APPLICATION_NAME,
                    "application_version": __version__,
                    "mode": "deep" if deep else "standard",
                    "started_at": _now(),
                    "completed_at": _now(),
                    "archive_id": archive_metadata["archive_id"],
                    "bioproject": resolved_bioproject,
                    "control_fingerprint": control_fingerprint,
                    "quick_payload_fingerprint": quick_fingerprint,
                    "result": "fail",
                    "runs_checked": len(records),
                    "per_run": per_run,
                    "failures": failures,
                },
            )
            return 5
        if pending:
            admissions.extend(pending)
            archive_module.replace_admission_records(project_dir, admissions)
        control_fingerprint = archive_module.control_fingerprint(
            _current_control_state(project_dir, archive_metadata, admissions)
        )
        quick_fingerprint = archive_module.quick_payload_fingerprint(_quick_payload_entries(project_dir))
        _write_attestation(
            project_dir,
            {
                "schema_version": archive_module.ATTESTATION_SCHEMA_VERSION,
                "validation_policy_version": archive_module.VALIDATION_POLICY_VERSION,
                "application": archive_module.APPLICATION_NAME,
                "application_version": __version__,
                "mode": "deep" if deep else "standard",
                "started_at": _now(),
                "completed_at": _now(),
                "archive_id": archive_metadata["archive_id"],
                "bioproject": resolved_bioproject,
                "control_fingerprint": control_fingerprint,
                "quick_payload_fingerprint": quick_fingerprint,
                "result": "pass",
                "runs_checked": len(records),
                "per_run": per_run,
                "failures": [],
            },
        )
        return 0

    legacy_archive_metadata = archive_module.create_archive_metadata(
        resolved_bioproject,
        origin="legacy",
        application_version=__version__,
    )
    legacy_archive_id = str(legacy_archive_metadata["archive_id"])
    legacy_admissions = []
    for record in records:
        provenance_result = None
        if admission_provenance is not None:
            provenance_result = admission_provenance.get(record.run_accession)
        if provenance_result is None:
            admission_method = "legacy_observation"
            observed_size_bytes = verified_integrities[record.run_accession].size_bytes
            observed_md5 = verified_integrities[record.run_accession].md5
            observed_sha256 = verified_integrities[record.run_accession].sha256
            initial_partial_size = 0
        else:
            admission_method = provenance_result.admission_method
            observed_size_bytes = provenance_result.observed_size_bytes
            observed_md5 = provenance_result.observed_md5
            observed_sha256 = provenance_result.observed_sha256
            initial_partial_size = provenance_result.initial_partial_size
        legacy_admissions.append(
            archive_module.create_admission_record(
                legacy_archive_id,
                {
                    "accession": record.run_accession,
                    "admission_method": admission_method,
                    "initial_partial_size": initial_partial_size,
                    "expected_size_bytes": record.sra_size_bytes,
                    "expected_md5": record.md5,
                    "observed_size_bytes": observed_size_bytes,
                    "observed_md5": observed_md5,
                    "observed_sha256": observed_sha256,
                },
                relative_path=f"sra/{record.run_accession}",
                application_version=__version__,
            )
        )
    control_fingerprint = archive_module.control_fingerprint(
        _current_control_state(project_dir, legacy_archive_metadata, legacy_admissions)
    )
    quick_fingerprint = archive_module.quick_payload_fingerprint(_quick_payload_entries(project_dir))
    archive_module.publish_provenance_bundle(
        project_dir,
        legacy_archive_metadata,
        legacy_admissions,
        [
            {
                "schema_version": archive_module.ATTESTATION_SCHEMA_VERSION,
                "validation_policy_version": archive_module.VALIDATION_POLICY_VERSION,
                "application": archive_module.APPLICATION_NAME,
                "application_version": __version__,
                "mode": "deep" if deep else "standard",
                "started_at": _now(),
                "completed_at": _now(),
                "archive_id": legacy_archive_id,
                "bioproject": resolved_bioproject,
                "control_fingerprint": control_fingerprint,
                "quick_payload_fingerprint": quick_fingerprint,
                "result": "pass",
                "runs_checked": len(records),
                "per_run": per_run,
                "failures": [],
            }
        ],
    )
    return 0