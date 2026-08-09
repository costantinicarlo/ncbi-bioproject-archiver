# From a Paper to a Reusable Local BioProject Dataset

*An end-to-end tutorial for `ncbi-sra-bioproject-downloader` 0.2.1*

A sequencing paper can be exciting for all the right reasons: an interesting organism, an elegant design, or a dataset that could answer a question the authors never asked. Then comes the less glamorous moment. Somewhere near the end of the article, often in a short data-availability paragraph, you find an accession such as `PRJNA831841`. You now have a route to the data—but not yet a dataset you can safely analyse.

The gap between those two things is larger than it first appears. A BioProject can contain many samples and runs. Its current database record may include material that was not central to the paper. The files may be hundreds of gigabytes. A network interruption can leave an apparently plausible but incomplete file. Months later, it may be difficult to remember exactly which records were downloaded, what their checksums were, or which version of the metadata you inspected.

`sra-bioproject` is designed to make that transition orderly. It treats a local BioProject directory as a reproducible acquisition unit:

```text
BioProject accession
    -> preserved metadata snapshot
    -> normalized tables
    -> run manifest
    -> verified SRA files
    -> optional compressed FASTQ files
```

This chapter begins with the smallest practical workflow: start with a BioProject accession from a publication, inspect what it represents, estimate the required storage, and download the lossless SRA data. The later sections explore the other routes and controls offered by the command-line application.

The commands target release **0.2.1**. Examples use `PRJNA831841`, the worked example already present in this repository. It is a large project, so do not launch its real download merely as a test. Replace that accession with the one from your publication and always perform a dry run first.

---

## 1. Start with the paper, not the download button

The first task is to identify the accession that describes the dataset at the right level.

Look in the paper's *Data availability*, *Data accessibility*, *Sequence data*, or supplementary information sections. Search the PDF for `PRJNA`, `SRP`, `SRR`, `SAMN`, `ENA`, or simply `accession`. The accession prefixes tell you what kind of object you have found:

| Prefix | Usually identifies | Why it matters |
| --- | --- | --- |
| `PRJNA...` | NCBI BioProject | The best starting point for a whole study |
| `SRP...` | SRA study | A sequencing-study record linked to runs |
| `SAMN...` | BioSample | One biological sample and its provenance |
| `SRX...` | SRA experiment | Library and sequencing design information |
| `SRR...` | SRA run | One downloadable sequencing run |

For this workflow, a `PRJNA...` BioProject accession is ideal. It acts as a hub connecting the project description, BioSamples, SRA experiments and runs, publications, assemblies, and other database records.

Do not assume that “the BioProject” and “the data analysed in the paper” are automatically identical. Projects can grow after publication, contain pilot samples, include several sequencing strategies, or connect to related studies. Before allocating disk space, compare the article's methods and sample table with the BioProject metadata. The application helps by preserving the database records and turning them into tables you can inspect.

It is worth recording the paper alongside the accession in your research notes before doing anything else:

```text
Paper: <full citation or DOI>
BioProject: PRJNA...
Scientific purpose: <why this dataset is relevant>
Expected organism/population/treatment: <brief description>
Retrieved on: <date>
```

The software captures technical retrieval provenance. Your note captures the scientific reason for acquiring the dataset.

---

## 2. Install the application in an isolated environment

The application requires Python 3.9 or newer. Sequence downloads use `curl`, which is normally already present on macOS and many Linux systems.

A virtual environment keeps the application separate from the rest of your Python installation. To install the tagged 0.2.0 release from GitHub:

```bash
python3 -m venv ~/.venvs/sra-bioproject
source ~/.venvs/sra-bioproject/bin/activate

python -m pip install --upgrade pip
python -m pip install \
  "git+https://github.com/costantinicarlo/ncbi-sra-bioproject-downloader.git@v0.2.0"
```

Confirm that the command is available:

```bash
sra-bioproject --help
```

For a repository-local installation:

```bash
git clone --branch v0.2.0 --depth 1 \
  https://github.com/costantinicarlo/ncbi-sra-bioproject-downloader.git

cd ncbi-sra-bioproject-downloader

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

Whenever you open a new terminal, reactivate the environment before using the command.

You do not need the NCBI SRA Toolkit to create metadata snapshots or download normalized SRA objects. FASTQ conversion additionally requires `fasterq-dump`, `vdb-validate`, and `gzip`; `pigz` is optional but preferable for multithreaded compression.

---

## 3. Choose a durable project directory

A BioProject folder should have a stable location and a simple name. The accession itself is an excellent directory name because it is unique, searchable, and easy to connect back to NCBI.

For Linux:

```bash
PROJECT="PRJNA831841"       # replace with the accession from your paper
DATA_ROOT="/data/bioprojects"
OUTDIR="$DATA_ROOT/$PROJECT"

