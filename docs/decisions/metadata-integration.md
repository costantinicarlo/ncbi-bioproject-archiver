# Metadata Integration Decision

## Observed Drift

The 0.1.0 repository already used the intended `sra_bioproject` package and `sra-bioproject` command. It had only `manifest` and `download`, one canonical SRA XML parser, no network client, and no metadata models. The committed PRJNA831841 example currently contains 54 runs, not the 187 stated in the integration brief; the committed XML and deterministic manifest remain the source of truth.

## Canonical Interface

`metadata ACCESSION --outdir DIR` retrieves metadata only. `snapshot ACCESSION --outdir DIR` also creates the canonical root `manifest.tsv` but does not download sequence data. Existing file-based `manifest` and `download` invocations are unchanged. `metadata-normalize` rebuilds from raw files, and `validate` verifies a project directory.

The existing SRA XML parser and `RunRecord` remain canonical. `runs.tsv` is the comprehensive metadata table; `manifest.tsv` is its download-focused projection. No duplicate XML parser or manifest implementation was introduced.

## Schema And Lifecycle

Snapshot, project JSON, metadata TSV, and manifest schemas have independent version constants. Raw responses and normalized outputs are SHA-256 indexed. A pre-existing snapshot is rejected unless `--refresh` archives it under `metadata/archive/YYYYMMDDTHHMMSSZ/`.

Required BioProject and SRA retrieval failures stop with exit status 3. Optional publication, assembly, and Europe PMC failures are recorded as partial and return status 4. Europe PMC remains opt-in and its associations are labeled text-discovered rather than curated.

Runtime metadata is not globally ignored because fixtures and examples are valid repository content. Only metadata beneath the conventional `output/*/` local runtime root is ignored.
