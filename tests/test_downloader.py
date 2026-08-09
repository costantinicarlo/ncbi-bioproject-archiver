from collections import Counter
from pathlib import Path
import subprocess

import pytest

from sra_bioproject import archive
from sra_bioproject import downloader as downloader_module
from sra_bioproject.cli import _is_native_new_destination, build_parser, run_download
from sra_bioproject.downloader import DownloadResult, download_batch, download_one
from sra_bioproject.manifest import write_manifest
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

    result = download_one(record, tmp_path, "curl", run_command=should_not_run)

    assert result.path == tmp_path / "SRR1"
    assert result.admission_method == "existing"
    assert result.initial_partial_size == 0


def test_resumes_part_file(tmp_path: Path) -> None:
    record = make_record("SRR1")
    (tmp_path / "SRR1.part").write_bytes(b"he")
    commands = []

    def finish(command, check):
        commands.append(command)
        (tmp_path / "SRR1.part").write_bytes(b"hello")
        return subprocess.CompletedProcess(command, 0)

    result = download_one(record, tmp_path, "curl", run_command=finish)
    assert commands[0][commands[0].index("--continue-at") + 1] == "-"
    assert result.admission_method == "resumed_download"
    assert result.initial_partial_size == 2
    assert (tmp_path / "SRR1").read_bytes() == b"hello"


def test_promotes_exact_size_partial_after_md5_verification(tmp_path: Path) -> None:
    record = make_record("SRR1")
    (tmp_path / "SRR1.part").write_bytes(b"hello")

    def should_not_run(*args, **kwargs):
        raise AssertionError("curl must not run when exact-size partial is already valid")

    result = download_one(record, tmp_path, "curl", run_command=should_not_run)
    assert result.path == tmp_path / "SRR1"
    assert result.admission_method == "promoted_partial"
    assert result.initial_partial_size == 5
    assert (tmp_path / "SRR1").read_bytes() == b"hello"
    assert not (tmp_path / "SRR1.part").exists()


def test_download_one_computes_integrity_once_for_completed_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = make_record("SRR1")
    calls = 0

    original_describe = downloader_module.describe_file_integrity

    def counting_describe(path: Path):
        nonlocal calls
        calls += 1
        return original_describe(path)

    monkeypatch.setattr(downloader_module, "describe_file_integrity", counting_describe)

    def finish(command, check):
        (tmp_path / "SRR1.part").write_bytes(b"hello")
        return subprocess.CompletedProcess(command, 0)

    result = download_one(record, tmp_path, "curl", run_command=finish)

    assert result.admission_method == "downloaded_fresh"
    assert calls == 1


def test_quarantines_invalid_completed_file(tmp_path: Path) -> None:
    record = make_record("SRR1")
    (tmp_path / "SRR1").write_bytes(b"wrong")

    def finish(command, check):
        (tmp_path / "SRR1.part").write_bytes(b"hello")
        return subprocess.CompletedProcess(command, 0)

    result = download_one(record, tmp_path, "curl", run_command=finish, timestamp=lambda: 123.0)
    assert result.admission_method == "downloaded_fresh"
    assert result.initial_partial_size == 0
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


def test_download_batch_reports_successes_to_coordinator(tmp_path: Path) -> None:
    records = [make_record("SRR1"), make_record("SRR2")]
    successes = []

    def succeed(record, sra_dir, curl_path):
        return DownloadResult(
            path=sra_dir / record.run_accession,
            admission_method="downloaded_fresh",
            initial_partial_size=0,
            observed_size_bytes=record.sra_size_bytes,
            observed_md5=record.md5,
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )

    failures = download_batch(
        records,
        tmp_path / "sra",
        tmp_path / "logs",
        "curl",
        jobs=1,
        batch_attempts=1,
        download=succeed,
        sleep=lambda seconds: None,
        on_success=lambda record, result: successes.append((record.run_accession, result.admission_method)),
    )

    assert failures == []
    assert successes == [
        ("SRR1", "downloaded_fresh"),
        ("SRR2", "downloaded_fresh"),
    ]


