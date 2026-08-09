from pathlib import Path
import json

import pytest

from sra_bioproject.metadata import snapshot as snapshot_module
from sra_bioproject.metadata.models import RawResponseRecord
from sra_bioproject.metadata.snapshot import create_snapshot
from sra_bioproject.metadata.validation import validate_project

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_records() -> list[RawResponseRecord]:
    metadata = FIXTURES / "metadata"
    return [
        RawResponseRecord("bioproject.xml", (metadata / "bioproject.xml").read_bytes(), "bioproject", "esummary", "application/xml"),
        RawResponseRecord("biosamples.xml", (metadata / "biosamples.xml").read_bytes(), "biosample", "efetch", "application/xml"),
        RawResponseRecord("sra_experiments.xml", (FIXTURES / "minimal_sra_export.xml").read_bytes(), "sra", "efetch", "application/xml"),
        RawResponseRecord("sra_runinfo.csv", b"Run\n", "sra", "efetch", "text/csv"),
        RawResponseRecord("pubmed.xml", (metadata / "pubmed.xml").read_bytes(), "pubmed", "efetch", "application/xml"),
        RawResponseRecord("pmc.xml", b"<pmc-articleset />", "pmc", "efetch", "application/xml"),
        RawResponseRecord("assemblies.xml", b"<DocumentSummarySet />", "assembly", "efetch", "application/xml"),
        RawResponseRecord("entrez_links.xml", (metadata / "entrez_links.xml").read_bytes(), "bioproject", "elink", "application/xml"),
    ]


def test_snapshot_refuses_overwrite_archives_refresh_and_validates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    snapshot, partial = create_snapshot(
        "PRJNA000001", tmp_path, write_download_manifest=True,
        command=["snapshot", "--api-key", "secret"],
    )
    assert snapshot.is_file()
    assert not partial
    assert validate_project(tmp_path) == []
    assert "secret" not in snapshot.read_text(encoding="utf-8")
    assert '"retrieved_at"' in (tmp_path / "metadata" / "derived" / "project.json").read_text()

    with pytest.raises(FileExistsError, match="--refresh"):
        create_snapshot("PRJNA000001", tmp_path)

    create_snapshot("PRJNA000001", tmp_path, refresh=True, write_download_manifest=True)
    archives = list((tmp_path / "metadata" / "archive").iterdir())
    assert len(archives) == 1
    assert (archives[0] / "snapshot.json").is_file()


def test_refresh_is_transactional_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    baseline_snapshot = (tmp_path / "metadata" / "snapshot.json").read_bytes()

    def fail_retrieve(*args, **kwargs):
        raise RuntimeError("transient failure")

    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", fail_retrieve)
    with pytest.raises(RuntimeError, match="transient failure"):
        create_snapshot("PRJNA000001", tmp_path, refresh=True, write_download_manifest=True)

    assert (tmp_path / "metadata" / "snapshot.json").read_bytes() == baseline_snapshot
    assert not list(tmp_path.glob(".metadata.staging.*"))


def test_repeated_refreshes_keep_archive_entries_flat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    create_snapshot("PRJNA000001", tmp_path, refresh=True, write_download_manifest=True)
    create_snapshot("PRJNA000001", tmp_path, refresh=True, write_download_manifest=True)

    archive_root = tmp_path / "metadata" / "archive"
    entries = sorted(item for item in archive_root.iterdir() if item.is_dir())
    assert len(entries) == 2
    assert all((entry / "snapshot.json").is_file() for entry in entries)
    assert all(not (entry / "archive").exists() for entry in entries)


def test_refresh_failure_after_manifest_write_restores_live_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    baseline_snapshot = (tmp_path / "metadata" / "snapshot.json").read_bytes()
    baseline_manifest = (tmp_path / "manifest.tsv").read_bytes()

    original_describe = snapshot_module._describe

    def fail_manifest_describe(path, root, record_count=None):
        if path.name == "manifest.tsv":
            raise RuntimeError("forced failure after manifest write")
        return original_describe(path, root, record_count)

    monkeypatch.setattr("sra_bioproject.metadata.snapshot._describe", fail_manifest_describe)
    with pytest.raises(RuntimeError, match="forced failure"):
        create_snapshot("PRJNA000001", tmp_path, refresh=True, write_download_manifest=True)

    assert (tmp_path / "metadata" / "snapshot.json").read_bytes() == baseline_snapshot
    assert (tmp_path / "manifest.tsv").read_bytes() == baseline_manifest


