# Archive Lifecycle Decision

## Scope

Version 0.3.0 promotes the application from a downloader-centric workflow to a managed BioProject archiver. The release adds immutable archive identity, append-only admission provenance, archive-wide verification attestations, independent validation-policy versioning, lifecycle status, and honest bootstrap of pre-v0.3 holdings.

The release does not add upstream reconciliation, automatic repair, reacquisition policy, signatures, FASTQ attestations, or import-namespace renaming.

## Canonical identities

The canonical product identity is `ncbi-bioproject-archiver`.

The canonical CLI is `ncbi-bioproject`.

The compatibility CLI alias is `sra-bioproject`, which remains available and warns once on stderr per invocation.

The Python import namespace remains `sra_bioproject`, and `python -m sra_bioproject` remains valid without a legacy-name warning.

## Authoritative payload rule

The authoritative scientific payload remains the verified lossless SRA object stored under `sra/<RUN_ACCESSION>` with no `.sra` suffix.

FASTQ output is derived material. It may be useful operationally, but it does not substitute for the authoritative archived SRA object when determining whether an archive is complete and production-safe.

For that reason, v0.3.0 rejects `--delete-sra-after-fastq`.

## Schemas and version independence

Archive identity, admission records, attestations, application version, and validation policy are independent version axes.

v0.3.0 starts with:

- `ARCHIVE_SCHEMA_VERSION = "1.0"`
- `ACQUISITION_SCHEMA_VERSION = "1.0"`
- `ATTESTATION_SCHEMA_VERSION = "1.0"`
- `VALIDATION_POLICY_VERSION = 1`

Application patches may leave the validation policy unchanged. Conversely, a future integrity or security fix may increment the validation policy without rewriting archive history.

## Immutable versus append-only state

`provenance/archive.json` is immutable once established.

`provenance/acquisitions.jsonl` is logically append-only admission history, published durably through complete-file replacement rather than concurrent append writes.

`provenance/validations/*.json` is append-only attestation history. A later attestation never overwrites an earlier one.

## Admission versus byte acquisition provenance

v0.3.0 distinguishes the admission of an artifact into a managed archive from the historical provenance of the bytes already present in that artifact.

Supported admission methods are:

- `downloaded_fresh`
- `resumed_download`
- `promoted_partial`
- `existing`
- `legacy_observation`

Only `downloaded_fresh` may attribute all bytes to the current application invocation. Resumed, promoted, existing, and legacy artifacts may still be admitted truthfully, but their original byte-acquisition provenance remains mixed or unknown unless actual evidence exists.

Metadata snapshot versions are not treated as proof of historical payload acquisition provenance.

## Verification semantics

`validate` remains the metadata-snapshot validator.

`verify` performs archive-wide integrity attestation over authoritative SRA holdings.

Standard verification checks:

- identity consistency
- metadata snapshot validity where applicable
- manifest validity
- safe paths
- authoritative SRA existence
- expected size
- upstream MD5
- local SHA-256

Deep verification additionally requires and runs `vdb-validate`.

If `vdb-validate` is unavailable, deep verification fails as an environment or input problem rather than as a scientific integrity failure.

## SHA-256 baseline semantics

For pre-ledger legacy material, the first v0.3 verification does not compare against a historical local SHA-256 because no such trustworthy baseline exists.

Instead, first legacy verification proves source compatibility through expected size plus upstream MD5 and establishes a local SHA-256 baseline.

Per-run SHA state is therefore reported as one of:

- `baseline_established`
- `baseline_matched`
- `baseline_mismatch`

Subsequent verification can compare current SHA-256 values against the established local baseline.

## Legacy bootstrap

Legacy bootstrap is all-or-nothing.

A recognizable pre-v0.3 archive remains `LEGACY` until the complete authoritative SRA set has been verified read-only.

If any required legacy artifact fails, v0.3.0 publishes no managed provenance, no ledger, and no attestation. Metadata or snapshot operations never bootstrap a recognizable legacy archive. Pre-existing payloads are admitted as `legacy_observation`; fresh, resumed, and promoted material retain their truthful admission method. The SHA-256 baseline committed by bootstrap always comes from the final verification pass.

On complete success, the archive receives immutable archive identity, truthful legacy admission observations, and a first PASS attestation whose control fingerprint already reflects the post-bootstrap state.

## Lifecycle states

`status` is strictly read-only.

It may report:

- `UNINITIALIZED`
- `LEGACY`
- `UNVERIFIED`
- `STALE`
- `FAILED`
- `VERIFIED`
- `INVALID`

`INVALID` means control or provenance state cannot safely be interpreted.

`FAILED` means the state was interpretable and a current verification attempt found actual integrity failures.

`VERIFIED` means the latest completed attestation is a PASS and still applies to the current policy, control state, and observed payload sentinel. The complete historical attestation set must also validate.

## Quick payload sentinel limitation

`status` does not reread every payload byte.

It uses a quick payload sentinel built from expected path, existence, size, and `mtime_ns` for every authoritative SRA artifact.

This sentinel is intentionally conservative. Timestamp-only changes may produce `STALE` even when payload bytes are unchanged.

Only `verify` makes a fresh cryptographic statement about current archive integrity.

## Status exit mapping

`status` returns:

- `0` only for `VERIFIED`
- `6` for actionable non-current lifecycle states
- `5` for `INVALID`

`verify` returns `0` for PASS, `5` for completed integrity failure, and `2` for invalid input or incomplete verification prerequisites such as a missing authoritative manifest.

## Non-goals

Version 0.3.0 does not attempt to decide whether upstream records have changed, repair broken archives, reacquire missing data automatically, certify FASTQ as archival evidence, or stabilize a long-term v1 archive specification.
