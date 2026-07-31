# Changelog

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
