# NCBI BioProject Archiver

`ncbi-bioproject` treats a BioProject directory as a reproducible archive unit:

```text
BioProject accession -> metadata snapshot -> run manifest -> managed archive identity -> verified SRA payloads -> optional FASTQ
```

It preserves raw NCBI responses, writes stable normalized JSON/TSV products, selects each run's lossless `SRA Normalized` object, and downloads it with resume, retry, and strict size plus MD5 verification.

The canonical product identity is `ncbi-bioproject-archiver`. The canonical CLI is `ncbi-bioproject`. The legacy alias `sra-bioproject` remains available for compatibility and emits a warning on stderr.

`SRA Normalized` is NCBI's full normalized SRA object produced by the primary ETL workflow. The tool requires `semantic_name="SRA Normalized"` and `supertype="Primary ETL"`; it never substitutes `SRA Lite`, whose reduced quality representation is not lossless.

## Tutorial

See [From a Paper to a Reusable Local BioProject Dataset](docs/tutorial-publication-to-local-bioproject.md)
for an end-to-end workflow covering accession discovery, metadata inspection,
storage planning, verified SRA downloads and optional FASTQ conversion.

## Installation

Python 3.9 or newer and `curl` are required. In a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

For editable development installation with pytest:

```bash
python -m pip install -e '.[dev]'
```

For `--mode fastq` to complete, the `fasterq-dump` binary and at least one compression binary, either `pigz` or `gzip`, must be installed and available on `PATH`. Unless validation is disabled with `--skip-vdb-validate`, the `vdb-validate` binary from the SRA Toolkit must also be installed. When both compression tools are available, `pigz` is preferred; otherwise `gzip` is used.

## Manifest

Generate a stable TSV file through either interface:

```bash
ncbi-bioproject manifest export.xml --output manifest.tsv
python scripts/sra_xml_to_manifest.py export.xml --output manifest.tsv
```

Use `--output -` for standard output. Columns are documented in [docs/design.md](docs/design.md).

## Metadata Snapshots

Set a contact email for substantial NCBI retrievals:

```bash
export NCBI_EMAIL=researcher@example.org
ncbi-bioproject metadata PRJNA831841 --outdir /data/PRJNA831841
ncbi-bioproject snapshot PRJNA831841 --outdir /data/PRJNA831841
```

`metadata` retrieves and normalizes metadata only. `snapshot` additionally writes `manifest.tsv`; neither command downloads sequence data. Add `--include-literature-search` to search Europe PMC for accession mentions, or `--sra-xml existing.xml` to reuse an existing SRA XML export. NCBI-curated links and database links remain distinct from text-discovered Europe PMC associations.

Successful native metadata and snapshot creation now establish immutable archive identity under `provenance/archive.json`. Managed metadata-only archives are valid but remain `UNVERIFIED` until a later archive-wide verification can attest the authoritative payloads.

The metadata behavior is intentionally asymmetric: a new native destination receives archive identity and becomes `UNVERIFIED`, while a recognizable pre-v0.3 destination receives updated metadata only and remains `LEGACY` until archive verification completes adoption.

Existing snapshots are never overwritten implicitly. Use `--refresh` to rebuild in a staging directory and atomically swap only after a successful refresh; the previous metadata state is then archived under `metadata/archive/<timestamp>/`. Rebuild derived files without network access with:

```bash
ncbi-bioproject metadata-normalize --metadata-dir /data/PRJNA831841/metadata \
  --manifest /data/PRJNA831841/manifest.tsv
ncbi-bioproject validate /data/PRJNA831841
```

Raw files are server responses preserved without reformatting. Derived files are normalized products for scripting and inspection. Metadata describes records and provenance; it is not sequence data and does not recursively download linked resources.

For archive lifecycle operations, use:

```bash
ncbi-bioproject status /data/PRJNA831841
ncbi-bioproject verify /data/PRJNA831841
```

`status` is read-only. It validates the complete historical attestation set and evaluates the latest completed attestation against the current manifest, provenance, metadata control state, and observed payload sentinel. `verify` rereads the authoritative SRA payloads and writes a new attestation.

## Download

Inspect storage requirements without network activity:

```bash
ncbi-bioproject download export.xml --outdir /data/my-project --dry-run --bioproject PRJNA000001
```

Download from XML or a previously generated manifest:

```bash
ncbi-bioproject download export.xml --outdir /data/my-project --jobs 2 --bioproject PRJNA000001
ncbi-bioproject download manifest.tsv --outdir /data/my-project --jobs 2 --bioproject PRJNA000001
```

Input format is inferred only from `.xml` or `.tsv`; use `--input-format` to override it. `--bioproject` is required when archive identity cannot be inferred from existing managed provenance or a valid metadata snapshot. Interrupted commands are safe to rerun. Curl resumes `.part` files, exact-size partials are promoted only after verification, verified completed files are skipped, and invalid files are quarantined as `.bad.<timestamp>`.

