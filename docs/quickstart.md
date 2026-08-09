# Quick Start: From a BioProject Accession to a Verified Local Archive

*The shortest operational path through `ncbi-bioproject-archiver` 0.3.0*

A publication's BioProject accession is a route to data, not yet a dataset you can safely analyse. The project may contain more runs than the paper used, and a large transfer can fail in ways that leave plausible but incomplete files. `ncbi-bioproject` preserves the metadata, records what was admitted, verifies the authoritative payload, and makes the archive state inspectable later.

The v0.3 lifecycle is:

```text
snapshot -> validate metadata -> dry-run -> download authoritative SRA
         -> verify archive -> status VERIFIED -> optional FASTQ conversion
```

Examples use `PRJNA831841`, the worked example in this repository. It is large, so do not launch its real download merely as a test. Replace it with the accession from your publication and always perform a dry run first. `ncbi-bioproject` is canonical; `sra-bioproject` remains only as a warned compatibility alias.

If you want the biological and archival rationale behind each step rather than the shortest operational route, see [tutorial-publication-to-local-bioproject.md](tutorial-publication-to-local-bioproject.md).

## 1. Identify the BioProject

Look in the paper's data-availability or supplementary sections for `PRJNA`, `SRP`, `SRR`, `SAMN`, or `SRX`. A BioProject (`PRJNA...`) is usually the best starting point because it links project, sample, experiment, run, publication, and assembly records.

Do not assume that the current BioProject is identical to the dataset analysed in the paper. Compare its organisms, samples, library strategies, and run list with the publication before allocating storage.

Record the scientific context separately from the application's technical provenance:

```text
Paper: <citation or DOI>
BioProject: PRJNA...
Scientific purpose: <why this dataset is relevant>
Retrieved on: <date>
```

## 2. Install and choose a destination

Python 3.9 or newer and `curl` are required. FASTQ conversion additionally needs `fasterq-dump`, `vdb-validate`, and `gzip`; `pigz` is optional.

```bash
python3 -m venv ~/.venvs/ncbi-bioproject
source ~/.venvs/ncbi-bioproject/bin/activate
python -m pip install --upgrade pip
python -m pip install .
ncbi-bioproject --help
```

Choose a stable directory named for the accession:

```bash
PROJECT="PRJNA831841"
DATA_ROOT="/Volumes/Bioinfo-1"
OUTDIR="$DATA_ROOT/$PROJECT"

mkdir -p "$OUTDIR"
test -d "$DATA_ROOT" && test -w "$DATA_ROOT"
df -h "$DATA_ROOT"
```

Paths below `/Volumes` receive an additional macOS mount and writability check. Set a real NCBI contact email for network retrievals:

```bash
export NCBI_EMAIL="your.name@example.org"
export NCBI_TOOL="ncbi-bioproject"
```

## 3. Create and inspect the metadata snapshot

```bash
ncbi-bioproject snapshot "$PROJECT" --outdir "$OUTDIR"
```

`metadata` retrieves and normalizes records without a manifest; `snapshot` also writes `manifest.tsv`. Neither downloads sequence data. A native snapshot establishes immutable archive identity and starts the archive in `UNVERIFIED`.

The managed form after snapshot includes:

```text
OUTDIR/
├── manifest.tsv
├── metadata/
│   ├── snapshot.json
│   ├── raw/
│   └── derived/
│       ├── project.json
│       ├── samples.tsv
│       ├── sample_attributes.tsv
│       └── runs.tsv
└── provenance/
    └── archive.json
```

Raw files preserve server responses. Derived files are normalized products for inspection and scripting. The manifest records the lossless `SRA Normalized` object, expected size, upstream MD5, URL, run, sample, experiment, and library metadata.

Inspect before downloading:

```bash
python -m json.tool "$OUTDIR/metadata/derived/project.json" | less
column -t -s $'\t' "$OUTDIR/metadata/derived/samples.tsv" | less -S
column -t -s $'\t' "$OUTDIR/metadata/derived/sample_attributes.tsv" | less -S
head -n 5 "$OUTDIR/metadata/derived/runs.tsv"
```

Optional linked-resource failures produce a partial snapshot and exit status `4`. Required BioProject or SRA retrieval failures produce status `3` and should be resolved before acquisition.

## 4. Validate metadata and measure storage

Validate the metadata snapshot separately from archive integrity:

```bash
ncbi-bioproject validate "$OUTDIR"
```

Then perform a no-network capacity check. On a fresh destination, provide the accession explicitly so archive identity is unambiguous:

```bash
ncbi-bioproject download examples/PRJNA831841/NCBI_PRJNA831841.xml \
  --outdir "$OUTDIR" \
  --dry-run \
  --bioproject "$PROJECT"
```

The dry run reports run count, normalized SRA size, sequenced bases, and free space. Reserve room for partial files, logs, filesystem overhead, and downstream work. FASTQ conversion needs considerably more space.

