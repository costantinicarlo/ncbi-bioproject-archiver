"""Data models shared by parsers, manifests, and download workflows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunRecord:
    run_accession: str
    experiment_accession: str
    experiment_alias: str
    biosample: str
    sample_alias: str
    library_strategy: str
    library_source: str
    library_layout: str
    instrument_model: str
    total_bases: int
    total_spots: int
    sra_size_bytes: int
    md5: str
    url: str
