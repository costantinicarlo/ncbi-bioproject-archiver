"""Validate snapshot checksums, schemas, and run-manifest consistency."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..manifest import read_manifest
from .normalize import sha256sum
from .schemas import RUN_COLUMNS


def validate_project(project_dir: Path) -> list[str]:
    errors = []
    metadata_dir = project_dir / "metadata"
    snapshot_path = metadata_dir / "snapshot.json"
    if not snapshot_path.is_file():
        return [f"Missing snapshot: {snapshot_path}"]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for group in ("raw_files", "derived_files"):
        for item in snapshot.get(group, []):
            base = project_dir if item["path"] == "manifest.tsv" else metadata_dir
            path = base / item["path"]
            if not path.is_file():
                errors.append(f"Missing file: {path}")
            elif sha256sum(path) != item["sha256"]:
                errors.append(f"SHA-256 mismatch: {path}")
    runs_path = metadata_dir / "derived" / "runs.tsv"
    if runs_path.is_file():
        with runs_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != RUN_COLUMNS:
                errors.append("runs.tsv header does not match the current schema")
            run_rows = list(reader)
            runs = {row["run_accession"]: row for row in run_rows}
        if len(runs) != len(run_rows):
            errors.append("runs.tsv contains duplicate run accessions")
        if len(runs) != snapshot.get("record_counts", {}).get("runs"):
            errors.append("runs.tsv count does not match snapshot provenance")
        samples_path = metadata_dir / "derived" / "samples.tsv"
        if samples_path.is_file():
            with samples_path.open(encoding="utf-8", newline="") as handle:
                sample_rows = list(csv.DictReader(handle, delimiter="\t"))
            samples = {row["biosample"] for row in sample_rows}
            if len(samples) != len(sample_rows):
                errors.append("samples.tsv contains duplicate BioSample accessions")
            unresolved = sorted({row["biosample"] for row in run_rows if row["biosample"] and row["biosample"] not in samples})
            if unresolved:
                errors.append(f"Unresolved run BioSamples: {', '.join(unresolved)}")
        manifest_path = project_dir / "manifest.tsv"
        if manifest_path.exists():
            manifest = {item.run_accession: item for item in read_manifest(manifest_path)}
            if set(runs) != set(manifest):
                errors.append("manifest and runs.tsv accessions differ")
            for accession in set(runs) & set(manifest):
                record = manifest[accession]
                if (runs[accession]["url"], runs[accession]["sra_size_bytes"], runs[accession]["md5"]) != (record.url, str(record.sra_size_bytes), record.md5):
                    errors.append(f"manifest and runs.tsv differ for {accession}")
    return errors