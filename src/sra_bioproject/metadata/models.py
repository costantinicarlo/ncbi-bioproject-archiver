"""Typed records used by metadata parsers and serializers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SampleAttribute:
    name: str
    value: str
    harmonized_name: str = ""


@dataclass(frozen=True)
class BioSampleRecord:
    accession: str
    sample_name: str = ""
    title: str = ""
    organism: str = ""
    taxid: str = ""
    package: str = ""
    attributes: tuple[SampleAttribute, ...] = ()


@dataclass(frozen=True)
class BioProjectRecord:
    accession: str
    entrez_uid: str = ""
    title: str = ""
    description: str = ""
    organism: str = ""
    taxid: str = ""
    project_type: str = ""
    project_scope: str = ""
    data_types: tuple[str, ...] = ()
    submitter_organization: str = ""
    submission_accession: str = ""
    registration_date: str = ""
    release_date: str = ""
    last_update: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicationRecord:
    pmid: str = ""
    pmcid: str = ""
    doi: str = ""
    title: str = ""
    journal: str = ""
    publication_date: str = ""
    authors: tuple[str, ...] = ()
    association_source: str = ""
    association_confidence: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class ProjectRelationship:
    source_accession: str
    relationship: str
    target_database: str
    target_accession: str = ""
    target_uid: str = ""
    evidence_source: str = "ncbi_elink"


@dataclass(frozen=True)
class RawResponseRecord:
    filename: str
    content: bytes
    database: str
    operation: str
    content_type: str
    query: str = ""
    linkname: str = ""
    status: int = 200
    retrieved_at: str = ""