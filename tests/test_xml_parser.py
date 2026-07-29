from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from sra_bioproject.xml_parser import parse_xml

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_sra_export.xml"


def test_parses_metadata_and_sorts_runs() -> None:
    records = parse_xml(FIXTURE)

    assert [record.run_accession for record in records] == ["SRR000001", "SRR000002"]
    first = records[0]
    assert (first.experiment_accession, first.experiment_alias) == ("SRX000001", "Library A")
    assert (first.biosample, first.sample_alias) == ("SAMN000001", "Sample A")
    assert (first.library_strategy, first.library_source) == ("WGS", "GENOMIC")
    assert (first.library_layout, first.instrument_model) == ("PAIRED", "Sequel II")
    assert (first.total_bases, first.total_spots, first.sra_size_bytes) == (100, 10, 5)


def test_selects_normalized_instead_of_lite() -> None:
    second = parse_xml(FIXTURE)[1]
    assert second.url == "https://example.test/SRR000002"
    assert not second.url.endswith(".lite")


def modified_xml(tmp_path: Path, modify) -> Path:
    tree = ET.parse(FIXTURE)
    modify(tree.getroot())
    path = tmp_path / "modified.xml"
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def test_rejects_duplicate_accessions(tmp_path: Path) -> None:
    def duplicate(root: ET.Element) -> None:
        runs = root.findall("./EXPERIMENT_PACKAGE/RUN_SET/RUN")
        runs[1].set("accession", runs[0].get("accession", ""))

    with pytest.raises(ValueError, match="Duplicate run accessions"):
        parse_xml(modified_xml(tmp_path, duplicate))


def test_rejects_missing_normalized_file(tmp_path: Path) -> None:
    def remove_normalized(root: ET.Element) -> None:
        files = root.findall("./EXPERIMENT_PACKAGE/RUN_SET/RUN/SRAFiles/SRAFile")
        for sra_file in files:
            if sra_file.get("semantic_name") == "SRA Normalized":
                sra_file.set("semantic_name", "SRA Lite")

    with pytest.raises(ValueError, match="expected one SRA Normalized file"):
        parse_xml(modified_xml(tmp_path, remove_normalized))


def test_rejects_missing_http_url(tmp_path: Path) -> None:
    def remove_http(root: ET.Element) -> None:
        sra_file = root.find("./EXPERIMENT_PACKAGE/RUN_SET/RUN/SRAFiles/SRAFile[@semantic_name='SRA Normalized']")
        assert sra_file is not None
        sra_file.attrib.pop("url", None)
        for alternative in list(sra_file):
            alternative.set("url", "s3://example/no-http")

    with pytest.raises(ValueError, match=r"no HTTP\(S\) URL"):
        parse_xml(modified_xml(tmp_path, remove_http))


@pytest.mark.parametrize("field", ["total_bases", "total_spots"])
def test_rejects_malformed_run_numbers(tmp_path: Path, field: str) -> None:
    def corrupt(root: ET.Element) -> None:
        run = root.find("./EXPERIMENT_PACKAGE/RUN_SET/RUN")
        assert run is not None
        run.set(field, "not-a-number")

    with pytest.raises(ValueError, match=f"malformed numeric field {field}"):
        parse_xml(modified_xml(tmp_path, corrupt))


def test_rejects_no_runs(tmp_path: Path) -> None:
    path = tmp_path / "empty.xml"
    path.write_text("<EXPERIMENT_PACKAGE_SET />", encoding="utf-8")
    with pytest.raises(ValueError, match="No runs"):
        parse_xml(path)