mkdir -p "$OUTDIR"
```

For an external macOS volume:

```bash
PROJECT="PRJNA831841"       # replace with your accession
DATA_ROOT="/Volumes/Bioinfo-1"
OUTDIR="$DATA_ROOT/$PROJECT"

mkdir -p "$OUTDIR"
```

Check the destination:

```bash
test -d "$DATA_ROOT" && test -w "$DATA_ROOT" \
  && echo "Destination is available"

df -h "$DATA_ROOT"
```

Paths below `/Volumes` receive an additional macOS safety check. A misspelled or unmounted volume causes the application to stop instead of quietly placing data somewhere unintended.

---

## 4. Identify yourself to NCBI

NCBI asks programmatic users to provide a real contact email. Set it in the terminal session:

```bash
export NCBI_EMAIL="your.name@example.org"
```

The default tool identifier is suitable, although it can be made explicit:

```bash
export NCBI_TOOL="sra-bioproject"
```

An API key is optional:

```bash
export NCBI_API_KEY="your-api-key"
```

The key is redacted from snapshot provenance and should never be committed to Git.

---

## 5. Create the metadata snapshot and run manifest

For the normal accession-first workflow, use `snapshot`:

```bash
sra-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR"
```

This retrieves the BioProject and linked public metadata, preserves the raw responses, creates normalized tables, and writes `manifest.tsv`. It **does not download sequence files**.

A snapshot resembles:

```text
PRJNA.../
├── manifest.tsv
└── metadata/
    ├── snapshot.json
    ├── raw/
    │   ├── bioproject.xml
    │   ├── biosamples.xml
    │   ├── sra_experiments.xml
    │   ├── entrez_links.xml
    │   └── ...
    └── derived/
        ├── project.json
        ├── samples.tsv
        ├── sample_attributes.tsv
        ├── runs.tsv
        ├── publications.tsv
        ├── relationships.tsv
        └── linked_resources.tsv
```

Files under `metadata/raw/` are preserved server responses. Files under `metadata/derived/` are stable normalized products for reading, scripting, and analysis. The manifest is the acquisition recipe: each row records a run URL, expected size, MD5 checksum, BioSample, experiment, library information, and platform metadata.

Some linked resources are optional. A project with no linked publication or a temporary optional-service failure can produce a usable snapshot with `status: partial` and exit status `4`. Inspect:

```bash
python -m json.tool "$OUTDIR/metadata/snapshot.json" | less
```

A required BioProject or SRA failure returns status `3` and should be resolved before downloading.

---

## 6. Read the snapshot before acquiring sequence files

Begin with the project summary:

```bash
python -m json.tool \
  "$OUTDIR/metadata/derived/project.json" | less
```

Inspect the common sample fields:

```bash
column -t -s $'\t' \
  "$OUTDIR/metadata/derived/samples.tsv" | less -S
```

BioSample records are heterogeneous, so the long-format attribute table often contains the biologically decisive details:

```bash
column -t -s $'\t' \
  "$OUTDIR/metadata/derived/sample_attributes.tsv" | less -S
```

Count the records and inspect the first runs:

```bash
head -n 5 "$OUTDIR/metadata/derived/runs.tsv"

runs=$(($(wc -l < "$OUTDIR/metadata/derived/runs.tsv") - 1))
samples=$(($(wc -l < "$OUTDIR/metadata/derived/samples.tsv") - 1))

printf 'samples=%s runs=%s\n' "$samples" "$runs"
```

Compare these records with the publication. Does the organism match? Are the sample names recognizable? Is the library strategy the one described in the paper? Are there unexpected pilot samples or repeated runs? The cost of discovering a mismatch is low now and potentially high after a large transfer.

Associated literature is summarized in:

```bash
column -t -s $'\t' \
  "$OUTDIR/metadata/derived/publications.tsv" | less -S
```

`relationships.tsv` and `linked_resources.tsv` inventory connected records; they do not instruct the application to recursively download every linked dataset.

---

## 7. Validate the snapshot

Run:

```bash
sra-bioproject validate "$OUTDIR"
```

This checks metadata checksums, derived products, and manifest consistency. It verifies internal coherence; only the researcher can decide whether the samples fit the scientific question.

---

## 8. Measure before downloading

The most useful first download command downloads nothing:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --dry-run
```

The dry run reports the run count, total SRA size, sequenced bases, destination free space, and manifest path.

Check capacity independently:

```bash
df -h "$OUTDIR"
```

