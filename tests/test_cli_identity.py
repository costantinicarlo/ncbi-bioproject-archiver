import pytest

from sra_bioproject.cli import build_parser, entrypoint, legacy_entrypoint


def test_parser_uses_canonical_program_name() -> None:
    assert build_parser().prog == "ncbi-bioproject"


def test_legacy_entrypoint_warns_once(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sra_bioproject.cli.main", lambda argv=None: 0)

    with pytest.raises(SystemExit) as exc:
        legacy_entrypoint()

    assert exc.value.code == 0
    assert capsys.readouterr().err == (
        "Warning: 'sra-bioproject' is a legacy command name. Use 'ncbi-bioproject' instead.\n"
    )


def test_canonical_entrypoint_does_not_warn(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sra_bioproject.cli.main", lambda argv=None: 0)

    with pytest.raises(SystemExit) as exc:
        entrypoint()

    assert exc.value.code == 0
    assert capsys.readouterr().err == ""