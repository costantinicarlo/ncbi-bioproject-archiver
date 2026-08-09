# Changelog

## 0.3.0

- Renamed the Python distribution to `ncbi-bioproject-archiver` and made `ncbi-bioproject` the canonical CLI while retaining `sra-bioproject` as a warned compatibility alias.
- Added durable archive identity, append-only admission provenance, structured verification attestations, and lifecycle status reporting for managed BioProject archives.
- Added archive-wide `verify` and `status` commands, including honest legacy bootstrap, SHA-256 baseline establishment, and conservative staleness detection.
- Made metadata and snapshot creation initialize native archive identity transactionally without weakening refresh rollback guarantees.
- Added deep verification through required `vdb-validate`, atomic all-or-nothing legacy bootstrap, fail-closed destination classification, and restart-safe failed initialization.
- Recorded separate acquisition and admission provenance, policy-versioned attestations, and combined MD5/SHA-256 integrity traversal for authoritative SRA payloads.
- Rejected `--delete-sra-after-fastq` under the v0.3 archival contract because authoritative SRA payloads must remain present.

## 0.2.1

- Hardened run accession handling with strict validation and filesystem containment checks before run-specific file operations.
- Made SRA verification fail closed: downloads now require positive expected size and valid MD5 metadata at ingestion and verification time.
- Strengthened partial download handling by promoting exact-size `.part` files only after MD5 verification.
- Corrected FASTQ completion semantics by validating completion-marker manifests (exact file set, recorded sizes, safe names, gzip integrity).
- Tightened metadata snapshot validation to enforce schema-major support, required files, safe relative paths, checksums, record counts, and cross-file identity consistency.
- Made metadata refresh transactional using staging and atomic directory swaps; failed refreshes no longer mutate existing snapshots.
- Made offline metadata normalization reproducible by preserving `retrieved_at` provenance from existing snapshots.
- Added automatic Entrez POST for large `efetch` UID lists.
- Switched XML parsing to `defusedxml` and added it as a runtime dependency.
- Added CI and Dependabot configuration to continuously exercise tests and dependency updates.

## 0.2.0

- Added reproducible BioProject metadata snapshots, normalization, provenance,
  validation, refresh archival and optional literature discovery.
- Added the MIT License, citation metadata and third-party material notices to
  the 0.2.0 package through the licensed 0.1.0 release history.

## 0.1.0

### Added

- MIT License for the repository's original software and documentation.
- `CITATION.cff` metadata for scholarly citation.
- `THIRD_PARTY_NOTICES.md` clarifying the status of NCBI records, sequence data
  and externally invoked software.

### Changed

- Updated Python package metadata to use the SPDX `MIT` licence expression and
  include the root `LICENSE` file in distributions.

- Generalized the downloader as the `sra_bioproject` package and `sra-bioproject` CLI.
- Added strict NCBI SRA XML parsing and reproducible TSV manifests.
- Added XML and TSV download inputs, offline regression tests, and operational documentation.
- Preserved verified resumable downloads, failed-only retry passes, integrity checks, and optional sequential FASTQ conversion.

## Prototype v1.2

- Added `curl --retry-all-errors`, failed-run retry passes, continued processing after individual failures, and `failed_accessions.txt`.

## Prototype v1.1

- Corrected the destination volume spelling and validated mounted, writable macOS volumes.

## Prototype v1.0

- Added the initial project-specific resumable SRA downloader.
