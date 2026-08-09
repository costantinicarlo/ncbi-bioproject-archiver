from collections import Counter
from pathlib import Path
import subprocess

import pytest

from sra_bioproject.cli import build_parser
from sra_bioproject.downloader import download_batch, download_one
from sra_bioproject.models import RunRecord


def make_record(accession: str) -> RunRecord:
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


def test_skips_verified_file(tmp_path: Path) -> None:
    record = make_record("SRR1")
    (tmp_path / "SRR1").write_bytes(b"hello")

    def should_not_run(*args, **kwargs):
        raise AssertionError("curl must not run for a verified file")

    assert download_one(record, tmp_path, "curl", run_command=should_not_run) == tmp_path / "SRR1"


def test_resumes_part_file(tmp_path: Path) -> None:
    record = make_record("SRR1")
    (tmp_path / "SRR1.part").write_bytes(b"he")
    commands = []

    def finish(command, check):
        commands.append(command)
        (tmp_path / "SRR1.part").write_bytes(b"hello")
        return subprocess.CompletedProcess(command, 0)

    download_one(record, tmp_path, "curl", run_command=finish)
    assert commands[0][commands[0].index("--continue-at") + 1] == "-"
    assert (tmp_path / "SRR1").read_bytes() == b"hello"


def test_promotes_exact_size_partial_after_md5_verification(tmp_path: Path) -> None:
    record = make_record("SRR1")
    (tmp_path / "SRR1.part").write_bytes(b"hello")

    def should_not_run(*args, **kwargs):
        raise AssertionError("curl must not run when exact-size partial is already valid")

    result = download_one(record, tmp_path, "curl", run_command=should_not_run)
    assert result == tmp_path / "SRR1"
    assert (tmp_path / "SRR1").read_bytes() == b"hello"
    assert not (tmp_path / "SRR1.part").exists()


def test_quarantines_invalid_completed_file(tmp_path: Path) -> None:
    record = make_record("SRR1")
    (tmp_path / "SRR1").write_bytes(b"wrong")

    def finish(command, check):
        (tmp_path / "SRR1.part").write_bytes(b"hello")
        return subprocess.CompletedProcess(command, 0)

    download_one(record, tmp_path, "curl", run_command=finish, timestamp=lambda: 123.0)
    assert (tmp_path / "SRR1.bad.123").read_bytes() == b"wrong"
    assert (tmp_path / "SRR1").read_bytes() == b"hello"


def test_continues_and_retries_only_failed_accessions(tmp_path: Path) -> None:
    records = [make_record("SRR1"), make_record("SRR2")]
    calls = []

    def flaky(record, sra_dir, curl_path):
        calls.append(record.run_accession)
        if record.run_accession == "SRR1" and calls.count("SRR1") == 1:
            raise RuntimeError("transient")
        return sra_dir / record.run_accession

    failures = download_batch(
        records,
        tmp_path / "sra",
        tmp_path / "logs",
        "curl",
        jobs=1,
        batch_attempts=2,
        download=flaky,
        sleep=lambda seconds: None,
    )
    assert failures == []
    assert calls == ["SRR1", "SRR2", "SRR1"]


def test_writes_persistent_failures(tmp_path: Path) -> None:
    records = [make_record("SRR1"), make_record("SRR2")]
    calls = Counter()

    def failing(record, sra_dir, curl_path):
        calls[record.run_accession] += 1
        if record.run_accession == "SRR1":
            raise RuntimeError("persistent")
        return sra_dir / record.run_accession

    failures = download_batch(
        records,
        tmp_path / "sra",
        tmp_path / "logs",
        "curl",
        jobs=1,
        batch_attempts=3,
        download=failing,
        sleep=lambda seconds: None,
    )
    assert [record.run_accession for record in failures] == ["SRR1"]
    assert calls == Counter({"SRR1": 3, "SRR2": 1})
    assert (tmp_path / "logs" / "failed_accessions.txt").read_text() == "SRR1\n"


def test_rejects_unsafe_accession_paths(tmp_path: Path) -> None:
    record = make_record("SRR1")
    object.__setattr__(record, "run_accession", "../../escape")
    with pytest.raises(ValueError, match="Invalid run accession"):
        download_one(record, tmp_path, "curl")


@pytest.mark.parametrize("option", ["--jobs", "--threads", "--batch-attempts"])
def test_positive_integer_arguments(option: str) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["download", "input.xml", "--outdir", "output", option, "0"])


@pytest.mark.parametrize("command", ["metadata", "snapshot"])
def test_metadata_commands_parse_network_settings(command: str) -> None:
    args = build_parser().parse_args([
        command, "PRJNA1", "--outdir", "output", "--timeout", "30",
        "--attempts", "2", "--include-literature-search",
    ])
    assert args.accession == "PRJNA1"
    assert args.timeout == 30
    assert args.attempts == 2
    assert args.include_literature_search