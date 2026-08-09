"""Parse NCBI SRA XML exports into normalized run records."""

from __future__ import annotations

from defusedxml import ElementTree as ET
from pathlib import Path

from .models import RunRecord
from .validation import validate_md5, validate_run_accession


def _text(element: ET.Element | None, path: str) -> str:
    if element is None:
        return ""
    value = element.findtext(path)
    return value.strip() if value else ""


def _required_integer(value: str | None, accession: str, field: str, minimum: int = 0) -> int:
    if value is None or not value.strip():
        raise ValueError(f"{accession}: missing required numeric field {field}")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{accession}: malformed numeric field {field}: {value!r}") from exc
    if parsed < minimum:
        comparator = "positive" if minimum == 1 else f">= {minimum}"
        raise ValueError(f"{accession}: numeric field {field} must be {comparator}")
    return parsed


def _http_url(sra_file: ET.Element, accession: str) -> str:
    candidates = [sra_file.get("url", "")]
    candidates.extend(
        alternative.get("url", "")
        for alternative in sra_file.findall("./Alternatives")
    )
    for candidate in candidates:
        url = candidate.strip()
        if url.startswith(("https://", "http://")):
            return url
    raise ValueError(f"{accession}: no HTTP(S) URL for SRA Normalized file")


def _library_layout(experiment: ET.Element | None) -> str:
    if experiment is None:
        return ""
    layout = experiment.find("./DESIGN/LIBRARY_DESCRIPTOR/LIBRARY_LAYOUT")
    if layout is None or not list(layout):
        return ""
    return list(layout)[0].tag.rsplit("}", 1)[-1]


def _instrument_model(experiment: ET.Element | None) -> str:
    if experiment is None:
        return ""
    for platform in experiment.findall("./PLATFORM/*"):
        model = _text(platform, "./INSTRUMENT_MODEL")
        if model:
            return model
    return ""


def parse_xml(xml_path: Path | str) -> list[RunRecord]:
    root = ET.parse(xml_path).getroot()
    records: list[RunRecord] = []

    packages = [root] if root.tag.rsplit("}", 1)[-1] == "EXPERIMENT_PACKAGE" else root.findall("./EXPERIMENT_PACKAGE")
    for package in packages:
        experiment = package.find("./EXPERIMENT")
        sample = package.find("./SAMPLE")
        descriptor = experiment.find("./DESIGN/LIBRARY_DESCRIPTOR") if experiment is not None else None

        experiment_accession = experiment.get("accession", "").strip() if experiment is not None else ""
        experiment_alias = experiment.get("alias", "").strip() if experiment is not None else ""
        if not experiment_alias:
            experiment_alias = _text(descriptor, "./LIBRARY_NAME")

        biosample = ""
        if sample is not None:
            external = sample.find("./IDENTIFIERS/EXTERNAL_ID[@namespace='BioSample']")
            biosample = (external.text or "").strip() if external is not None else ""
            if not biosample:
                biosample = sample.get("accession", "").strip()

        for run in package.findall("./RUN_SET/RUN"):
            accession = run.get("accession", "").strip()
            if not accession:
                raise ValueError("A RUN element lacks an accession")
            accession = validate_run_accession(accession)

            normalized = [
                sra_file
                for sra_file in run.findall("./SRAFiles/SRAFile")
                if sra_file.get("semantic_name") == "SRA Normalized"
                and sra_file.get("supertype") == "Primary ETL"
            ]
            if len(normalized) != 1:
                raise ValueError(
                    f"{accession}: expected one SRA Normalized file, found {len(normalized)}"
                )
            sra_file = normalized[0]

            records.append(
                RunRecord(
                    run_accession=accession,
                    experiment_accession=experiment_accession,
                    experiment_alias=experiment_alias,
                    biosample=biosample,
                    sample_alias=sample.get("alias", "").strip() if sample is not None else "",
                    library_strategy=_text(descriptor, "./LIBRARY_STRATEGY"),
                    library_source=_text(descriptor, "./LIBRARY_SOURCE"),
                    library_selection=_text(descriptor, "./LIBRARY_SELECTION"),
                    library_layout=_library_layout(experiment),
                    instrument_model=_instrument_model(experiment),
                    total_bases=_required_integer(run.get("total_bases"), accession, "total_bases"),
                    total_spots=_required_integer(run.get("total_spots"), accession, "total_spots"),
                    sra_size_bytes=_required_integer(sra_file.get("size"), accession, "sra_size_bytes", minimum=1),
                    md5=validate_md5(sra_file.get("md5", ""), accession),
                    url=_http_url(sra_file, accession),
                )
            )

    if not records:
        raise ValueError("No runs were found in the SRA XML export")

    accessions = [record.run_accession for record in records]
    if len(accessions) != len(set(accessions)):
        raise ValueError("Duplicate run accessions found in XML")
    return sorted(records, key=lambda record: record.run_accession)
