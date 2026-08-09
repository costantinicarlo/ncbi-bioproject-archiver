from pathlib import Path
import json
import shutil

from sra_bioproject.manifest import read_manifest
from sra_bioproject.metadata.normalize import normalize, sha256sum
from sra_bioproject.metadata.snapshot import normalize_existing
from sra_bioproject.metadata.schemas import RUN_COLUMNS, SAMPLE_ATTRIBUTE_COLUMNS

FIXTURES = Path(__file__).parent / "fixtures"


def test_normalizes_stored_raw_metadata_without_network(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata"
    shutil.copytree(FIXTURES / "metadata", metadata_dir / "raw")
    shutil.copy(FIXTURES / "minimal_sra_export.xml", metadata_dir / "raw" / "sra_experiments.xml")

    accession, counts = normalize(metadata_dir, tmp_path / "manifest.tsv")

    assert accession == "PRJNA000001"
    assert counts["samples"] == 2
    assert counts["runs"] == 2
    attributes = (metadata_dir / "derived" / "sample_attributes.tsv").read_text()
    assert attributes.splitlines()[0].split("\t") == list(SAMPLE_ATTRIBUTE_COLUMNS)
    assert "custom field\tpreserved" in attributes
    runs = (metadata_dir / "derived" / "runs.tsv").read_text().splitlines()
    assert runs[0].split("\t") == list(RUN_COLUMNS)
    assert [item.run_accession for item in read_manifest(tmp_path / "manifest.tsv")] == ["SRR000001", "SRR000002"]
    assert sha256sum(metadata_dir / "derived" / "runs.tsv")
    assert (metadata_dir / "derived" / "publications.tsv").read_text().count("12345") == 1
    assert "nuccore" in (metadata_dir / "derived" / "linked_resources.tsv").read_text()


def test_normalize_existing_reuses_snapshot_retrieved_at(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata"
    shutil.copytree(FIXTURES / "metadata", metadata_dir / "raw")
    shutil.copy(FIXTURES / "minimal_sra_export.xml", metadata_dir / "raw" / "sra_experiments.xml")

    retrieved_at = "2026-08-09T00:00:00Z"
    normalize(metadata_dir, tmp_path / "manifest.tsv", retrieved_at)
    baseline_project = (metadata_dir / "derived" / "project.json").read_bytes()
    baseline_runs = (metadata_dir / "derived" / "runs.tsv").read_bytes()
    snapshot = {
        "schema_version": "1.0",
        "bioproject": "PRJNA000001",
        "retrieved_at": retrieved_at,
        "completed_at": "2026-08-09T00:00:30Z",
        "status": "complete",
        "application": "sra-bioproject",
        "application_version": "0.2.1",
        "raw_files": [],
        "derived_files": [],
        "record_counts": {"runs": 2, "samples": 2},
    }
    (metadata_dir / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")

    normalize_existing(metadata_dir, tmp_path / "manifest.tsv")

    assert (metadata_dir / "derived" / "project.json").read_bytes() == baseline_project
    assert (metadata_dir / "derived" / "runs.tsv").read_bytes() == baseline_runs