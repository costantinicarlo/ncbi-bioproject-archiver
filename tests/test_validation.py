import os
from pathlib import Path

import pytest

from sra_bioproject.models import RunRecord
from sra_bioproject.validation import md5sum, validate_destination, verify_download


def record_for(content: bytes) -> RunRecord:
    return RunRecord(
        run_accession="SRRTEST",
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
        sra_size_bytes=len(content),
        md5="5d41402abc4b2a76b9719d911017c592",
        url="https://example.test/SRRTEST",
    )


def test_size_and_md5_verification(tmp_path: Path) -> None:
    path = tmp_path / "SRRTEST"
    path.write_bytes(b"hello")
    assert md5sum(path) == "5d41402abc4b2a76b9719d911017c592"
    assert verify_download(path, record_for(b"hello"))
    path.write_bytes(b"wrong")
    assert not verify_download(path, record_for(b"hello"))


def test_macos_missing_volume_is_rejected() -> None:
    with pytest.raises(FileNotFoundError, match="not mounted"):
        validate_destination(Path("/Volumes/DefinitelyMissing/Test"), platform="darwin")


def test_non_macos_volumes_and_local_paths_are_portable(tmp_path: Path) -> None:
    assert validate_destination(Path("/Volumes/DefinitelyMissing/Test"), platform="linux") == Path("/Volumes/DefinitelyMissing/Test")
    assert validate_destination(tmp_path / "output", platform="darwin") == tmp_path / "output"


def test_macos_unwritable_volume_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    original_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: True if path == Path("/Volumes/Test") else original_is_dir(path))
    monkeypatch.setattr(Path, "is_mount", lambda path: True if path == Path("/Volumes/Test") else False)
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    with pytest.raises(PermissionError, match="not writable"):
        validate_destination(Path("/Volumes/Test/Project"), platform="darwin")


def test_macos_non_mount_volume_path_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    original_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: True if path == Path("/Volumes/Test") else original_is_dir(path))
    monkeypatch.setattr(Path, "is_mount", lambda path: False)
    monkeypatch.setattr(os, "access", lambda path, mode: True)
    with pytest.raises(FileNotFoundError, match="not on a mounted volume"):
        validate_destination(Path("/Volumes/Test/Project"), platform="darwin")


def test_verification_fails_closed_for_missing_integrity_metadata(tmp_path: Path) -> None:
    path = tmp_path / "SRRTEST"
    path.write_bytes(b"hello")
    record = RunRecord(
        run_accession="SRRTEST",
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
        sra_size_bytes=0,
        md5="",
        url="https://example.test/SRRTEST",
    )
    assert not verify_download(path, record)