For an overnight macOS run, create the destination before redirecting output:

```bash
mkdir -p /Volumes/Research/my-project/logs
nohup caffeinate -i ncbi-bioproject download export.xml \
  --outdir /Volumes/Research/my-project --jobs 2 \
  --bioproject PRJNA000001 \
  > /Volumes/Research/my-project/logs/launcher.log 2>&1 &
```

On Linux:

```bash
mkdir -p /data/my-project/logs
nohup ncbi-bioproject download export.xml --outdir /data/my-project --jobs 2 --bioproject PRJNA000001 \
  > /data/my-project/logs/launcher.log 2>&1 &
```

Monitor with:

```bash
tail -f /data/my-project/logs/download.log
pgrep -af ncbi-bioproject
find /data/my-project/sra -type f ! -name '*.part' | wc -l
```

The PRJNA831841 worked example may use:

```bash
ncbi-bioproject download examples/PRJNA831841/NCBI_PRJNA831841.xml \
  --outdir /Volumes/Bioinfo-1/PRJNA831841 --dry-run \
  --bioproject PRJNA831841
```

## FASTQ Conversion

FASTQ conversion may be selected during the initial download or run later as a separate transaction. There is no `--fasterq-dump` option; FASTQ conversion is enabled with `--mode fastq` on the `download` command.

Before using `--mode fastq`, confirm that `fasterq-dump` and either `pigz` or `gzip` are installed and available on `PATH`. Also install `vdb-validate` unless the command will use `--skip-vdb-validate`.

To download SRA objects now and convert them later, run:

```bash
ncbi-bioproject download manifest.tsv --outdir /data/my-project --bioproject PRJNA000001
ncbi-bioproject download manifest.tsv --outdir /data/my-project --mode fastq --bioproject PRJNA000001
```

The second command verifies and skips the completed SRA files, then converts them sequentially. It requires the same XML or manifest input and output directory used for the download, and the verified SRA files must still be present under `OUTDIR/sra/`. The command remains safely resumable: completed FASTQ outputs are skipped only when completion markers match the exact expected file set and validated gzip outputs.

Alternatively, add `--mode fastq` to the initial download command to begin conversion immediately after every required SRA object has downloaded and verified. In either workflow, the application runs `fasterq-dump --split-files`, compresses each FASTQ with `pigz` or `gzip`, tests gzip integrity, and writes a completion marker. Conversion is sequential to limit temporary storage and I/O pressure. `vdb-validate` runs by default; use `--skip-vdb-validate` only deliberately. `--delete-sra-after-fastq` is rejected in v0.3.0 because the authoritative archived payload must remain the verified SRA object.

FASTQ conversion can require substantially more temporary and final disk space than the normalized SRA download. Plan for the SRA file, uncompressed staging FASTQ, compression output, and toolkit scratch space to coexist.

## Output Layout

The layout below is the managed v0.3 form. Recognizable pre-v0.3 legacy directories may lack `provenance/` until successful all-or-nothing adoption.

```text
OUTDIR/
├── manifest.tsv
├── metadata/
│   ├── snapshot.json
│   ├── raw/             preserved NCBI and optional Europe PMC responses
│   ├── derived/         project.json and stable TSV tables
│   └── archive/         prior snapshots created by --refresh
├── provenance/
│   ├── archive.json     immutable archive identity
│   ├── acquisitions.jsonl
│   └── validations/     append-only verification attestations
├── sra/                 verified SRA objects and resumable .part files
├── fastq/               optional .fastq.gz files and completion markers
├── tmp/                 FASTQ staging and scratch data
└── logs/
    ├── download.log
    └── failed_accessions.txt   present only after persistent failures
```

The dry run reports normalized SRA size, sequenced bases, and destination free space. A final filename is never considered complete by name alone: required size and MD5 metadata must verify before an atomic `.part` rename or skip.

Exit statuses are `0` complete success or current verified status, `1` general failure, `2` invalid input/configuration, `3` required retrieval incomplete, `4` optional metadata missing, `5` normalization or verification failure, `6` actionable non-current lifecycle state from `status`, and `130` keyboard interruption. See [docs/troubleshooting.md](docs/troubleshooting.md) for recovery commands.

## Limitations

The parser requires one unique lossless normalized object per run. Downloads depend on `curl` and the URLs remaining valid. Metadata retrieval depends on current public Entrez records; optional resources may be absent and produce explicit empty tables or partial status. FASTQ conversion is not parallelized.

## License

The original software and documentation in this repository are licensed under
the [MIT License](LICENSE).

NCBI records, sequence data, metadata exports and external software used by the
application remain subject to the terms and policies of their respective
sources and copyright holders.
