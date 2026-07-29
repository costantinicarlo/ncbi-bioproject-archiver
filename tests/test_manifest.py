from pathlib import Path

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
    write_manifest(parse_xml(EXAMPLE_DIR / "NCBI_PRJNA831841.xml"), generated)
    assert generated.read_bytes() == (EXAMPLE_DIR / "PRJNA831841_manifest.tsv").read_bytes()