def test_initial_creation_failure_leaves_no_partial_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    original_describe = snapshot_module._describe

    def fail_manifest_describe(path, root, record_count=None):
        if path.name == "manifest.tsv":
            raise RuntimeError("forced failure during initial create")
        return original_describe(path, root, record_count)

    monkeypatch.setattr("sra_bioproject.metadata.snapshot._describe", fail_manifest_describe)
    with pytest.raises(RuntimeError, match="forced failure"):
        create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)

    assert not (tmp_path / "metadata").exists()
    assert not (tmp_path / "manifest.tsv").exists()
    assert not list(tmp_path.glob(".snapshot.staging.*"))


def test_refresh_aborts_when_staged_validation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    baseline_snapshot = (tmp_path / "metadata" / "snapshot.json").read_bytes()
    baseline_manifest = (tmp_path / "manifest.tsv").read_bytes()

    def fail_validation(project_dir: Path) -> list[str]:
        if project_dir.name.startswith(".snapshot.staging."):
            return ["forced staged validation failure"]
        return []

    monkeypatch.setattr("sra_bioproject.metadata.snapshot.validate_project", fail_validation)
    with pytest.raises(ValueError, match="Staged snapshot failed validation"):
        create_snapshot("PRJNA000001", tmp_path, refresh=True, write_download_manifest=True)

    assert (tmp_path / "metadata" / "snapshot.json").read_bytes() == baseline_snapshot
    assert (tmp_path / "manifest.tsv").read_bytes() == baseline_manifest


def test_archive_copy_failure_restores_previous_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    baseline_snapshot = (tmp_path / "metadata" / "snapshot.json").read_bytes()
    baseline_raw = (tmp_path / "metadata" / "raw" / "bioproject.xml").read_bytes()
    baseline_manifest = (tmp_path / "manifest.tsv").read_bytes()

    original_copy2 = snapshot_module.shutil.copy2
    calls = {"count": 0}

    def flaky_copy2(source, destination, *, follow_symlinks=True):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise OSError("forced archive copy failure")
        return original_copy2(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(snapshot_module.shutil, "copy2", flaky_copy2)
    with pytest.raises(OSError, match="forced archive copy failure"):
        create_snapshot("PRJNA000001", tmp_path, refresh=True, write_download_manifest=True)

    assert (tmp_path / "metadata" / "snapshot.json").read_bytes() == baseline_snapshot
    assert (tmp_path / "metadata" / "raw" / "bioproject.xml").read_bytes() == baseline_raw
    assert (tmp_path / "manifest.tsv").read_bytes() == baseline_manifest


@pytest.mark.parametrize("relative_path", ["raw/bioproject.xml", "derived/runs.tsv"])
def test_validation_detects_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_path: str) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    path = tmp_path / "metadata" / relative_path
    path.write_bytes(path.read_bytes() + b"corrupt")
    assert any("SHA-256 mismatch" in error for error in validate_project(tmp_path))


def test_validation_rejects_empty_snapshot_object(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "snapshot.json").write_text(json.dumps({}), encoding="utf-8")
    errors = validate_project(tmp_path)
    assert any("missing required keys" in error for error in errors)


def test_validation_reports_malformed_runs_header_without_crashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    runs_path = tmp_path / "metadata" / "derived" / "runs.tsv"
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    header = lines[0].replace("run_accession", "run_id")
    runs_path.write_text("\n".join([header, *lines[1:]]) + "\n", encoding="utf-8")

    errors = validate_project(tmp_path)
    assert any("runs.tsv header" in error for error in errors)


def test_validation_reports_non_object_project_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    (tmp_path / "metadata" / "derived" / "project.json").write_text("[]\n", encoding="utf-8")

    errors = validate_project(tmp_path)
    assert any("project.json must be a JSON object" in error for error in errors)


@pytest.mark.parametrize("payload", ['{"accession": 123}\n', '{"accession": null}\n'])
def test_validation_reports_non_string_project_accession(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    (tmp_path / "metadata" / "derived" / "project.json").write_text(payload, encoding="utf-8")

    errors = validate_project(tmp_path)
    assert any("project.json accession does not match" in error for error in errors)


@pytest.mark.parametrize("column", ["md5", "url"])
def test_validation_reports_missing_runs_fields_without_crashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, column: str) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    runs_path = tmp_path / "metadata" / "derived" / "runs.tsv"
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    index = header.index(column)
    header.pop(index)
    rewritten = ["\t".join(header)]
    for line in lines[1:]:
        fields = line.split("\t")
        fields.pop(index)
        rewritten.append("\t".join(fields))
    runs_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    errors = validate_project(tmp_path)
    assert any("runs.tsv header" in error for error in errors)
    assert any("runs.tsv missing required fields" in error for error in errors)


def test_validation_reports_invalid_manifest_without_crashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    (tmp_path / "manifest.tsv").write_text("not\ta\tmanifest\n", encoding="utf-8")

    errors = validate_project(tmp_path)
    assert any("manifest.tsv is invalid" in error for error in errors)