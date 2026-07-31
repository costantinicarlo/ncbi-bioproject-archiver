# Design

## Responsibilities

XML parsing is separate from download execution so NCBI metadata can be tested, reviewed, and converted without network access. `xml_parser.py` selects one `SRA Normalized`/`Primary ETL` object per run. `manifest.py` serializes those records. `downloader.py` handles local files and curl. `fastq.py` owns optional conversion, and `cli.py` coordinates them.

The manifest is a durable provenance artifact: it records exactly which run URL, size, checksum, BioSample, experiment, library, and platform metadata drove a download. Its stable UTF-8 TSV columns are:

```text
run_accession experiment_accession experiment_alias biosample sample_alias
library_strategy library_source library_layout instrument_model total_bases
total_spots sra_size_bytes md5 url
```

The physical separator is a tab and records are sorted by run accession.

## Integrity And Recovery

Size catches truncation cheaply; MD5 verifies content. Both are checked when available because neither a filename nor a successful process exit proves that the expected object is complete. Curl writes to `<accession>.part`; only a verified part is atomically renamed to the final filename. An invalid final file is retained as `.bad.<timestamp>` for diagnosis.

One run's failure is collected rather than propagated through the executor. Other futures finish, and later passes contain only failed runs. Persistent failures produce `logs/failed_accessions.txt` and a non-zero exit status.

FASTQ conversion is sequential because `fasterq-dump`, scratch I/O, and compression can multiply temporary storage and saturate a disk. Completion is represented by gzip-tested outputs plus a marker. SRA deletion is permitted only after that state is reached.

Runtime data (`sra/`, `fastq/`, `tmp/`, logs, partials, and quarantined files) are excluded from Git because they are large, mutable, and reproducible from the committed XML/manifest provenance.

Mounted-volume validation is macOS-specific and applies only to paths below `/Volumes`. XML/TSV parsing, ordinary paths, curl execution, checksums, retries, and FASTQ workflows are portable to Linux and macOS.

## Metadata Architecture

BioProject is the discovery hub, while BioSample carries heterogeneous sample provenance. Entrez ESearch resolves the accession, ESummary preserves the project record, and ELink discovers explicit relationships. The current link definitions are verified through EInfo and include `bioproject_biosample_all`, `bioproject_sra_all`, `bioproject_pubmed`, `bioproject_pmc`, `bioproject_assembly_all`, `bioproject_bioproject_d2u`, and `bioproject_bioproject_u2d`.

Raw responses are immutable evidence. Normalized products are reproducible interpretations of that evidence. `samples.tsv` provides stable common fields while `sample_attributes.tsv` preserves every original BioSample attribute in long form. `runs.tsv` and `manifest.tsv` derive from the same `RunRecord` objects; the manifest is the download-focused projection. Linked resources are inventoried rather than recursively downloaded.

NCBI project and database links retain curated/database confidence. Europe PMC accession search is opt-in and records `text_discovered` confidence plus query evidence. Publication deduplication uses PMID, PMCID, normalized DOI, then exact normalized title.

Schema versions are independent constants in `metadata/schemas.py`. Readers should reject unsupported major versions; future compatible columns may increment minor versions. Incompatible changes require a migration path from preserved raw data. Refresh archives the complete previous metadata state before writing a new snapshot.

Required BioProject and SRA failures stop the workflow. Optional PubMed, PMC, Assembly, and Europe PMC failures produce a partial snapshot and exit status 4. Every raw and derived artifact is written atomically and recorded with SHA-256 and size in `snapshot.json`.
