import os
from pathlib import Path

import pytest

from sra_bioproject import archive
from sra_bioproject.cli import build_parser, run_status, run_verify
from sra_bioproject.manifest import write_manifest
from sra_bioproject.metadata.snapshot import create_snapshot
from sra_bioproject.metadata.models import RawResponseRecord
from sra_bioproject.models import RunRecord
from sra_bioproject.verification import status_project, verify_project

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


def make_record(accession: str = "SRR1") -> RunRecord:
    return RunRecord(
        run_accession=accession,
        experiment_accession="",
        experiment_alias="",
        biosample="",
        sample_alias="",
        library_strategy="",
        library_source="",
        library_layout="",
        instrument_model="",
        total_bases=0,
        total_spots=0,
        sra_size_bytes=5,
        md5="5d41402abc4b2a76b9719d911017c592",
        url=f"https://example.test/{accession}",
    )


def test_metadata_only_archive_is_unverified_and_not_verifiable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=False)

    status = status_project(tmp_path)

    assert status["state"] == "UNVERIFIED"
    assert verify_project(tmp_path) == 2
    assert not (tmp_path / "provenance" / "validations").exists()


def test_verify_managed_archive_writes_attestation_and_pending_admission(tmp_path: Path) -> None:
    archive.write_archive_metadata(
        tmp_path,
        archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0"),
    )
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")

    assert verify_project(tmp_path) == 0

    admissions = archive.load_admission_records(tmp_path)
    assert len(admissions) == 1
    assert admissions[0]["admission_method"] == "existing"
    validations = sorted((tmp_path / "provenance" / "validations").iterdir())
    assert len(validations) == 1
    status = status_project(tmp_path)
    assert status["state"] == "VERIFIED"


def test_status_becomes_stale_after_timestamp_only_change(tmp_path: Path) -> None:
    archive.write_archive_metadata(
        tmp_path,
        archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0"),
    )
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    payload = sra_dir / "SRR1"
    payload.write_bytes(b"hello")

    assert verify_project(tmp_path) == 0
    stat_result = payload.stat()
    os.utime(payload, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000_000))

    assert status_project(tmp_path)["state"] == "STALE"


def test_status_reports_legacy_without_managed_provenance(tmp_path: Path) -> None:
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")

    assert status_project(tmp_path)["state"] == "LEGACY"


def test_verify_bootstraps_legacy_archive_only_on_complete_success(tmp_path: Path) -> None:
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")

    assert verify_project(tmp_path, bioproject="PRJNA000001") == 0
    assert archive.load_archive_metadata(tmp_path)["origin"] == "legacy"
    assert status_project(tmp_path)["state"] == "VERIFIED"


def test_verify_leaves_failing_legacy_archive_unmanaged(tmp_path: Path) -> None:
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"wrong")

    assert verify_project(tmp_path, bioproject="PRJNA000001") == 5
    assert not (tmp_path / "provenance").exists()
    assert status_project(tmp_path)["state"] == "LEGACY"


def test_cli_parses_verify_and_status_commands() -> None:
    parser = build_parser()

    verify_args = parser.parse_args(["verify", "project", "--bioproject", "PRJNA000001"])
    status_args = parser.parse_args(["status", "project"])

    assert verify_args.project_dir == Path("project")
    assert verify_args.bioproject == "PRJNA000001"
    assert status_args.project_dir == Path("project")


def test_run_status_maps_verified_and_unverified_states_to_exit_codes(tmp_path: Path) -> None:
    archive.write_archive_metadata(
        tmp_path,
        archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0"),
    )
    args = build_parser().parse_args(["status", str(tmp_path)])

    assert run_status(args) == 6

    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")
    assert verify_project(tmp_path) == 0

    assert run_status(args) == 0


def test_run_verify_delegates_exit_code(tmp_path: Path) -> None:
    args = build_parser().parse_args(["verify", str(tmp_path), "--bioproject", "PRJNA000001"])
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")

    assert run_verify(args) == 0