Leave room for partial files, filesystem overhead, logs, and downstream work. FASTQ conversion needs much more headroom because SRA, uncompressed staging FASTQ, compressed output, and scratch data may coexist.

For a first acquisition, the safest policy is to retain verified SRA objects and postpone FASTQ conversion.

---

## 9. Download verified SRA objects

A foreground download is:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2
```

Each run first appears as `SRR....part`. Only after size and MD5 verification is it atomically renamed to `SRR....`. One failed run does not abort the rest; later passes retry only failed accessions.

### Overnight on macOS

Create the log directory before shell redirection:

```bash
mkdir -p "$OUTDIR/logs"

nohup caffeinate -i \
  sra-bioproject download "$OUTDIR/manifest.tsv" \
    --outdir "$OUTDIR" \
    --jobs 2 \
  > "$OUTDIR/logs/launcher.log" 2>&1 &
bg_pid=$!

echo "process id: $bg_pid"
```

### Overnight on Linux

```bash
mkdir -p "$OUTDIR/logs"

nohup \
  sra-bioproject download "$OUTDIR/manifest.tsv" \
    --outdir "$OUTDIR" \
    --jobs 2 \
  > "$OUTDIR/logs/launcher.log" 2>&1 &
bg_pid=$!

echo "process id: $bg_pid"
```

On shared systems, use the institution's scheduler or long-session mechanism when required.

---

## 10. Monitor progress

The application log shows downloader activity:

```bash
tail -f "$OUTDIR/logs/download.log"
```

The launcher log captures shell, command lookup, and redirection failures:

```bash
tail -f "$OUTDIR/logs/launcher.log"
```

Check the process:

```bash
pgrep -af sra-bioproject
```

A rough count is:

```bash
expected=$(($(wc -l < "$OUTDIR/manifest.tsv") - 1))

complete=$(
  find "$OUTDIR/sra" \
    -type f \
    ! -name '*.part' \
    ! -name '*.bad.*' |
  wc -l |
  tr -d ' '
)

printf 'expected=%s complete=%s\n' "$expected" "$complete"
```

This is a progress aid, not an integrity proof. The application performs the authoritative checks.

Follow storage use with:

```bash
du -sh "$OUTDIR"/{sra,tmp,fastq} 2>/dev/null
df -h "$OUTDIR"
```

---

## 11. Resume after interruption

Rerun exactly the same command:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2
```

The application verifies and skips completed objects, resumes suitable `.part` files, promotes exact-size partials only after verification, removes oversized partials, and quarantines invalid completed files as `.bad.<timestamp>`.

Do not delete `.part` files merely because a transfer stopped.

For an unstable connection:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 1
```

Persistent failures are listed in:

```bash
cat "$OUTDIR/logs/failed_accessions.txt"
```

A rerun concentrates on incomplete runs because verified files are skipped.

---

## 12. What the completed folder represents

After SRA acquisition:

```text
PRJNA.../
├── manifest.tsv
├── metadata/
│   ├── snapshot.json
│   ├── raw/
│   ├── derived/
│   └── archive/
├── sra/
│   ├── SRR...
│   └── ...
├── tmp/
└── logs/
    ├── download.log
    └── launcher.log
```

This is more than a directory of sequence files. The raw metadata records what remote services returned. Derived tables make it usable. The manifest records which objects were expected and how they were verified. `snapshot.json` records retrieval time, software version, sources, checksums, warnings, and counts.

Keep these pieces together. Separating sequence files from their manifest and metadata weakens the provenance of the local dataset.

Add a brief human-written note describing the source publication, why the project was acquired, and any decision to include or exclude runs. The application records acquisition facts, not analytical judgement.

---

## Exploring the rest of the CLI

The `snapshot -> validate -> dry-run -> download` route is the usual path. The alternatives below become useful as projects and workflows grow.

## Retrieve metadata without creating a manifest

Use `metadata` for reconnaissance:

```bash
sra-bioproject metadata "$PROJECT" \
  --outdir "$OUTDIR"
```

It preserves and normalizes metadata but does not write `manifest.tsv`. A manifest can later be generated offline:

```bash
sra-bioproject metadata-normalize \
  --metadata-dir "$OUTDIR/metadata" \
  --manifest "$OUTDIR/manifest.tsv"
```

## Search Europe PMC for accession mentions

Explicit NCBI links are the high-confidence publication source. To search for papers that mention the accession without being formally linked:

```bash
sra-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR" \
  --include-literature-search
```

Text-discovered associations remain labelled separately from curated and database links. Inspect them rather than assuming that every mention is the original publication.

## Reuse an existing SRA XML export

```bash
sra-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR" \
  --sra-xml /path/to/existing-sra-export.xml
