# NCBI SRA BioProject Downloader

`sra-bioproject` parses an NCBI SRA XML export, selects each run's lossless `SRA Normalized` object, and downloads it with resume, retry, size, and MD5 verification. It can also create a durable TSV manifest or optionally convert verified SRA objects to compressed FASTQ.

`SRA Normalized` is NCBI's full normalized SRA object produced by the primary ETL workflow. The tool requires `semantic_name="SRA Normalized"` and `supertype="Primary ETL"`; it never substitutes `SRA Lite`, whose reduced quality representation is not lossless.

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

FASTQ mode additionally requires `fasterq-dump` and, unless validation is disabled, `vdb-validate` from the SRA Toolkit. `pigz` is used when available; otherwise the required system `gzip` command is used.

## Manifest

Generate a stable TSV file through either interface:

```bash
sra-bioproject manifest export.xml --output manifest.tsv
python scripts/sra_xml_to_manifest.py export.xml --output manifest.tsv
```

Use `--output -` for standard output. Columns are documented in [docs/design.md](docs/design.md).

## Download

Inspect storage requirements without network activity:

```bash
sra-bioproject download export.xml --outdir /data/my-project --dry-run
```

Download from XML or a previously generated manifest:

```bash
sra-bioproject download export.xml --outdir /data/my-project --jobs 2
sra-bioproject download manifest.tsv --outdir /data/my-project --jobs 2
```

Input format is inferred only from `.xml` or `.tsv`; use `--input-format` to override it. Interrupted commands are safe to rerun. Curl resumes `.part` files, verified completed files are skipped, and invalid completed files are quarantined as `.bad.<timestamp>`.

For an overnight macOS run, create the destination before redirecting output:

```bash
mkdir -p /Volumes/Research/my-project/logs
nohup caffeinate -i sra-bioproject download export.xml \
  --outdir /Volumes/Research/my-project --jobs 2 \
  > /Volumes/Research/my-project/logs/launcher.log 2>&1 &
```

On Linux:

```bash
mkdir -p /data/my-project/logs
nohup sra-bioproject download export.xml --outdir /data/my-project --jobs 2 \
  > /data/my-project/logs/launcher.log 2>&1 &
```

Monitor with:

```bash
tail -f /data/my-project/logs/download.log
pgrep -af sra-bioproject
find /data/my-project/sra -type f ! -name '*.part' | wc -l
```

The PRJNA831841 worked example may use:

```bash
sra-bioproject download examples/PRJNA831841/NCBI_PRJNA831841.xml \
  --outdir /Volumes/Bioinfo-1/PRJNA831841 --dry-run
```

## FASTQ Conversion

SRA is the default output. Add `--mode fastq` to run `fasterq-dump --split-files`, compress each FASTQ with `pigz` or `gzip`, test gzip integrity, and write a completion marker. Conversion is sequential to limit temporary storage and I/O pressure. `vdb-validate` runs by default; use `--skip-vdb-validate` only deliberately. `--delete-sra-after-fastq` removes an SRA object only after all compressed FASTQ files pass their checks.

FASTQ conversion can require substantially more temporary and final disk space than the normalized SRA download. Plan for the SRA file, uncompressed staging FASTQ, compression output, and toolkit scratch space to coexist.

## Output Layout

```text
OUTDIR/
├── manifest.tsv
├── sra/                 verified SRA objects and resumable .part files
├── fastq/               optional .fastq.gz files and completion markers
├── tmp/                 FASTQ staging and scratch data
└── logs/
    ├── download.log
    └── failed_accessions.txt   present only after persistent failures
```

The dry run reports normalized SRA size, sequenced bases, and destination free space. A final filename is never considered complete by name alone: available size and MD5 metadata must verify before an atomic `.part` rename or skip.

Exit status `0` means success, `1` means a fatal error or persistent run failure, and `130` means keyboard interruption. See [docs/troubleshooting.md](docs/troubleshooting.md) for recovery commands.

## Limitations

The parser targets NCBI SRA experiment-package XML exports and requires one unique lossless normalized object per run. Downloads depend on `curl` and the URLs remaining valid. The tool does not fetch BioProjects by accession, manage credentials, or parallelize FASTQ conversion.
