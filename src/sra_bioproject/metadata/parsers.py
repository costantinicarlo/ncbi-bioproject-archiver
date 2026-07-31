"""Parse preserved NCBI and Europe PMC responses into typed records."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from .models import BioProjectRecord, BioSampleRecord, ProjectRelationship, PublicationRecord, SampleAttribute


def _first(root: ET.Element, paths: tuple[str, ...]) -> str:
    for path in paths:
        element = root.find(path)
        if element is not None and element.text and element.text.strip():
            return element.text.strip()
    return ""


def parse_bioproject(content: bytes) -> BioProjectRecord:
    root = ET.fromstring(content)
    project = root.find(".//Project") or root
    accession = project.get("accession", "") or _first(root, (".//ProjectID/ArchiveID", ".//ArchiveID"))
    archive = root.find(".//ArchiveID")
    submission = root.find(".//Submission")
    if archive is not None:
        accession = archive.get("accession", accession)
    if not accession:
        raise ValueError("BioProject response has no accession")
    organism = root.find(".//Organism")
    data_types = tuple(sorted({element.text.strip() for element in root.findall(".//DataType") if element.text and element.text.strip()}))
    return BioProjectRecord(
        accession=accession, entrez_uid=(archive.get("id", "") if archive is not None else ""),
        title=_first(root, (".//ProjectDescr/Title", ".//Title")),
        description=_first(root, (".//ProjectDescr/Description", ".//Description")),
        organism=(organism.get("species", "") if organism is not None else ""),
        taxid=(organism.get("taxID", "") if organism is not None else ""),
        project_type=_first(root, (".//ProjectType/*/ProjectType", ".//ProjectType")),
        project_scope=_first(root, (".//ProjectType/*/ProjectScope", ".//ProjectScope")),
        data_types=data_types,
        submitter_organization=_first(root, (".//Submission/Organization/Name", ".//Organization/Name")),
        submission_accession=(submission.get("accession", "") if submission is not None else ""),
        registration_date=(archive.get("registration_date", "") if archive is not None else ""),
        release_date=(archive.get("release_date", "") if archive is not None else ""),
        last_update=(archive.get("last_update", "") if archive is not None else ""),
    )


def parse_biosamples(content: bytes) -> list[BioSampleRecord]:
    root = ET.fromstring(content)
    records = []
    for sample in root.findall(".//BioSample"):
        accession = sample.get("accession", "")
        if not accession:
            continue
        organism = sample.find("./Description/Organism")
        attributes = tuple(SampleAttribute(
            attribute.get("attribute_name", ""), (attribute.text or "").strip(),
            attribute.get("harmonized_name", ""),
        ) for attribute in sample.findall("./Attributes/Attribute"))
        records.append(BioSampleRecord(
            accession=accession,
            sample_name=_first(sample, ("./Ids/Id[@db_label='Sample name']", "./Description/SampleName")),
            title=_first(sample, ("./Description/Title",)),
            organism=(organism.get("taxonomy_name", "") if organism is not None else ""),
            taxid=(organism.get("taxonomy_id", "") if organism is not None else ""),
            package=sample.get("package", ""), attributes=attributes,
        ))
    return sorted(records, key=lambda item: item.accession)


def parse_publications(content: bytes, source: str, confidence: str) -> list[PublicationRecord]:
    root = ET.fromstring(content)
    records = []
    for article in root.findall(".//PubmedArticle") + root.findall(".//article"):
        pmid = _first(article, (".//PMID", ".//article-id[@pub-id-type='pmid']"))
        pmcid = _first(article, (".//ArticleId[@IdType='pmc']", ".//article-id[@pub-id-type='pmcid']"))
        doi = _first(article, (".//ArticleId[@IdType='doi']", ".//article-id[@pub-id-type='doi']"))
        title = "".join((article.findtext(".//ArticleTitle") or article.findtext(".//article-title") or "").splitlines()).strip()
        authors = []
        for author in article.findall(".//Author"):
            name = " ".join(filter(None, [_first(author, ("./ForeName",)), _first(author, ("./LastName",))]))
            if name:
                authors.append(name)
        records.append(PublicationRecord(
            pmid=pmid, pmcid=pmcid, doi=doi.lower(), title=title,
            journal=_first(article, (".//Journal/Title", ".//journal-title")),
            publication_date=_first(article, (".//PubDate/Year", ".//pub-date/year")),
            authors=tuple(authors), association_source=source,
            association_confidence=confidence, evidence=source,
        ))
    return records


def parse_europe_pmc(content: bytes, accession: str) -> list[PublicationRecord]:
    payload = json.loads(content.decode("utf-8"))
    records = []
    for item in payload.get("resultList", {}).get("result", []):
        records.append(PublicationRecord(
            pmid=str(item.get("pmid", "")), pmcid=item.get("pmcid", ""),
            doi=item.get("doi", "").lower(), title=item.get("title", ""),
            journal=item.get("journalTitle", ""), publication_date=item.get("firstPublicationDate", ""),
            authors=tuple(part.strip() for part in item.get("authorString", "").rstrip(".").split(",") if part.strip()),
            association_source="europe_pmc_accession_search", association_confidence="text_discovered",
            evidence=f'accession query: "{accession}"',
        ))
    return records


def deduplicate_publications(records: list[PublicationRecord]) -> list[PublicationRecord]:
    selected = {}
    for record in records:
        key = ("pmid", record.pmid) if record.pmid else (("pmcid", record.pmcid) if record.pmcid else (("doi", record.doi) if record.doi else ("title", re.sub(r"\W+", "", record.title.lower()))))
        if key[1] and key not in selected:
            selected[key] = record
    return sorted(selected.values(), key=lambda item: (item.pmid, item.pmcid, item.doi, item.title))


def parse_links(content: bytes, source_accession: str) -> list[ProjectRelationship]:
    root = ET.fromstring(content)
    records = []
    for group in root.findall(".//LinkSetDb"):
        linkname = _first(group, ("./LinkName",))
        database = _first(group, ("./DbTo",))
        relationship = linkname.removeprefix("bioproject_") if hasattr(str, "removeprefix") else linkname.replace("bioproject_", "", 1)
        for element in group.findall("./Link/Id"):
            records.append(ProjectRelationship(source_accession, relationship, database, target_uid=(element.text or "").strip()))
    return records