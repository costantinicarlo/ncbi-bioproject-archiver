import os
from pathlib import Path
import subprocess

import pytest

from sra_bioproject import archive
from sra_bioproject import verification as verification_module
from sra_bioproject.cli import build_parser, run_status, run_verify
from sra_bioproject.manifest import read_manifest, write_manifest
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


def test_status_is_not_verified_after_snapshot_tracked_metadata_corruption(
    tmp_path: Path,
) -> None:
    archive_metadata = archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0")
    archive.write_archive_metadata(tmp_path, archive_metadata)
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    metadata_dir = tmp_path / "metadata"
    raw_dir = metadata_dir / "raw"
    derived_dir = metadata_dir / "derived"
    raw_dir.mkdir(parents=True)
    derived_dir.mkdir(parents=True)
    raw_path = raw_dir / "bioproject.xml"
    raw_path.write_text("<root />\n", encoding="utf-8")
    project_path = derived_dir / "project.json"
    project_path.write_text('{"accession": "PRJNA000001"}\n', encoding="utf-8")
    snapshot_path = metadata_dir / "snapshot.json"
    snapshot_path.write_text(
        "\n".join(
            [
                "{",
                '  "application": "ncbi-bioproject-archiver",',
                '  "application_version": "0.3.0",',
                '  "bioproject": "PRJNA000001",',
                '  "completed_at": "2026-08-09T00:00:00Z",',
                '  "derived_files": [',
                '    {"path": "derived/project.json", "sha256": "5f8133f83f0beae8600df3f5f1fefa680b31e5f1d6778a53af50f6c7bf8ca858", "size_bytes": 27}',
                '  ],',
                '  "raw_files": [',
                '    {"path": "raw/bioproject.xml", "sha256": "7489927f88f0c4e63f4e352a989ee853bfd392db14e8e40f006c888c06fdaf2d", "size_bytes": 9}',
                '  ],',
                '  "record_counts": {},',
                '  "retrieved_at": "2026-08-09T00:00:00Z",',
                '  "schema_version": "1.0",',
                '  "status": "complete"',
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")
    admissions = [
        archive.create_admission_record(
            str(archive_metadata["archive_id"]),
            {
                "accession": "SRR1",
                "admission_method": "existing",
                "initial_partial_size": 0,
                "expected_size_bytes": 5,
                "expected_md5": "5d41402abc4b2a76b9719d911017c592",
                "observed_size_bytes": 5,
                "observed_md5": "5d41402abc4b2a76b9719d911017c592",
                "observed_sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            },
            relative_path="sra/SRR1",
            application_version="0.3.0",
        )
    ]
    archive.replace_admission_records(tmp_path, admissions)
    control_fingerprint = archive.control_fingerprint(
        verification_module._current_control_state(tmp_path, archive_metadata, admissions)
    )
    quick_payload_fingerprint = archive.quick_payload_fingerprint(
        verification_module._quick_payload_entries(tmp_path)
    )
    validations_dir = tmp_path / "provenance" / "validations"
    validations_dir.mkdir(parents=True)
    (validations_dir / "20260809T000000Z-test.json").write_text(
        "\n".join(
            [
                "{",
                '  "application": "ncbi-bioproject-archiver",',
                '  "application_version": "0.3.0",',
                f'  "archive_id": "{archive_metadata["archive_id"]}",',
                '  "bioproject": "PRJNA000001",',
                '  "completed_at": "2026-08-09T00:00:00Z",',
                f'  "control_fingerprint": "{control_fingerprint}",',
                '  "failures": [],',
                '  "mode": "standard",',
                f'  "quick_payload_fingerprint": "{quick_payload_fingerprint}",',
                '  "result": "pass",',
                '  "runs_checked": 1,',
                '  "schema_version": "1.0",',
                '  "started_at": "2026-08-09T00:00:00Z",',
                '  "validation_policy_version": 1',
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    raw_path.write_bytes(raw_path.read_bytes() + b"corrupt")

    assert status_project(tmp_path)["state"] != "VERIFIED"


def test_status_reports_legacy_without_managed_provenance(tmp_path: Path) -> None:
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")

    assert status_project(tmp_path)["state"] == "LEGACY"


def test_status_reports_manifest_only_legacy_without_managed_provenance(tmp_path: Path) -> None:
    write_manifest([make_record()], tmp_path / "manifest.tsv")

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


def test_verify_rejects_conflicting_explicit_bioproject_for_managed_archive(tmp_path: Path) -> None:
    archive.write_archive_metadata(
        tmp_path,
        archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0"),
    )
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")

    assert verify_project(tmp_path, bioproject="PRJNA999999") == 2
    assert not (tmp_path / "provenance" / "validations").exists()


def test_verify_rejects_conflicting_snapshot_identity_for_managed_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive.write_archive_metadata(
        tmp_path,
        archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0"),
    )
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=False)
    snapshot_path = tmp_path / "metadata" / "snapshot.json"
    payload = snapshot_path.read_text(encoding="utf-8").replace("PRJNA000001", "PRJNA999999", 1)
    snapshot_path.write_text(payload, encoding="utf-8")

    assert verify_project(tmp_path) == 2
    assert not (tmp_path / "provenance" / "validations").exists()


def test_verify_rejects_conflicting_explicit_bioproject_for_legacy_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sra_bioproject.metadata.snapshot.retrieve", lambda *args, **kwargs: (fixture_records(), []))
    create_snapshot("PRJNA000001", tmp_path, write_download_manifest=False)

    assert verify_project(tmp_path, bioproject="PRJNA999999") == 2
    assert not (tmp_path / "provenance" / "validations").exists()


def test_legacy_bootstrap_publication_failure_leaves_archive_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")

    def fail_publish(*args, **kwargs):
        raise OSError("forced provenance publish failure")

    monkeypatch.setattr("sra_bioproject.verification.archive_module.publish_provenance_bundle", fail_publish)

    with pytest.raises(OSError, match="forced provenance publish failure"):
        verify_project(tmp_path, bioproject="PRJNA000001")

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


def test_status_is_invalid_for_archive_id_mismatch_in_admissions(tmp_path: Path) -> None:
    archive_metadata = archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0")
    archive.write_archive_metadata(tmp_path, archive_metadata)
    admissions = [
        archive.create_admission_record(
            "00000000-0000-0000-0000-000000000000",
            {
                "accession": "SRR1",
                "admission_method": "existing",
                "initial_partial_size": 0,
                "expected_size_bytes": 5,
                "expected_md5": "5d41402abc4b2a76b9719d911017c592",
                "observed_size_bytes": 5,
                "observed_md5": "5d41402abc4b2a76b9719d911017c592",
                "observed_sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            },
            relative_path="sra/SRR1",
            application_version="0.3.0",
        )
    ]
    archive.replace_admission_records(tmp_path, admissions)

    assert status_project(tmp_path)["state"] == "INVALID"


def test_status_is_invalid_for_malformed_attestation(tmp_path: Path) -> None:
    archive_metadata = archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0")
    archive.write_archive_metadata(tmp_path, archive_metadata)
    validations_dir = tmp_path / "provenance" / "validations"
    validations_dir.mkdir(parents=True)
    (validations_dir / "bad.json").write_text("[]\n", encoding="utf-8")

    assert status_project(tmp_path)["state"] == "INVALID"


def test_deep_verify_requires_vdb_validate_without_writing_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive.write_archive_metadata(
        tmp_path,
        archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0"),
    )
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")
    monkeypatch.setattr("sra_bioproject.verification.check_command", lambda name, required=True: None)

    assert verify_project(tmp_path, deep=True) == 2
    assert not (tmp_path / "provenance" / "validations").exists()


def test_deep_verify_runs_vdb_validate_and_records_deep_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive.write_archive_metadata(
        tmp_path,
        archive.create_archive_metadata("PRJNA000001", origin="native", application_version="0.3.0"),
    )
    write_manifest([make_record()], tmp_path / "manifest.tsv")
    sra_dir = tmp_path / "sra"
    sra_dir.mkdir()
    sra_path = sra_dir / "SRR1"
    sra_path.write_bytes(b"hello")
    commands = []

    monkeypatch.setattr("sra_bioproject.verification.check_command", lambda name, required=True: name)

    def fake_run(command, check):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("sra_bioproject.verification.subprocess.run", fake_run)

    assert verify_project(tmp_path, deep=True) == 0
    assert commands == [["vdb-validate", str(sra_path)]]
    attestation = next((tmp_path / "provenance" / "validations").iterdir())
    payload = attestation.read_text(encoding="utf-8")
    assert '"mode": "deep"' in payload