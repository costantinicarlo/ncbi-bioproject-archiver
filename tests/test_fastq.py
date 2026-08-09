from pathlib import Path

import pytest

from sra_bioproject.fastq import fastq_complete


def _write_complete_marker(path: Path, accession: str, entries: list[tuple[str, int]]) -> None:
    marker = path / f".{accession}.complete"
    marker.write_text("\n".join(f"{name}\t{size}" for name, size in entries) + "\n", encoding="utf-8")


def test_fastq_complete_requires_exact_recorded_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    accession = "SRR123"
    monkeypatch.setattr("sra_bioproject.fastq.gzip_test", lambda path, gzip_path: None)

    mate1 = tmp_path / f"{accession}_1.fastq.gz"
    mate2 = tmp_path / f"{accession}_2.fastq.gz"
    mate1.write_bytes(b"one")
    mate2.write_bytes(b"two")
    _write_complete_marker(
        tmp_path,
        accession,
        [(mate1.name, mate1.stat().st_size), (mate2.name, mate2.stat().st_size)],
    )

    assert fastq_complete(accession, tmp_path, "gzip")

    mate2.unlink()
    assert not fastq_complete(accession, tmp_path, "gzip")


def test_fastq_complete_rejects_marker_filename_injection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    accession = "SRR123"
    monkeypatch.setattr("sra_bioproject.fastq.gzip_test", lambda path, gzip_path: None)
    _write_complete_marker(tmp_path, accession, [("../escape.fastq.gz", 1)])
    assert not fastq_complete(accession, tmp_path, "gzip")


def test_fastq_complete_rejects_unexpected_extra_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    accession = "SRR123"
    monkeypatch.setattr("sra_bioproject.fastq.gzip_test", lambda path, gzip_path: None)

    expected = tmp_path / f"{accession}_1.fastq.gz"
    extra = tmp_path / f"{accession}_2.fastq.gz"
    expected.write_bytes(b"one")
    extra.write_bytes(b"two")
    _write_complete_marker(tmp_path, accession, [(expected.name, expected.stat().st_size)])

    assert not fastq_complete(accession, tmp_path, "gzip")