def test_download_batch_surfaces_coordinator_errors(tmp_path: Path) -> None:
    records = [make_record("SRR1")]

    def succeed(record, sra_dir, curl_path):
        return DownloadResult(
            path=sra_dir / record.run_accession,
            admission_method="downloaded_fresh",
            initial_partial_size=0,
            observed_size_bytes=record.sra_size_bytes,
            observed_md5=record.md5,
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )

    with pytest.raises(RuntimeError, match="provenance write failed"):
        download_batch(
            records,
            tmp_path / "sra",
            tmp_path / "logs",
            "curl",
            jobs=1,
            batch_attempts=1,
            download=succeed,
            sleep=lambda seconds: None,
            on_success=lambda record, result: (_ for _ in ()).throw(RuntimeError("provenance write failed")),
        )


def test_native_new_destination_allows_scaffolding_directories(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    (tmp_path / "tmp").mkdir()

    assert _is_native_new_destination(tmp_path)


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


def test_download_command_parses_bioproject() -> None:
    args = build_parser().parse_args([
        "download",
        "input.tsv",
        "--outdir",
        "output",
        "--bioproject",
        "PRJNA000001",
    ])

    assert args.bioproject == "PRJNA000001"


def test_rejects_delete_sra_after_fastq_before_filesystem_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "\t".join(
            [
                "run_accession",
                "experiment_accession",
                "experiment_alias",
                "biosample",
                "sample_alias",
                "library_strategy",
                "library_source",
                "library_layout",
                "instrument_model",
                "total_bases",
                "total_spots",
                "sra_size_bytes",
                "md5",
                "url",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "SRR1",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "0",
                "0",
                "5",
                "5d41402abc4b2a76b9719d911017c592",
                "https://example.test/SRR1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    outdir = tmp_path / "output"
    args = build_parser().parse_args(
        [
            "download",
            str(manifest),
            "--outdir",
            str(outdir),
            "--mode",
            "fastq",
            "--delete-sra-after-fastq",
        ]
    )

    assert run_download(args) == 2
    assert capsys.readouterr().err == (
        "--delete-sra-after-fastq is incompatible with the v0.3 archival contract "
        "because SRA is the authoritative archived payload.\n"
    )
    assert not outdir.exists()


def test_download_requires_bioproject_for_new_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text("placeholder\n", encoding="utf-8")
    outdir = tmp_path / "output"
    args = build_parser().parse_args([
        "download",
        str(manifest),
        "--outdir",
        str(outdir),
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))

    assert run_download(args) == 2
    assert not outdir.exists()


def test_download_initializes_native_provenance_and_records_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text("placeholder\n", encoding="utf-8")
    outdir = tmp_path / "output"
    args = build_parser().parse_args([
        "download",
        str(manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    def fake_download_batch(records, sra_dir, logs_dir, curl_path, jobs, batch_attempts, *, download=None, sleep=None, on_success=None):
        result = DownloadResult(
            path=sra_dir / "SRR1",
            admission_method="downloaded_fresh",
            initial_partial_size=0,
            observed_size_bytes=5,
            observed_md5="5d41402abc4b2a76b9719d911017c592",
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )
        if on_success is not None:
            on_success(records[0], result)
        return []

    monkeypatch.setattr("sra_bioproject.cli.download_batch", fake_download_batch)

    assert run_download(args) == 0

    archive_metadata = archive.load_archive_metadata(outdir)
    assert archive_metadata["bioproject"] == "PRJNA000001"
    assert archive_metadata["origin"] == "native"

    admissions = archive.load_admission_records(outdir)
    assert len(admissions) == 1
    assert admissions[0]["admission_method"] == "downloaded_fresh"
    assert admissions[0]["accession"] == "SRR1"
    assert admissions[0]["relative_path"] == "sra/SRR1"
    assert admissions[0]["admitted_by_application"] == archive.APPLICATION_NAME
    assert admissions[0]["expected_md5"] == "5d41402abc4b2a76b9719d911017c592"
    assert (outdir / "manifest.tsv").is_file()


def test_download_treats_precreated_empty_directory_as_new_native_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text("placeholder\n", encoding="utf-8")
    outdir = tmp_path / "output"
    outdir.mkdir()
    args = build_parser().parse_args([
        "download",
        str(manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    def fake_download_batch(records, sra_dir, logs_dir, curl_path, jobs, batch_attempts, *, download=None, sleep=None, on_success=None):
        result = DownloadResult(
            path=sra_dir / "SRR1",
            admission_method="downloaded_fresh",
            initial_partial_size=0,
            observed_size_bytes=5,
            observed_md5="5d41402abc4b2a76b9719d911017c592",
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )
        if on_success is not None:
            on_success(records[0], result)
        return []

    monkeypatch.setattr("sra_bioproject.cli.download_batch", fake_download_batch)

    assert run_download(args) == 0
    assert archive.load_archive_metadata(outdir)["origin"] == "native"


def test_download_does_not_append_duplicate_existing_admission_on_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text("placeholder\n", encoding="utf-8")
    outdir = tmp_path / "output"
    args = build_parser().parse_args([
        "download",
        str(manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    def fake_download_batch(records, sra_dir, logs_dir, curl_path, jobs, batch_attempts, *, download=None, sleep=None, on_success=None):
        result = DownloadResult(
            path=sra_dir / "SRR1",
            admission_method="existing",
            initial_partial_size=0,
            observed_size_bytes=5,
            observed_md5="5d41402abc4b2a76b9719d911017c592",
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )
        if on_success is not None:
            on_success(records[0], result)
        return []

    monkeypatch.setattr("sra_bioproject.cli.download_batch", fake_download_batch)

    assert run_download(args) == 0
    assert run_download(args) == 0
    admissions = archive.load_admission_records(outdir)
    assert len(admissions) == 1
    assert admissions[0]["admission_method"] == "existing"


def test_download_does_not_append_duplicate_admission_after_fresh_then_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text("placeholder\n", encoding="utf-8")
    outdir = tmp_path / "output"
    args = build_parser().parse_args([
        "download",
        str(manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    results = iter([
        DownloadResult(
            path=outdir / "sra" / "SRR1",
            admission_method="downloaded_fresh",
            initial_partial_size=0,
            observed_size_bytes=5,
            observed_md5="5d41402abc4b2a76b9719d911017c592",
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        ),
        DownloadResult(
            path=outdir / "sra" / "SRR1",
            admission_method="existing",
            initial_partial_size=0,
            observed_size_bytes=5,
            observed_md5="5d41402abc4b2a76b9719d911017c592",
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        ),
    ])

    def fake_download_batch(records, sra_dir, logs_dir, curl_path, jobs, batch_attempts, *, download=None, sleep=None, on_success=None):
        result = next(results)
        if on_success is not None:
            on_success(records[0], result)
        return []

    monkeypatch.setattr("sra_bioproject.cli.download_batch", fake_download_batch)

    assert run_download(args) == 0
    assert run_download(args) == 0
    admissions = archive.load_admission_records(outdir)
    assert len(admissions) == 1
    assert admissions[0]["admission_method"] == "downloaded_fresh"


def test_download_preserves_reacquisition_history_for_same_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text("placeholder\n", encoding="utf-8")
    outdir = tmp_path / "output"
    args = build_parser().parse_args([
        "download",
        str(manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    results = iter([
        DownloadResult(
            path=outdir / "sra" / "SRR1",
            admission_method="downloaded_fresh",
            initial_partial_size=0,
            observed_size_bytes=5,
            observed_md5="5d41402abc4b2a76b9719d911017c592",
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        ),
        DownloadResult(
            path=outdir / "sra" / "SRR1",
            admission_method="downloaded_fresh",
            initial_partial_size=0,
            observed_size_bytes=5,
            observed_md5="5d41402abc4b2a76b9719d911017c592",
            observed_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        ),
    ])

    def fake_download_batch(records, sra_dir, logs_dir, curl_path, jobs, batch_attempts, *, download=None, sleep=None, on_success=None):
        result = next(results)
        if on_success is not None:
            on_success(records[0], result)
        return []

    monkeypatch.setattr("sra_bioproject.cli.download_batch", fake_download_batch)

    assert run_download(args) == 0
    assert run_download(args) == 0
    admissions = archive.load_admission_records(outdir)
    assert len(admissions) == 2
    assert admissions[0]["admission_method"] == "downloaded_fresh"
    assert admissions[1]["admission_method"] == "downloaded_fresh"


def test_download_rolls_back_manifest_when_archive_metadata_init_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text("placeholder\n", encoding="utf-8")
    outdir = tmp_path / "output"
    args = build_parser().parse_args([
        "download",
        str(manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    def fake_download_batch(records, sra_dir, logs_dir, curl_path, jobs, batch_attempts, *, download=None, sleep=None, on_success=None):
        return []

    monkeypatch.setattr("sra_bioproject.cli.download_batch", fake_download_batch)
    original_write_archive_metadata = archive.write_archive_metadata

    call_count = 0

    def fail_archive_write_once(project_dir: Path, metadata):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("forced archive metadata failure")
        return original_write_archive_metadata(project_dir, metadata)

    monkeypatch.setattr("sra_bioproject.cli.archive_module.write_archive_metadata", fail_archive_write_once)

    with pytest.raises(OSError, match="forced archive metadata failure"):
        run_download(args)

    assert not (outdir / "manifest.tsv").exists()
    assert not (outdir / "provenance").exists()
    assert (outdir / "sra").exists()

    assert run_download(args) == 0
    assert archive.load_archive_metadata(outdir)["origin"] == "native"


def test_download_refuses_unclassified_destination_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text("placeholder\n", encoding="utf-8")
    outdir = tmp_path / "output"
    outdir.mkdir()
    (outdir / "unexpected.txt").write_text("occupied\n", encoding="utf-8")

    args = build_parser().parse_args([
        "download",
        str(manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    assert run_download(args) == 2
    assert not (outdir / "logs").exists()
    assert not (outdir / "sra").exists()
    assert not (outdir / "tmp").exists()


def test_legacy_fastq_download_continues_after_legacy_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outdir = tmp_path / "output"
    outdir.mkdir()
    write_manifest([make_record("SRR1")], outdir / "manifest.tsv")
    sra_dir = outdir / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")
    input_manifest = tmp_path / "input.tsv"
    input_manifest.write_text("placeholder\n", encoding="utf-8")
    args = build_parser().parse_args([
        "download",
        str(input_manifest),
        "--outdir",
        str(outdir),
        "--mode",
        "fastq",
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)
    monkeypatch.setattr("sra_bioproject.cli.download_batch", lambda *args, **kwargs: [])
    monkeypatch.setattr("sra_bioproject.cli.verify_project", lambda *args, **kwargs: 0)
    monkeypatch.setattr("sra_bioproject.cli.validate_vdb", lambda *args, **kwargs: None)
    monkeypatch.setattr("sra_bioproject.cli.fastq_complete", lambda *args, **kwargs: False)
    conversions = []
    monkeypatch.setattr("sra_bioproject.cli.convert_one", lambda record, sra_path, fastq_dir, tmp_dir, threads, fasterq_dump, pigz_path, gzip_path, delete_sra_after_fastq: conversions.append(record.run_accession))

    assert run_download(args) == 0
    assert conversions == ["SRR1"]


def test_legacy_download_bootstraps_archive_after_complete_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outdir = tmp_path / "output"
    outdir.mkdir()
    write_manifest([make_record("SRR1")], outdir / "manifest.tsv")
    sra_dir = outdir / "sra"
    sra_dir.mkdir()
    (sra_dir / "SRR1").write_bytes(b"hello")
    input_manifest = tmp_path / "input.tsv"
    input_manifest.write_text("placeholder\n", encoding="utf-8")
    args = build_parser().parse_args([
        "download",
        str(input_manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    def fake_download_batch(records, sra_dir, logs_dir, curl_path, jobs, batch_attempts, *, download=None, sleep=None, on_success=None):
        return []

    monkeypatch.setattr("sra_bioproject.cli.download_batch", fake_download_batch)

    assert run_download(args) == 0
    assert archive.load_archive_metadata(outdir)["origin"] == "legacy"


def test_failing_legacy_download_leaves_archive_unmanaged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outdir = tmp_path / "output"
    outdir.mkdir()
    write_manifest([make_record("SRR1")], outdir / "manifest.tsv")
    input_manifest = tmp_path / "input.tsv"
    input_manifest.write_text("placeholder\n", encoding="utf-8")
    args = build_parser().parse_args([
        "download",
        str(input_manifest),
        "--outdir",
        str(outdir),
        "--bioproject",
        "PRJNA000001",
    ])

    monkeypatch.setattr("sra_bioproject.cli.load_records", lambda path, input_format: ([make_record("SRR1")], "tsv"))
    monkeypatch.setattr("sra_bioproject.cli.check_command", lambda name, required=True: name)

    def fake_download_batch(records, sra_dir, logs_dir, curl_path, jobs, batch_attempts, *, download=None, sleep=None, on_success=None):
        return [records[0]]

    monkeypatch.setattr("sra_bioproject.cli.download_batch", fake_download_batch)

    assert run_download(args) == 1
    assert not (outdir / "provenance").exists()