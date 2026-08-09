from pathlib import Path
import os

import pytest

from sra_bioproject import archive


def test_archive_metadata_round_trip_is_immutable(tmp_path: Path) -> None:
    metadata = archive.create_archive_metadata(
        "PRJNA000001",
        origin="native",
        application_version="0.3.0",
        archive_id="12345678-1234-5678-9abc-123456789abc",
        created_at="2026-08-09T00:00:00Z",
    )

    path = archive.write_archive_metadata(tmp_path, metadata)

    assert path == tmp_path / "provenance" / "archive.json"
    assert archive.load_archive_metadata(tmp_path) == metadata
    with pytest.raises(FileExistsError):
        archive.write_archive_metadata(tmp_path, metadata)


def test_replace_admission_records_round_trip_and_rejects_duplicate_ids(tmp_path: Path) -> None:
    records = [
        {
            "schema_version": archive.ACQUISITION_SCHEMA_VERSION,
            "event_id": "11111111-1111-1111-1111-111111111111",
            "archive_id": "12345678-1234-5678-9abc-123456789abc",
            "accession": "SRR000001",
            "relative_path": "sra/SRR000001",
            "admission_method": "downloaded_fresh",
            "admitted_at": "2026-08-09T00:00:00Z",
            "admitted_by_application": archive.APPLICATION_NAME,
            "admitted_by_version": "0.3.0",
            "byte_acquisition": {"application": archive.APPLICATION_NAME, "version": "0.3.0"},
            "expected_size_bytes": 5,
            "expected_md5": "5d41402abc4b2a76b9719d911017c592",
            "observed_size_bytes": 5,
            "observed_md5": "5d41402abc4b2a76b9719d911017c592",
            "observed_sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        }
    ]

    path = archive.replace_admission_records(tmp_path, records)

    assert path == tmp_path / "provenance" / "acquisitions.jsonl"
    assert archive.load_admission_records(tmp_path) == records

    with pytest.raises(ValueError, match="duplicate event IDs"):
        archive.replace_admission_records(tmp_path, records + [dict(records[0])])


def test_quick_payload_fingerprint_changes_on_timestamp_only_change(tmp_path: Path) -> None:
    payload = tmp_path / "sra" / "SRR000001"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"hello")
    first = archive.quick_payload_fingerprint(
        [{"path": "sra/SRR000001", "file_path": payload}]
    )

    stat_result = payload.stat()
    os.utime(payload, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000_000))
    second = archive.quick_payload_fingerprint(
        [{"path": "sra/SRR000001", "file_path": payload}]
    )

    assert first != second


def test_load_admission_records_rejects_unsafe_relative_paths(tmp_path: Path) -> None:
    archive.provenance_directory(tmp_path).mkdir(parents=True)
    (tmp_path / "provenance" / "acquisitions.jsonl").write_text(
        '{"schema_version": "1.0", "event_id": "11111111-1111-1111-1111-111111111111", "archive_id": "12345678-1234-5678-9abc-123456789abc", "accession": "SRR000001", "relative_path": "../escape", "admission_method": "existing", "admitted_at": "2026-08-09T00:00:00Z", "admitted_by_application": "ncbi-bioproject-archiver", "admitted_by_version": "0.3.0", "byte_acquisition": {}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsafe relative path"):
        archive.load_admission_records(tmp_path)