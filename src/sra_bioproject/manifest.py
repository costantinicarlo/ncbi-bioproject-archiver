"""Read and write deterministic TSV manifests."""

from __future__ import annotations

import csv
from pathlib import Path
import sys
from typing import Iterable, TextIO

from .models import RunRecord
from .validation import validate_md5, validate_run_accession

MANIFEST_COLUMNS = (
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
)


def _integer(row: dict[str, str], field: str, accession: str) -> int:
    value = row.get(field, "").strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{accession}: malformed manifest field {field}: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{accession}: manifest field {field} must not be negative")
    return parsed


def _record_from_row(row: dict[str, str], row_number: int) -> RunRecord:
    accession = row.get("run_accession", "").strip()
    if not accession:
        raise ValueError(f"Manifest row {row_number} has no run_accession")
    accession = validate_run_accession(accession)
    url = row.get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"{accession}: manifest has no usable HTTP(S) URL")
    sra_size_bytes = _integer(row, "sra_size_bytes", accession)
    if sra_size_bytes <= 0:
        raise ValueError(f"{accession}: manifest field sra_size_bytes must be positive")
    return RunRecord(
        run_accession=accession,
        experiment_accession=row.get("experiment_accession", "").strip(),
        experiment_alias=row.get("experiment_alias", "").strip(),
        biosample=row.get("biosample", "").strip(),
        sample_alias=row.get("sample_alias", "").strip(),
        library_strategy=row.get("library_strategy", "").strip(),
        library_source=row.get("library_source", "").strip(),
        library_layout=row.get("library_layout", "").strip(),
        instrument_model=row.get("instrument_model", "").strip(),
        total_bases=_integer(row, "total_bases", accession),
        total_spots=_integer(row, "total_spots", accession),
        sra_size_bytes=sra_size_bytes,
        md5=validate_md5(row.get("md5", ""), accession),
        url=url,
    )


def read_manifest(path: Path | str) -> list[RunRecord]:
    with Path(path).open("r", encoding="utf-8", newline="") as file_handle:
        reader = csv.DictReader(file_handle, delimiter="\t")
        missing = [column for column in MANIFEST_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Manifest is missing required columns: {', '.join(missing)}")
        records = [
            _record_from_row(row, row_number)
            for row_number, row in enumerate(reader, start=2)
        ]
    if not records:
        raise ValueError("Manifest contains no runs")
    accessions = [record.run_accession for record in records]
    if len(accessions) != len(set(accessions)):
        raise ValueError("Duplicate run accessions found in manifest")
    return sorted(records, key=lambda record: record.run_accession)


def _write(records: Iterable[RunRecord], file_handle: TextIO) -> None:
    writer = csv.DictWriter(
        file_handle,
        fieldnames=MANIFEST_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for record in sorted(records, key=lambda item: item.run_accession):
        writer.writerow({column: getattr(record, column) for column in MANIFEST_COLUMNS})


def write_manifest(records: Iterable[RunRecord], output: Path | str) -> None:
    if str(output) == "-":
        _write(records, sys.stdout)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file_handle:
        _write(records, file_handle)
