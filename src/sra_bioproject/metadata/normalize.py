"""Normalize a preserved raw metadata directory into stable products."""

from __future__ import annotations

import csv
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable

from ..manifest import write_manifest
from ..xml_parser import parse_xml
from .models import PublicationRecord
from .parsers import deduplicate_publications, parse_bioproject, parse_biosamples, parse_europe_pmc, parse_links, parse_publications
from .schemas import LINKED_RESOURCE_COLUMNS, PROJECT_SCHEMA_VERSION, PUBLICATION_COLUMNS, RELATIONSHIP_COLUMNS, RUN_COLUMNS, SAMPLE_ATTRIBUTE_COLUMNS, SAMPLE_COLUMNS, TSV_SCHEMA_VERSION


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_tsv(path: Path, columns: tuple[str, ...], rows: Iterable[dict[str, object]]) -> int:
    temporary = path.with_name(f".{path.name}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    os.replace(temporary, path)
    return count


def normalize(metadata_dir: Path, manifest_path: Path | None = None, retrieved_at: str = "") -> tuple[str, dict[str, object]]:
    raw = metadata_dir / "raw"
    derived = metadata_dir / "derived"
    project = parse_bioproject((raw / "bioproject.xml").read_bytes())
    samples = parse_biosamples((raw / "biosamples.xml").read_bytes())
    runs = parse_xml(raw / "sra_experiments.xml")
    links = parse_links((raw / "entrez_links.xml").read_bytes(), project.accession)
    publications: list[PublicationRecord] = []
    if (raw / "pubmed.xml").exists():
        publications += parse_publications((raw / "pubmed.xml").read_bytes(), "ncbi_bioproject_link", "curated_link")
    if (raw / "pmc.xml").exists():
        publications += parse_publications((raw / "pmc.xml").read_bytes(), "ncbi_pmc_link", "database_link")
    if (raw / "europe_pmc.json").exists():
        publications += parse_europe_pmc((raw / "europe_pmc.json").read_bytes(), project.accession)
    publications = deduplicate_publications(publications)
    project_data = asdict(project)
    project_data.update({"schema_version": PROJECT_SCHEMA_VERSION, "retrieved_at": retrieved_at or None,
                         "parent_projects": [item.target_accession or item.target_uid for item in links if item.relationship == "bioproject_d2u"],
                         "child_projects": [item.target_accession or item.target_uid for item in links if item.relationship == "bioproject_u2d"]})
    atomic_write(derived / "project.json", (json.dumps(project_data, indent=2, sort_keys=True) + "\n").encode())
    common = {attribute.name.lower().replace(" ", "_"): attribute.value for sample in samples for attribute in sample.attributes}
    sample_rows = []
    attribute_rows = []
    for sample in samples:
        attrs = {attribute.harmonized_name or attribute.name: attribute.value for attribute in sample.attributes}
        sample_rows.append({"bioproject": project.accession, "biosample": sample.accession, "sample_name": sample.sample_name, "title": sample.title, "organism": sample.organism, "taxid": sample.taxid, "package": sample.package, **attrs})
        attribute_rows.extend({"bioproject": project.accession, "biosample": sample.accession, "attribute_name": item.name, "attribute_value": item.value, "attribute_harmonized_name": item.harmonized_name} for item in sample.attributes)
    counts = {"samples": write_tsv(derived / "samples.tsv", SAMPLE_COLUMNS, sample_rows),
              "sample_attributes": write_tsv(derived / "sample_attributes.tsv", SAMPLE_ATTRIBUTE_COLUMNS, attribute_rows)}
    run_rows = [{"bioproject": project.accession, **asdict(run)} for run in runs]
    counts["runs"] = write_tsv(derived / "runs.tsv", RUN_COLUMNS, run_rows)
    counts["publications"] = write_tsv(derived / "publications.tsv", PUBLICATION_COLUMNS, ({"bioproject": project.accession, **asdict(item), "authors": "; ".join(item.authors)} for item in publications))
    counts["relationships"] = write_tsv(derived / "relationships.tsv", RELATIONSHIP_COLUMNS, (asdict(item) for item in links))
    principal = {"bioproject", "biosample", "sra", "pubmed", "pmc", "assembly"}
    counts["linked_resources"] = write_tsv(derived / "linked_resources.tsv", LINKED_RESOURCE_COLUMNS, ({"bioproject": project.accession, **asdict(item)} for item in links if item.target_database not in principal))
    if manifest_path is not None:
        write_manifest(runs, manifest_path)
    counts["tsv_schema_version"] = TSV_SCHEMA_VERSION
    return project.accession, counts