## 5. Download the authoritative SRA objects

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2
```

The lossless SRA object under `sra/<RUN_ACCESSION>` is authoritative. Each transfer uses a `.part` file and promotes it only after expected size and MD5 verification. Verified files are skipped on reruns; invalid files are quarantined as `.bad.<timestamp>`.

For an overnight macOS run:

```bash
mkdir -p "$OUTDIR/logs"
nohup caffeinate -i ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" --jobs 2 \
  > "$OUTDIR/logs/launcher.log" 2>&1 &
```

Rerun the same command after an interruption. Leave `.part` files in place so suitable transfers can resume. Inspect `logs/download.log` and `logs/failed_accessions.txt` when a pass ends with failures.

## 6. Verify the archive and inspect lifecycle status

After acquisition, perform archive-wide verification:

```bash
ncbi-bioproject verify "$OUTDIR"
ncbi-bioproject status "$OUTDIR"
```

Verification rereads every authoritative SRA object and writes a validation attestation under `provenance/validations/`. Admission events are recorded in `provenance/acquisitions.jsonl`. A successful verification leaves the archive `VERIFIED`.

`status` is read-only. It validates every historical attestation, then evaluates the latest completed attestation against the current manifest, provenance, metadata control state, and quick payload sentinel. It does not reread every payload byte, so run `verify` for a fresh cryptographic statement.

The possible states are:

| State | Meaning |
| --- | --- |
| `UNINITIALIZED` | No recognizable archive or metadata exists. |
| `LEGACY` | Recognizable pre-v0.3 material has not completed bootstrap. |
| `UNVERIFIED` | Native identity exists without an applicable PASS attestation. |
| `STALE` | A prior PASS no longer matches current control or payload-sentinel state. |
| `FAILED` | A current verification found actual integrity failures. |
| `VERIFIED` | The latest completed attestation is a valid PASS for current state. |
| `INVALID` | Control or provenance state cannot safely be interpreted. |

For deep verification, install the SRA Toolkit and run:

```bash
ncbi-bioproject verify "$OUTDIR" --deep
vdb-validate "$OUTDIR/sra/SRR12345678"
```

Deep verification requires `vdb-validate`; its absence is not silently ignored.

Pre-v0.3 legacy directories may lack `provenance/`. Legacy adoption is all-or-nothing: every required authoritative SRA object must pass read-only verification before identity, admission provenance, and the first PASS attestation are published. If one object fails, no managed provenance is published and the directory remains `LEGACY`.

## 7. Convert verified SRA to compressed FASTQ

FASTQ is derived output and does not replace the authoritative SRA object. Convert it only after the archive has been verified:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --mode fastq \
  --threads 8
```

The command verifies and skips completed SRA files, runs `fasterq-dump --split-files`, compresses with `pigz` or `gzip`, tests gzip integrity, and writes completion markers. `vdb-validate` runs by default; `--skip-vdb-validate` is an explicit exception. SRA files are always retained. `--delete-sra-after-fastq` is rejected under the v0.3 archival contract.

## Other workflows

Use metadata reconnaissance without a manifest:

```bash
ncbi-bioproject metadata "$PROJECT" --outdir "$OUTDIR"
```

Refresh metadata transactionally:

```bash
ncbi-bioproject snapshot "$PROJECT" --outdir "$OUTDIR" --refresh
```

The previous metadata state is archived under `metadata/archive/`; sequence files and provenance remain in place. Rebuild derived products without network access with:

```bash
ncbi-bioproject metadata-normalize \
  --metadata-dir "$OUTDIR/metadata" \
  --manifest "$OUTDIR/manifest.tsv"
```

Use `--include-literature-search` for opt-in Europe PMC accession searching. Use `--sra-xml /path/to/export.xml` to process an existing SRA XML export. For a standalone XML or TSV, `download --input-format xml|tsv` selects the input explicitly when the suffix is unusual.

## Exit statuses

| Status | Meaning |
| ---: | --- |
| `0` | Complete success or current `VERIFIED` status |
| `1` | General failure or persistent download failure |
| `2` | Invalid input or configuration |
| `3` | Required metadata retrieval incomplete |
| `4` | Optional metadata missing from an otherwise usable snapshot |
| `5` | Metadata normalization, archive verification, or integrity failure |
| `6` | `status` reports an actionable non-current lifecycle state |
| `130` | Keyboard interruption |

## Further reading

- [Full tutorial](tutorial-publication-to-local-bioproject.md) for the longer narrative workflow and lifecycle rationale
- [README](../README.md) for installation and command summary
- [Design notes](design.md) for manifest, integrity, recovery, and metadata architecture
- [Archive lifecycle decision](decisions/archive-lifecycle.md) for state and provenance rules
- [Troubleshooting](troubleshooting.md) for mounts, TLS failures, retries, and recovery
- [Development history](development-history.md) for the incidents that shaped the downloader