```

The supplied file becomes `metadata/raw/sra_experiments.xml` and is processed by the same canonical parser.

## Refresh metadata without erasing history

```bash
sra-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR" \
  --refresh
```

The previous metadata state is archived under:

```text
metadata/archive/YYYYMMDDTHHMMSSZ-<8hex>/
```

Sequence files remain untouched. Compare old and new run manifests before acquiring newly added records.
The refresh operation is transactional: the replacement snapshot is built and validated in staging before the swap.

## Rebuild derived products offline

```bash
sra-bioproject metadata-normalize \
  --metadata-dir "$OUTDIR/metadata" \
  --manifest "$OUTDIR/manifest.tsv"
```

This is useful after software upgrades or accidental deletion of derived tables. The command preserves retrieval provenance so derived outputs remain reproducible for a given raw snapshot.

## Convert a standalone XML export to TSV

```bash
sra-bioproject manifest /path/to/export.xml \
  --output /path/to/manifest.tsv
```

Use `--output -` for standard output:

```bash
sra-bioproject manifest /path/to/export.xml \
  --output -
```

The repository also includes:

```bash
python scripts/sra_xml_to_manifest.py \
  /path/to/export.xml \
  --output /path/to/manifest.tsv
```

## Download from XML instead of TSV

```bash
sra-bioproject download /path/to/export.xml \
  --outdir "$OUTDIR" \
  --dry-run
```

Format is inferred from `.xml` or `.tsv`. For an unusual suffix:

```bash
sra-bioproject download /path/to/input.data \
  --input-format xml \
  --outdir "$OUTDIR" \
  --dry-run
```

The TSV manifest is usually preferable for long-term review and comparison.

## Tune concurrency and retry passes

Increase concurrent transfers cautiously:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 4
```

More jobs do not guarantee more speed. Start with two; use one on unstable connections.

Increase batch retry passes when needed:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2 \
  --batch-attempts 5
```

For detailed logs:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --verbose
```

## Tune metadata requests

```bash
sra-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR" \
  --timeout 120 \
  --attempts 6
```

The email and tool can be provided directly, although environment variables are more convenient:

```bash
sra-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR" \
  --email "your.name@example.org" \
  --tool "my-lab-sra-acquisition"
```

## Convert SRA to compressed FASTQ

The conservative two-stage workflow is:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR"

sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --mode fastq \
  --threads 8
```

The second command verifies and skips existing SRA files, converts runs sequentially with `fasterq-dump --split-files`, compresses with `pigz` or `gzip`, tests the archives, and writes completion markers.

SRA files are retained by default. To delete each one only after successful FASTQ conversion:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --mode fastq \
  --threads 8 \
  --delete-sra-after-fastq
```

This is a storage-policy choice, not merely a performance option.

`vdb-validate` runs by default. It can be skipped deliberately:

```bash
sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --mode fastq \
  --skip-vdb-validate
```

---

## Exit statuses

| Status | Meaning |
| ---: | --- |
| `0` | Complete success |
| `1` | General failure or persistent sequence download failure |
| `2` | Invalid input or configuration |
| `3` | Required metadata retrieval incomplete |
| `4` | Snapshot completed with optional metadata missing |
| `5` | Metadata normalization or validation failure |
| `130` | Keyboard interruption |

Status `4` is a prompt to inspect warnings, not an automatic declaration that the SRA manifest is unusable.

---

## A compact copy-and-adapt recipe

```bash
PROJECT="PRJNA831841"       # replace this
DATA_ROOT="/path/to/bioprojects"
OUTDIR="$DATA_ROOT/$PROJECT"

export NCBI_EMAIL="your.name@example.org"

mkdir -p "$OUTDIR"

sra-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR"

# Inspect project.json, samples.tsv, sample_attributes.tsv, and runs.tsv.

sra-bioproject validate "$OUTDIR"

sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --dry-run

sra-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2
```

The responsible workflow is not “find an accession and download everything.” It is “find an accession, understand the project, preserve its provenance, measure the transfer, and then acquire verified objects.”

That habit scales from a handful of runs on a laptop to hundreds of genomes on dedicated storage. More importantly, it leaves your future self with a dataset whose origin and integrity can still be understood after the paper, terminal session, and download night have faded from memory.

---

## Further reading

- [README](../README.md) — installation and command summary
- [Design notes](design.md) — manifest, integrity, recovery, and metadata architecture
- [Troubleshooting](troubleshooting.md) — mounted volumes, TLS failures, retries, partial files, and metadata recovery
- [Development history](development-history.md) — how the downloader evolved and why its recovery behaviour exists
