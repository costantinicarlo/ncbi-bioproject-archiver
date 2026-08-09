# Design

## Responsibilities

XML parsing is separate from download execution so NCBI metadata can be tested, reviewed, and converted without network access. `xml_parser.py` selects one `SRA Normalized`/`Primary ETL` object per run. `manifest.py` serializes those records. `downloader.py` handles local files and curl. `fastq.py` owns optional conversion, and `cli.py` coordinates them.

`archive.py` owns immutable archive identity, admission provenance, attestation storage, and control/payload fingerprints. `verification.py` owns archive-wide verification, lifecycle state calculation, and honest bootstrap of pre-v0.3 holdings.

The manifest is a durable provenance artifact: it records exactly which run URL, size, checksum, BioSample, experiment, library, and platform metadata drove a download. Its stable UTF-8 TSV columns are:

```text
run_accession experiment_accession experiment_alias biosample sample_alias
library_strategy library_source library_layout instrument_model total_bases
total_spots sra_size_bytes md5 url
```

The physical separator is a tab and records are sorted by run accession.

## Integrity And Recovery

Size catches truncation cheaply; MD5 verifies content. Both are required and validated at ingestion because neither a filename nor a successful process exit proves that the expected object is complete. Archive verification calculates MD5 and SHA-256 in one payload traversal, retaining the upstream check and establishing the local baseline without rereading the payload. Curl writes to `<accession>.part`; only a verified part is atomically renamed to the final filename. Exact-size partials are promoted only after verification. Invalid files are retained as `.bad.<timestamp>` for diagnosis.

One run's failure is collected rather than propagated through the executor. Other futures finish, and later passes contain only failed runs. Persistent failures produce `logs/failed_accessions.txt` and a non-zero exit status.

Managed archives now distinguish byte acquisition from admission into the archive. Fresh downloads may attribute all bytes to the current application, while resumed, promoted, existing, and legacy material retain unknown or mixed original acquisition provenance and still receive truthful admission events.

FASTQ conversion is sequential because `fasterq-dump`, scratch I/O, and compression can multiply temporary storage and saturate a disk. Completion is represented by gzip-tested outputs plus a marker manifest that records exact filenames and sizes. v0.3.0 rejects `--delete-sra-after-fastq` because the authoritative archival payload remains the verified SRA object.

Runtime data (`sra/`, `fastq/`, `tmp/`, logs, partials, and quarantined files) are excluded from Git because they are large, mutable, and reproducible from the committed XML/manifest provenance.

Mounted-volume validation is macOS-specific and applies only to paths below `/Volumes`; paths are resolved first, then checked for writability and a real mount-point root. XML/TSV parsing, ordinary paths, curl execution, checksums, retries, and FASTQ workflows are portable to Linux and macOS.

Destination classification fails closed: an ambiguous mixture of managed, legacy, and unexplained content is rejected before runtime directories or provenance are mutated. This preserves restart safety and prevents a download from silently adopting the wrong archive identity.

## Metadata Architecture

BioProject is the discovery hub, while BioSample carries heterogeneous sample provenance. Entrez ESearch resolves the accession, ESummary preserves the project record, and ELink discovers explicit relationships. The current link definitions are verified through EInfo and include `bioproject_biosample_all`, `bioproject_sra_all`, `bioproject_pubmed`, `bioproject_pmc`, `bioproject_assembly_all`, `bioproject_bioproject_d2u`, and `bioproject_bioproject_u2d`. Large EFetch UID lists are submitted with POST to avoid oversized query strings.

Raw responses are immutable evidence. Normalized products are reproducible interpretations of that evidence. `samples.tsv` provides stable common fields while `sample_attributes.tsv` preserves every original BioSample attribute in long form. `runs.tsv` and `manifest.tsv` derive from the same `RunRecord` objects; the manifest is the download-focused projection. Linked resources are inventoried rather than recursively downloaded.

NCBI project and database links retain curated/database confidence. Europe PMC accession search is opt-in and records `text_discovered` confidence plus query evidence. Publication deduplication uses PMID, PMCID, normalized DOI, then exact normalized title.

Schema versions are independent constants in `metadata/schemas.py`. Readers should reject unsupported major versions; future compatible columns may increment minor versions. Incompatible changes require a migration path from preserved raw data. Refresh is transactional: retrieval and normalization complete in staging before an atomic swap and archival of the previous metadata state.

Archive lifecycle state is distinct from metadata validation. `validate` remains the metadata-snapshot validator. `verify` performs archive-wide integrity attestation, while `status` validates the complete historical attestation set and evaluates the latest completed attestation against the current control state and observed payload sentinel. `status` is read-only and does not reread payload bytes.

Native metadata or snapshot creation establishes immutable archive identity and starts the archive as `UNVERIFIED`. A recognizable pre-v0.3 destination is treated as `LEGACY`: snapshot updates metadata but does not bootstrap managed provenance. Legacy adoption requires complete authoritative SRA verification and publishes identity, admission observations, and the first attestation atomically.

Required BioProject and SRA failures stop the workflow. Optional PubMed, PMC, Assembly, and Europe PMC failures produce a partial snapshot and exit status 4. Every raw and derived artifact is written atomically and recorded with SHA-256 and size in `snapshot.json`.
