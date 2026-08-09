"""Validate snapshot checksums, schemas, and run-manifest consistency."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re

from ..manifest import read_manifest
from ..validation import MD5_RE, RUN_ACCESSION_RE
from .normalize import sha256sum
from .schemas import PROJECT_SCHEMA_VERSION, RUN_COLUMNS, SAMPLE_COLUMNS, SNAPSHOT_SCHEMA_VERSION

REQUIRED_RAW_FILES = {
    "raw/bioproject.xml",
    "raw/biosamples.xml",
    "raw/sra_experiments.xml",
    "raw/sra_runinfo.csv",
    "raw/pubmed.xml",
    "raw/pmc.xml",
    "raw/assemblies.xml",
    "raw/entrez_links.xml",
}

REQUIRED_DERIVED_FILES = {
    "derived/project.json",
    "derived/samples.tsv",
    "derived/sample_attributes.tsv",
    "derived/runs.tsv",
    "derived/publications.tsv",
    "derived/relationships.tsv",
    "derived/linked_resources.tsv",
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _safe_relative(path_value: str) -> bool:
    if not path_value:
        return False
    path = Path(path_value)
    if path.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in path.parts)


def _contains(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _record_count(snapshot: dict[str, object], key: str) -> int | None:
    record_counts = snapshot.get("record_counts")
    if not isinstance(record_counts, dict):
        return None
    value = record_counts.get(key)
    if not isinstance(value, int) or value < 0:
        return None
    return value


def validate_project(project_dir: Path) -> list[str]:
    errors: list[str] = []
    metadata_dir = project_dir / "metadata"
    snapshot_path = metadata_dir / "snapshot.json"
    if not snapshot_path.is_file():
        return [f"Missing snapshot: {snapshot_path}"]

    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"Invalid snapshot JSON: {exc}"]
    if not isinstance(snapshot, dict):
        return ["snapshot.json must be a JSON object"]

    required_keys = {
        "schema_version", "bioproject", "retrieved_at", "completed_at", "status",
        "application", "application_version", "raw_files", "derived_files", "record_counts",
    }
    missing_keys = sorted(required_keys - set(snapshot))
    if missing_keys:
        errors.append(f"snapshot.json missing required keys: {', '.join(missing_keys)}")

    schema_version = snapshot.get("schema_version", "")
    if not isinstance(schema_version, str) or not schema_version:
        errors.append("snapshot schema_version must be a non-empty string")
    else:
        supported_major = SNAPSHOT_SCHEMA_VERSION.split(".", 1)[0]
        actual_major = schema_version.split(".", 1)[0]
        if actual_major != supported_major:
            errors.append(
                f"Unsupported snapshot schema major version: {schema_version} (expected {supported_major}.x)"
            )

    bioproject = snapshot.get("bioproject", "")
    if not isinstance(bioproject, str) or not bioproject.strip():
        errors.append("snapshot bioproject must be a non-empty string")
        bioproject = ""
    else:
        bioproject = bioproject.strip().upper()

    file_lists: dict[str, set[str]] = {"raw_files": set(), "derived_files": set()}
    for group in ("raw_files", "derived_files"):
        items = snapshot.get(group)
        if not isinstance(items, list) or not items:
            errors.append(f"snapshot {group} must be a non-empty list")
            continue
        for item in items:
            if not isinstance(item, dict):
                errors.append(f"snapshot {group} contains non-object entry")
                continue
            path_value = item.get("path")
            size_bytes = item.get("size_bytes")
            checksum = item.get("sha256")
            if not isinstance(path_value, str) or not _safe_relative(path_value):
                errors.append(f"snapshot {group} contains unsafe relative path: {path_value!r}")
                continue
            if not isinstance(size_bytes, int) or size_bytes < 0:
                errors.append(f"snapshot {group} has invalid size_bytes for {path_value}")
            if not isinstance(checksum, str) or not SHA256_RE.fullmatch(checksum):
                errors.append(f"snapshot {group} has invalid sha256 for {path_value}")

            base = project_dir if path_value == "manifest.tsv" else metadata_dir
            path = base / path_value
            if not _contains(base, path):
                errors.append(f"snapshot {group} path escapes base directory: {path_value}")
                continue
            file_lists[group].add(path_value)
            if not path.is_file():
                errors.append(f"Missing file: {path}")
            else:
                if isinstance(size_bytes, int) and path.stat().st_size != size_bytes:
                    errors.append(f"size_bytes mismatch: {path}")
                if isinstance(checksum, str) and SHA256_RE.fullmatch(checksum) and sha256sum(path) != checksum:
                    errors.append(f"SHA-256 mismatch: {path}")

    missing_raw = sorted(REQUIRED_RAW_FILES - file_lists["raw_files"])
    if missing_raw:
        errors.append(f"snapshot is missing required raw files: {', '.join(missing_raw)}")
    missing_derived = sorted(REQUIRED_DERIVED_FILES - file_lists["derived_files"])
    if missing_derived:
        errors.append(f"snapshot is missing required derived files: {', '.join(missing_derived)}")

    runs_path = metadata_dir / "derived" / "runs.tsv"
    run_rows: list[dict[str, str]] = []
    runs: dict[str, dict[str, str]] = {}
    if runs_path.is_file():
        with runs_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != RUN_COLUMNS:
                errors.append("runs.tsv header does not match the current schema")
            run_rows = list(reader)
            runs = {}
            for row in run_rows:
                raw_accession = row.get("run_accession")
                accession_key = raw_accession if isinstance(raw_accession, str) else ""
                runs[accession_key] = row
        run_accessions = [
            (row.get("run_accession") if isinstance(row.get("run_accession"), str) else "")
            for row in run_rows
        ]
        if len({item for item in run_accessions if item}) != len([item for item in run_accessions if item]):
            errors.append("runs.tsv contains duplicate run accessions")
        expected_runs = _record_count(snapshot, "runs")
        if expected_runs is None:
            errors.append("snapshot record_counts.runs must be a non-negative integer")
        elif len(run_rows) != expected_runs:
            errors.append("runs.tsv count does not match snapshot provenance")
        for row in run_rows:
            raw_accession = row.get("run_accession", "")
            accession = raw_accession if isinstance(raw_accession, str) else ""
            if not RUN_ACCESSION_RE.fullmatch(accession):
                errors.append(f"runs.tsv contains invalid run accession: {accession}")
            bioproject_value = row.get("bioproject", "")
            if not isinstance(bioproject_value, str) or bioproject_value.strip().upper() != bioproject:
                errors.append(f"runs.tsv bioproject mismatch for run {accession}")
            sra_size = row.get("sra_size_bytes", "")
            if not isinstance(sra_size, str) or not sra_size.isdigit() or int(sra_size) <= 0:
                errors.append(f"runs.tsv has non-positive sra_size_bytes for {accession}")
            md5_raw = row.get("md5", "")
            md5 = md5_raw.strip().lower() if isinstance(md5_raw, str) else ""
            if not MD5_RE.fullmatch(md5):
                errors.append(f"runs.tsv has invalid md5 for {accession}")

    samples_path = metadata_dir / "derived" / "samples.tsv"
    if samples_path.is_file():
        with samples_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != SAMPLE_COLUMNS:
                errors.append("samples.tsv header does not match the current schema")
            sample_rows = list(reader)
        biosamples = [
            (row.get("biosample") if isinstance(row.get("biosample"), str) else "")
            for row in sample_rows
        ]
        samples = {item for item in biosamples if item}
        if len(samples) != len([item for item in biosamples if item]):
            errors.append("samples.tsv contains duplicate BioSample accessions")
        expected_samples = _record_count(snapshot, "samples")
        if expected_samples is None:
            errors.append("snapshot record_counts.samples must be a non-negative integer")
        elif len(sample_rows) != expected_samples:
            errors.append("samples.tsv count does not match snapshot provenance")
        for row in sample_rows:
            bioproject_value = row.get("bioproject", "")
            biosample_value = row.get("biosample", "")
            if not isinstance(bioproject_value, str) or bioproject_value.strip().upper() != bioproject:
                errors.append(f"samples.tsv bioproject mismatch for sample {biosample_value if isinstance(biosample_value, str) else ''}")
        unresolved = sorted(
            {
                biosample
                for row in run_rows
                for biosample in [row.get("biosample") if isinstance(row.get("biosample"), str) else ""]
                if biosample and biosample not in samples
            }
        )
        if unresolved:
            errors.append(f"Unresolved run BioSamples: {', '.join(unresolved)}")

    project_json_path = metadata_dir / "derived" / "project.json"
    if project_json_path.is_file():
        try:
            project_payload = json.loads(project_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid project.json: {exc}")
        else:
            if not isinstance(project_payload, dict):
                errors.append("project.json must be a JSON object")
                project_payload = {}
            project_accession = project_payload.get("accession", "")
            project_accession_text = project_accession.strip().upper() if isinstance(project_accession, str) else ""
            if project_accession_text != bioproject:
                errors.append("project.json accession does not match snapshot bioproject")
            if project_payload.get("schema_version") != PROJECT_SCHEMA_VERSION:
                errors.append("project.json schema_version does not match supported schema")
            snapshot_retrieved = snapshot.get("retrieved_at")
            if isinstance(snapshot_retrieved, str) and project_payload.get("retrieved_at") != snapshot_retrieved:
                errors.append("project.json retrieved_at does not match snapshot provenance")

    manifest_path = project_dir / "manifest.tsv"
    if manifest_path.exists() and runs:
        try:
            manifest = {item.run_accession: item for item in read_manifest(manifest_path)}
        except Exception as exc:
            errors.append(f"manifest.tsv is invalid: {exc}")
            return errors
        run_keys = {item for item in runs if item}
        if run_keys != set(manifest):
            errors.append("manifest and runs.tsv accessions differ")
        for accession in run_keys & set(manifest):
            record = manifest[accession]
            run_data = runs[accession]
            run_url = run_data.get("url")
            run_size = run_data.get("sra_size_bytes")
            run_md5 = run_data.get("md5")
            if not (isinstance(run_url, str) and isinstance(run_size, str) and isinstance(run_md5, str)):
                errors.append(f"runs.tsv missing required fields for {accession}")
                continue
            if (run_url, run_size, run_md5) != (record.url, str(record.sra_size_bytes), record.md5):
                errors.append(f"manifest and runs.tsv differ for {accession}")
    return errors