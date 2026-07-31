from pathlib import Path

import pytest

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


@pytest.mark.parametrize("relative_path", ["raw/bioproject.xml", "derived/runs.tsv"])
def test_validation_detects_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_path: str) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=True)
    path = tmp_path / "metadata" / relative_path
    path.write_bytes(path.read_bytes() + b"corrupt")
    assert any("SHA-256 mismatch" in error for error in validate_project(tmp_path))