# Changelog

## 0.1.0

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
