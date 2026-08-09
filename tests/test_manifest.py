from pathlib import Path
import re

import pytest

from sra_bioproject.manifest import MANIFEST_COLUMNS, read_manifest, write_manifest
from sra_bioproject.xml_parser import parse_xml

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_sra_export.xml"
EXAMPLE_DIR = Path(__file__).parents[1] / "examples" / "PRJNA831841"


def test_manifest_headers_and_values(tmp_path: Path) -> None:
    path = tmp_path / "manifest.tsv"
    write_manifest(parse_xml(FIXTURE), path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == list(MANIFEST_COLUMNS)
    records = read_manifest(path)
    assert records[0].experiment_accession == "SRX000001"
    assert records[0].biosample == "SAMN000001"
    assert records[1].library_strategy == "RNA-Seq"


def test_committed_example_manifest_is_current(tmp_path: Path) -> None:
    generated = tmp_path / "manifest.tsv"
    records = parse_xml(EXAMPLE_DIR / "NCBI_PRJNA831841.xml")
    write_manifest(records, generated)
    assert len(records) == 187
    assert all("lite" not in record.url.lower() for record in records)
    assert generated.read_bytes() == (EXAMPLE_DIR / "PRJNA831841_manifest.tsv").read_bytes()


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("run_accession", "../../escape", "Invalid run accession"),
        ("sra_size_bytes", "0", "must be positive"),
        ("md5", "", "must be exactly 32 hexadecimal characters"),
    ],
)
def test_manifest_rejects_invalid_integrity_or_accession_fields(
    tmp_path: Path,
    column: str,
    value: str,
    message: str,
) -> None:
    generated = tmp_path / "manifest.tsv"
    write_manifest(parse_xml(FIXTURE), generated)
    lines = generated.read_text(encoding="utf-8").splitlines()
    headers = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    row = rows[0]
    row[headers.index(column)] = value
    lines[1] = "\t".join(row)
    generated.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(message)):
        read_manifest(generated)
