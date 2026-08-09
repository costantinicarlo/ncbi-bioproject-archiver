# From a Paper to a Reusable Local BioProject Dataset

*A practical guide to `ncbi-bioproject-archiver` 0.3.0 for computational biologists*

A sequencing paper often ends with a deceptively simple sentence: *the sequencing data are available under BioProject PRJNA…*. For a reader interested in reanalysing those data, that accession is an invitation—but it is not yet a dataset.

Between reading the accession and starting a genomic analysis lie several questions that are easy to overlook. Does the BioProject still contain exactly the samples analysed in the paper? Were additional runs deposited later? Which files correspond to lossless sequence data? How much storage will the project actually require? If an overnight download is interrupted, which files can safely be resumed? Six months later, how will you know that the files on disk are still the ones you originally acquired?

`ncbi-bioproject` is intended to make that transition from *published accession* to *reusable local research resource* explicit and reproducible.

If you already understand the archive model and simply want the shortest sequence of commands, see [`quickstart.md`](quickstart.md). This chapter takes the longer route. Its aim is not only to show **what to type**, but to explain **why each step exists**, what biological or archival question it answers, and how the pieces fit together.

The worked example is `PRJNA831841`, for which this repository contains an SRA XML export and a corresponding manifest. The bundled manifest contains 187 whole-genome sequencing runs, with sample aliases such as `Folonzo46` and `Kiribina21`, paired-end genomic libraries, HiSeq X Ten sequencing, expected SRA sizes, MD5 checksums, and download URLs. It is therefore large enough to resemble the kinds of datasets computational biologists actually want to archive rather than a toy example.

Do not start the real PRJNA831841 transfer simply to follow the tutorial. Substitute a smaller BioProject or your project of interest where appropriate, and use the dry-run stage before committing substantial disk space.

## 1. Why version 0.3 became an archiver rather than a better downloader

Understanding the change from v0.2.1 to v0.3.0 makes the rest of the application much easier to understand.

Version 0.2.1 already solved an important problem well. It could retrieve BioProject metadata, preserve the raw responses, derive stable tables, construct a reproducible run manifest, download lossless SRA objects, resume interrupted transfers, and refuse to accept a completed file unless its size and upstream MD5 checksum were correct.

In other words, v0.2.1 could answer a question such as:

> Did the files I asked for arrive correctly during this acquisition?

That is the right question for a downloader.

It is not quite the right question for an archive.

Imagine that the download finishes on Monday. A year later the project directory has been copied to another disk, restored from backup, inspected by several analyses, perhaps refreshed against newer metadata, and inherited by another researcher. The scientifically important questions have changed:

> - What exactly is this directory?
> - Which BioProject does it claim to represent?
> - Which files entered it during the original download, which were resumed from older partial transfers, and which were already present before the current software ever saw them?
> - Which version of the software admitted those files?
> - Which integrity rules were used when the archive was last examined?
> - Have the manifest, metadata, provenance records or authoritative payloads changed since that examination?
> - Can I still make the same integrity claim today?

Those are archive-lifecycle questions.

The conceptual change in v0.3.0 is therefore deeper than adding another checksum or another command. **The boundary of responsibility moves from the lifetime of a download command to the lifetime of the project directory.**

A useful way of expressing the difference is:

```text
v0.2.1

remote data
    ↓
retrieve
    ↓
verify transfer
    ↓
local files
    ↓
done
```

whereas v0.3.0 treats the destination itself as a persistent scientific object:

```text
remote data
    ↓
metadata snapshot
    ↓
archive identity
    ↓
artifact admission
    ↓
archive-wide verification
    ↓
validation attestation
    ↓
status through time
```

This change explains the renamed product. The Python distribution is now `ncbi-bioproject-archiver`, and the canonical command is `ncbi-bioproject`. The old `sra-bioproject` command remains available as a compatibility alias, so old scripts do not abruptly stop working, but it emits a warning encouraging migration.

The Python import namespace remains `sra_bioproject`. That is deliberate compatibility, not an inconsistency.

### Acquisition history is not the same thing as validation history

One of the most important distinctions introduced in v0.3.0 is between **how bytes entered the archive** and **when those bytes were later verified**.

Suppose a 10 GB SRA file had already downloaded 7 GB yesterday and today v0.3.0 resumes the final 3 GB. It would be false for today's software to claim that it acquired all 10 GB. It knows only that it completed and admitted a file whose final contents passed the required checks.

Likewise, if v0.3.0 encounters a perfectly good SRA file produced years ago by v0.2.1, it can verify that file today. It cannot truthfully invent a historical acquisition time or claim that v0.3.0 downloaded bytes that predate it.

The archive therefore distinguishes admission methods such as these:

| Admission method     | What it means                                                                     |
| -------------------- | --------------------------------------------------------------------------------- |
| `downloaded_fresh`   | The complete artifact was downloaded by the current acquisition.                  |
| `resumed_download`   | A partial transfer already existed and the current acquisition completed it.      |
| `promoted_partial`   | A complete `.part` file already existed and was promoted only after verification. |
| `existing`           | A valid final artifact was already present in a managed archive.                  |
| `legacy_observation` | A pre-v0.3 artifact was observed and verified during legacy adoption.             |

This apparently fussy distinction is scientifically important. Provenance should describe what is actually known, not what would make the history look tidier.

### Software version and validation policy are also different things

Another v0.3 distinction is between the **application version** and the **validation policy**.

A future v0.3.1 might fix a help message without changing what constitutes an acceptable archive. There would be no reason for every v0.3.0 verification to become obsolete.

Conversely, suppose a future integrity problem reveals that an additional validation check is necessary. The validation policy could change even if the archive schema remains readable.

For this reason, archive schema versions, acquisition schema versions, attestation schema versions, application versions and validation-policy versions are independent. An archive can retain its historical records while current software decides whether an old attestation is still sufficient.

This is the basis for the `STALE` state discussed later.

## 2. What the application considers the authoritative dataset

For the archive lifecycle to mean anything, the program needs an explicit definition of the payload it is protecting.

For each run, `ncbi-bioproject` selects NCBI's lossless **SRA Normalized** object from the Primary ETL workflow. It does not silently substitute SRA Lite, whose representation is intentionally reduced.

The authoritative local payload is stored as:

```text
sra/<RUN_ACCESSION>
```

For example:

```text
sra/SRR18920076
```

The absence of a `.sra` suffix is intentional.

FASTQ files are useful derivatives, but they are not the archival source of truth in v0.3.0. They can be regenerated from the retained SRA object. This is why the old `--delete-sra-after-fastq` option is now rejected rather than merely discouraged.

That decision captures an important archival principle:

> Preserve the compact, verified, lossless source object; derive analysis formats from it when needed.

FASTQ may be the beginning of a Nextflow, Snakemake, GATK or population-genomics workflow. It is not what `ncbi-bioproject` uses to decide whether the BioProject archive itself is intact.

## 3. Begin with the paper, not with the download command

Suppose a publication relevant to your work contains:

```text
Data are available from NCBI under BioProject PRJNA831841.
```

Before downloading anything, establish what kind of accession you have encountered.

| Prefix     | Usually represents | Typical role                     |
| ---------- | ------------------ | -------------------------------- |
| `PRJNA...` | BioProject         | Study-level discovery hub        |
| `SRP...`   | SRA study          | Sequencing-study grouping        |
| `SAMN...`  | BioSample          | Biological sample and attributes |
| `SRX...`   | SRA experiment     | Library and experimental design  |
| `SRR...`   | SRA run            | Individual sequence run          |

A BioProject is usually the most useful starting point because it connects several of these layers.

But a BioProject should not automatically be equated with *the dataset used in the paper*. Deposits can grow after publication. Pilot samples may coexist with final samples. A project may contain several library strategies or sequencing batches. Related datasets may have been added later.

Your first scientific task is therefore to compare what NCBI currently describes with what the publication says was analysed.

It is useful to record the human scientific context somewhere outside the machine-generated archive:

```text
Paper: <citation or DOI>
BioProject: PRJNA831841
Why I want the data: <scientific question>
Expected organism/populations/treatments: <short description>
Relevant sample subset, if any: <notes>
```

The software records technical provenance. It cannot know why these data matter to your research.

## 4. Install the application without entangling it with your analysis environment

The application requires Python 3.9 or newer. `curl` is required for sequence acquisition.

From a checked-out release tree, a small virtual environment is sufficient:

```bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install .
```

Check the canonical command:

```bash
ncbi-bioproject --help
```

The compatibility command also exists:

```bash
sra-bioproject --help
```

but new scripts and documentation should use `ncbi-bioproject`.

Metadata retrieval and SRA downloading do not require the SRA Toolkit. FASTQ conversion requires `fasterq-dump` and either `pigz` or `gzip`. FASTQ conversion also runs `vdb-validate` by default unless you deliberately request `--skip-vdb-validate`.

The stronger archive command:

```bash
ncbi-bioproject verify --deep ...
```

also requires `vdb-validate`.

## 5. Choose a durable location before the archive acquires an identity

For an accession-centred archive, using the BioProject accession as the directory name is simple and robust.

On a Linux workstation you might use:

```bash
PROJECT="PRJNA831841"
DATA_ROOT="/data/bioprojects"
OUTDIR="$DATA_ROOT/$PROJECT"

mkdir -p "$OUTDIR"
```

On a macOS workstation with an external data volume:

```bash
PROJECT="PRJNA831841"
DATA_ROOT="/Volumes/Bioinfo-1"
OUTDIR="$DATA_ROOT/$PROJECT"

mkdir -p "$OUTDIR"
```

Before retrieving anything substantial:

```bash
test -d "$DATA_ROOT" && test -w "$DATA_ROOT" \
  && echo "Destination is present and writable"

df -h "$DATA_ROOT"
```

Paths below `/Volumes` receive an additional macOS safety check. This protects against a particularly unpleasant failure mode: an external volume is absent or misspelled and a program quietly creates data somewhere on the internal filesystem instead.

An empty project directory is fine. Empty operational directories such as `logs/`, `tmp/` or `sra/` are also recognized as harmless scaffolding. If a destination contains unrelated unexplained material and cannot safely be classified as new, managed or legacy, v0.3.0 refuses to mutate it rather than guessing what you intended.

## 6. Identify your metadata requests to NCBI

For programmatic retrievals, provide a contact email:

```bash
export NCBI_EMAIL="your.name@example.org"
export NCBI_TOOL="ncbi-bioproject"
```

An NCBI API key is optional:

```bash
export NCBI_API_KEY="..."
```

Do not place API keys in scripts committed to a repository.

## 7. Create the first snapshot: this is where the archive begins

For the normal publication-to-archive workflow, start with:

```bash
ncbi-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR"
```

This retrieves the BioProject and related public metadata, preserves raw service responses, derives stable tables, and writes `manifest.tsv`.

It **does not download sequence data**.

A new native destination also receives something v0.2.1 did not have:

```text
provenance/archive.json
```

This establishes a persistent archive identity.

At this point the directory might resemble:

```text
PRJNA831841/
├── manifest.tsv
├── metadata/
│   ├── snapshot.json
│   ├── raw/
│   └── derived/
└── provenance/
    └── archive.json
```

The archive exists, but its authoritative SRA payload has not yet been acquired and attested.

Ask it what state it is in:

```bash
ncbi-bioproject status "$OUTDIR"
```

A normal new snapshot should report:

```text
BioProject: PRJNA831841
Archive status: UNVERIFIED
```

That is not an error in the scientific sense. It is an honest statement.

The archive knows **who it is**, but no archive-wide integrity attestation yet proves the authoritative payload.

Notice an important command-line detail: `status` returns exit status `6` for actionable non-current states such as `UNVERIFIED`. If you use it in shell automation, do not assume that every non-zero status means that the program crashed.

## 8. Raw metadata and derived metadata serve different purposes

Look inside:

```bash
find "$OUTDIR/metadata" -maxdepth 2 -type f | sort
```

The `raw/` directory preserves what remote services returned. These files are evidence of the metadata state retrieved at that time.

The `derived/` directory contains normalized products that are much easier to inspect and script against. Typical files include:

```text
metadata/derived/project.json
metadata/derived/samples.tsv
metadata/derived/sample_attributes.tsv
metadata/derived/runs.tsv
metadata/derived/publications.tsv
metadata/derived/relationships.tsv
metadata/derived/linked_resources.tsv
```

The distinction matters.

Raw responses maximize fidelity to the source. Derived files maximize usability. Because the raw material is retained, future software can reinterpret it without pretending that an old normalized table was the original database response.

`snapshot.json` connects these layers by recording retrieval information and checksums for the files belonging to the snapshot.

## 9. Inspect the biology before allocating hundreds of gigabytes

Downloading should not be the first time you notice that a BioProject contains unexpected samples.

Begin with the project description:

```bash
python -m json.tool \
  "$OUTDIR/metadata/derived/project.json" | less
```

Inspect the sample table:

```bash
column -t -s $'\t' \
  "$OUTDIR/metadata/derived/samples.tsv" | less -S
```

BioSample records are heterogeneous, so the long-form attribute table is often even more informative:

```bash
column -t -s $'\t' \
  "$OUTDIR/metadata/derived/sample_attributes.tsv" | less -S
```

Then inspect the run table and manifest:

```bash
head -n 10 "$OUTDIR/metadata/derived/runs.tsv"

head -n 10 "$OUTDIR/manifest.tsv"
```

In the repository's PRJNA831841 example, the manifest contains rows resembling:

```text
SRR18920076 ... Folonzo46 ... WGS  GENOMIC  PAIRED  HiSeq X Ten ...
SRR18920254 ... Kiribina21 ... WGS  GENOMIC  PAIRED  HiSeq X Ten ...
```

Already this tells you something useful. There are recognizable sample-name series, consistent whole-genome sequencing libraries, paired-end data, and explicit BioSample and experiment accessions.

A simple inspection can reveal the broad structure:

```bash
runs=$(( $(wc -l < "$OUTDIR/manifest.tsv") - 1 ))
printf 'runs=%s\n' "$runs"

cut -f6 "$OUTDIR/manifest.tsv" \
  | tail -n +2 \
  | sort \
  | uniq -c

cut -f8 "$OUTDIR/manifest.tsv" \
  | tail -n +2 \
  | sort \
  | uniq -c

cut -f9 "$OUTDIR/manifest.tsv" \
  | tail -n +2 \
  | sort \
  | uniq -c
```

For the bundled example, the run count is 187.

You can also ask how much sequence and SRA storage the manifest represents:

```bash
awk -F'\t' '
NR > 1 {
    bases += $10
    bytes += $12
}
END {
    printf "Sequenced bases: %.3f Tbp\n", bases / 1e12
    printf "Normalized SRA: %.2f GiB\n", bytes / 1024 / 1024 / 1024
}
' "$OUTDIR/manifest.tsv"
```

This is not merely bookkeeping. It is the point where the publication, the biological design and the computational cost should meet.

Ask questions such as: do the sample aliases correspond to the populations in the paper? Are the number of runs and samples plausible? Is every library `WGS`, or has RNA-seq or another strategy entered the project? Are there samples that appear to have been deposited after publication? Do several runs belong to one BioSample?

Discovering an unexpected subset before downloading is cheap. Discovering it after transferring hundreds of gigabytes is not.

## 10. Validate the metadata separately from validating the sequence archive

Once the metadata looks biologically plausible, validate the snapshot:

```bash
ncbi-bioproject validate "$OUTDIR"
```

This checks the internal structure and consistency of the local metadata snapshot.

The conceptual distinction is important:

```text
validate
    asks:
    "Is the metadata snapshot internally coherent?"

verify
    asks:
    "Does the BioProject archive currently satisfy the
     archive integrity policy?"
```

A snapshot can be perfectly valid while the archive is still `UNVERIFIED` because sequence data have not yet been acquired.

Conversely, metadata corruption is an archive-control problem even if the SRA bytes themselves remain intact.

The two commands therefore answer different questions and should not be collapsed into a single idea of “validation”.

## 11. Use the dry run as a scientific-computing planning tool

Before a large transfer:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --dry-run
```

Because the snapshot has already initialized archive identity, the program can infer the BioProject accession from the destination.

If instead you start directly from a standalone XML or TSV in a completely new directory, provide the identity explicitly:

```bash
ncbi-bioproject download /path/to/export.xml \
  --outdir /data/new-project \
  --bioproject PRJNA831841 \
  --dry-run
```

The dry run performs no acquisition or provenance mutation. It reports the expected runs, normalized SRA size, sequence volume and available storage.

Treat this as part of experimental planning, not as an optional convenience.

A project that fits comfortably as SRA may not fit once converted to FASTQ. During FASTQ conversion, several representations can temporarily coexist:

```text
authoritative SRA
+ uncompressed FASTQ staging
+ compressed FASTQ
+ temporary toolkit files
```

If storage is tight, archive the SRA first and postpone FASTQ production until the downstream workflow actually needs it.

## 12. Download the authoritative SRA objects

For the normal acquisition:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2
```

Each run is first written as a partial file:

```text
sra/SRR18920076.part
```

Only after the expected size and NCBI MD5 agree is it promoted atomically to:

```text
sra/SRR18920076
```

A final-looking filename is therefore never accepted merely because it exists.

The downloader also calculates a local SHA-256 while inspecting the completed artifact. That digest becomes part of the archive's local integrity history.

Two concurrent jobs are a deliberately conservative default. Large public object stores and local storage systems often benefit less from aggressive concurrency than expected. On a fragile connection, reducing concurrency can be more productive:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 1
```

If your network and disk can comfortably sustain more traffic:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 4
```

Higher is not automatically better.

## 13. What happens when a download is interrupted

Long genomic transfers eventually encounter reality: Wi-Fi disappears, a VPN reconnects, a remote endpoint resets a connection, a laptop reboots, or an external disk is temporarily unavailable.

The correct recovery procedure is usually delightfully uninteresting:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2
```

Run the same command again.

A valid completed object is checked and skipped.

A suitable partial file is resumed.

An exact-size `.part` file is not blindly trusted; it is promoted only if its integrity checks succeed.

An invalid completed object is quarantined rather than silently overwritten:

```text
SRR....bad.<timestamp>
```

Persistent failures are summarized in:

```text
logs/failed_accessions.txt
```

Do not routinely delete `.part` files after an interrupted acquisition. Their existence is what allows continuation rather than retransmission.

### Running overnight on macOS

Shell redirection happens before Python begins, so create the log directory before redirecting into it:

```bash
mkdir -p "$OUTDIR/logs"

nohup caffeinate -i \
  ncbi-bioproject download "$OUTDIR/manifest.tsv" \
    --outdir "$OUTDIR" \
    --jobs 2 \
  > "$OUTDIR/logs/launcher.log" 2>&1 &
```

Watch the application log:

```bash
tail -f "$OUTDIR/logs/download.log"
```

Watch the shell-level launcher log if the program failed before normal logging began:

```bash
tail -f "$OUTDIR/logs/launcher.log"
```

Check that the process still exists:

```bash
pgrep -af ncbi-bioproject
```

A rough progress count is:

```bash
expected=$(( $(wc -l < "$OUTDIR/manifest.tsv") - 1 ))

complete=$(
  find "$OUTDIR/sra" \
    -type f \
    ! -name '*.part' \
    ! -name '*.bad.*' \
  | wc -l \
  | tr -d ' '
)

printf 'expected=%s complete=%s\n' "$expected" "$complete"
```

This count is useful for watching a transfer. It is not proof of integrity.

## 14. The acquisition ledger: what v0.3 remembers that a directory listing cannot

After downloads have been admitted into a native managed archive, inspect:

```text
provenance/acquisitions.jsonl
```

For example:

```bash
python -m json.tool \
  < <(head -n 1 "$OUTDIR/provenance/acquisitions.jsonl")
```

or simply:

```bash
head "$OUTDIR/provenance/acquisitions.jsonl"
```

Each record associates an accession and relative path with expected integrity information, observed integrity information, admission method, archive identity, application identity and admission time.

This is one of the principal differences between possessing files and possessing an archive.

A filesystem can tell you:

```text
SRR18920076 exists.
```

The ledger can tell you, in structured form:

```text
this artifact belongs to archive X,
it represents accession SRR18920076,
this is the expected upstream MD5 and size,
this is the observed SHA-256,
this is how it entered the managed archive,
and this is when the archive admitted it.
```

The distinction becomes especially valuable when an archive is assembled over several sessions or software versions.

A harmless later rerun that merely encounters the same unchanged file as `existing` does not manufacture another acquisition event. A genuine reacquisition does remain part of history even if it eventually produces the same SHA-256.

## 15. Download success is not yet the same as archive verification

When a native SRA download completes, run:

```bash
ncbi-bioproject verify "$OUTDIR"
```

This is the point at which v0.3 changes the unit of reasoning from individual downloads to the archive as a whole.

Standard verification examines the authoritative run set from the manifest and checks, among other things, that the required SRA files exist, that paths are safe, that expected sizes agree, that upstream MD5 values agree, that local SHA-256 baselines agree where available, and that the metadata and archive identities are coherent.

A successful run writes an append-only validation attestation under:

```text
provenance/validations/
```

The managed directory now resembles:

```text
PRJNA831841/
├── manifest.tsv
├── metadata/
│   ├── snapshot.json
│   ├── raw/
│   ├── derived/
│   └── archive/
├── provenance/
│   ├── archive.json
│   ├── acquisitions.jsonl
│   └── validations/
│       └── <timestamp>-<id>.json
├── sra/
│   ├── SRR18920076
│   ├── SRR18920077
│   └── ...
├── fastq/
├── tmp/
└── logs/
```

Then ask:

```bash
ncbi-bioproject status "$OUTDIR"
```

A current successful archive should report:

```text
BioProject: PRJNA831841
Archive status: VERIFIED
```

Only now does `status` return zero.

## 16. What a verification attestation actually means

A PASS attestation is not merely a line saying “checksums were okay”.

It records the validation-policy version, application and version, verification mode, archive identity, BioProject identity, completion time, control fingerprint, quick payload fingerprint, number of runs examined, per-run results and failures.

Conceptually, it says:

> Under validation policy P, application V examined archive A in state C, observed payload state Q, and obtained PASS.

That wording matters because archive state can subsequently change.

The attestation is historical evidence. It is not continuously true by magic.

`status` asks whether the latest completed attestation still applies to what is present now.

## 17. `status` is intentionally cheap; `verify` is intentionally expensive

Hashing hundreds of gigabytes every time you want to inspect an archive would make routine status checks impractical.

Therefore:

```bash
ncbi-bioproject status "$OUTDIR"
```

does **not** reread every byte of every SRA object.

It validates the archive-control state and the historical attestation set, then compares the current state with the attestation using a quick payload sentinel. That sentinel includes properties such as expected relative path, existence, size and nanosecond modification time.

This makes `status` useful enough to run frequently.

It also makes it deliberately conservative.

A file whose bytes did not change but whose timestamp changed can cause the archive to become:

```text
STALE
```

That does not mean the data are corrupt.

It means:

> The previous cryptographic claim no longer describes the observable state closely enough for the software to reuse it without rechecking the bytes.

The remedy is:

```bash
ncbi-bioproject verify "$OUTDIR"
```

If the bytes are still correct, verification produces a new PASS attestation for the new state.

This separation between inexpensive surveillance and expensive cryptographic verification is fundamental to making lifecycle checks practical for terabyte-scale archives.

## 18. Understanding the lifecycle states

`status` can report seven states:

| State           | Interpretation                                                                                            |
| --------------- | --------------------------------------------------------------------------------------------------------- |
| `UNINITIALIZED` | No managed archive and no recognizable legacy archive exists.                                             |
| `LEGACY`        | Recognizable pre-v0.3 material exists but has not been adopted into managed provenance.                   |
| `UNVERIFIED`    | Managed archive identity exists, but no current applicable integrity attestation does.                    |
| `STALE`         | A previous attestation exists but no longer applies to the current policy or observable archive state.    |
| `FAILED`        | A current applicable verification actually found integrity failures.                                      |
| `VERIFIED`      | The latest completed attestation is a current PASS under the present validation policy and archive state. |
| `INVALID`       | Provenance or control state cannot be interpreted safely.                                                 |

Several distinctions are worth dwelling on.

### `STALE` is not `FAILED`

Suppose an archive was verified successfully, then its metadata snapshot was refreshed.

The old verification did not test the new metadata state, so it is no longer current.

That is `STALE`, not `FAILED`.

Similarly, copying an archive through a process that alters modification timestamps can conservatively produce `STALE` even if every sequence byte is identical.

### `FAILED` is not `INVALID`

`FAILED` means the archive was interpretable and verification found a real problem—for example a missing SRA object, a size mismatch, an MD5 mismatch, or a SHA-256 baseline mismatch.

`INVALID` is more fundamental. It means the program cannot safely interpret the archive-control or provenance structure—for example because identities contradict each other or a provenance record is malformed.

This fail-closed distinction is deliberate. When provenance itself cannot be trusted enough to interpret, the software does not invent a best guess.

### A failure can later become stale

Suppose verification produces `FAILED` because one SRA object is damaged. You then replace that file.

The old failure attestation described the old archive state. Once the state changes, it is no longer a current statement. `status` can therefore become `STALE` until verification is run again.

That is exactly what an attestation-based lifecycle should do.

## 19. Deep verification with the SRA Toolkit

Standard verification uses the archive's expected size, upstream MD5 and local SHA-256 evidence.

For a stronger SRA-specific inspection:

```bash
ncbi-bioproject verify "$OUTDIR" --deep
```

Deep mode additionally runs `vdb-validate` against every authoritative SRA object.

Check that the command exists first if you wish:

```bash
command -v vdb-validate
```

If `vdb-validate` is unavailable, deep verification stops as an environment/configuration problem. It does **not** write an integrity-failure attestation pretending that the SRA data themselves failed.

This distinction prevents “I could not run the validator” from becoming “the scientific payload is corrupt”.

Use deep verification when SRA Toolkit structural validation is valuable—for example after long-term storage, migration between storage systems, or before beginning an expensive downstream project.

## 20. What `VERIFIED` does—and does not—promise

A `VERIFIED` archive makes a strong local statement:

> The current local archive matches its recorded control state and authoritative payload integrity expectations under the current validation policy.

It does **not** mean that NCBI has never changed since the snapshot was taken.

`verify` is an archive-integrity operation, not an upstream synchronization service.

Likewise, v0.3.0 does not digitally sign attestations. It provides structured integrity and provenance management, but it is not a cryptographically signed chain of custody designed to resist an attacker who can maliciously rewrite the entire archive and all of its provenance.

Those are different problems.

For ordinary scientific reproducibility, the important point is that local state, acquisition history and validation history are explicit rather than implicit.

## 21. Produce FASTQ when analysis needs it, not because archiving requires it

Many downstream workflows expect FASTQ rather than SRA.

Once the SRA archive is safely acquired, you can derive compressed FASTQ:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --mode fastq \
  --threads 8
```

The downloader first checks the existing SRA objects, then processes runs sequentially. Conversion uses:

```text
fasterq-dump --split-files
```

followed by `pigz` when available or `gzip` otherwise. Compressed output is gzip-tested, and completion markers describe the expected output files.

`vdb-validate` runs by default before conversion. Skipping it requires an explicit decision:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --mode fastq \
  --threads 8 \
  --skip-vdb-validate
```

FASTQ conversion is deliberately sequential because simultaneous `fasterq-dump` jobs can create severe temporary-space and I/O pressure.

Most importantly:

```text
SRA remains.
```

This command is invalid in v0.3.0:

```bash
ncbi-bioproject download ... \
  --mode fastq \
  --delete-sra-after-fastq
```

The program rejects it because deleting the authoritative SRA object would violate the archive contract.

### Treat FASTQ as an interface to analysis, not as the archive itself

A useful mental architecture is:

```text
BioProject archive
    authoritative SRA
           ↓
      FASTQ derivative
           ↓
    workflow / scratch
           ↓
 BAM / CRAM / VCF / analyses
```

Your analysis repository can evolve rapidly. Parameters change, reference genomes change, aligners change and intermediate files are regenerated.

The archive should be much more conservative.

Keeping these roles conceptually distinct makes it easier to reproduce analyses without turning the archival source into a working directory full of mutable pipeline products.

## 22. Real-world case: the BioProject has changed since you first archived it

Public database records are not frozen merely because you downloaded them.

Months later you may want to see whether BioProject metadata have changed:

```bash
ncbi-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR" \
  --refresh
```

Refresh is transactional. The replacement snapshot is constructed and validated before replacing the current one, and the prior metadata state is archived under:

```text
metadata/archive/
```

Sequence payloads are left alone.

Now ask:

```bash
ncbi-bioproject status "$OUTDIR"
```

A previous PASS will normally no longer describe the refreshed metadata state, so `STALE` is the conservative result.

That is desirable.

Inspect the new manifest against the previous archived metadata. Perhaps nothing relevant changed. Perhaps one field changed. Perhaps new runs appeared.

Validate the new metadata:

```bash
ncbi-bioproject validate "$OUTDIR"
```

Then reassess storage:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --dry-run
```

Acquire anything now required:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2
```

Finally reattest:

```bash
ncbi-bioproject verify "$OUTDIR"
ncbi-bioproject status "$OUTDIR"
```

This is why `STALE` is a useful lifecycle state rather than an inconvenience. It tells you that an old scientific integrity statement should not silently migrate across a changed archive definition.

## 23. Real-world case: you want to inspect a BioProject but are not ready to download it

Sometimes the question is exploratory:

> Is this project even relevant enough to justify 500 GB of local storage?

Use:

```bash
ncbi-bioproject metadata "$PROJECT" \
  --outdir "$OUTDIR"
```

Unlike `snapshot`, the `metadata` command does not write `manifest.tsv`.

You still receive preserved and normalized metadata, and a new native destination receives archive identity. It is therefore a legitimate managed metadata-only archive.

Its lifecycle state is:

```text
UNVERIFIED
```

That is not a demand to download anything immediately.

It simply means no authoritative sequence archive has yet been attested.

If you later decide to proceed, you can regenerate derived products and a manifest from stored metadata:

```bash
ncbi-bioproject metadata-normalize \
  --metadata-dir "$OUTDIR/metadata" \
  --manifest "$OUTDIR/manifest.tsv"
```

Then continue with dry-run, acquisition and verification.

This workflow is useful when screening several candidate BioProjects before deciding which deserve local archival storage.

## 24. Real-world case: you already possess an SRA XML export

Perhaps a collaborator gives you an XML export used in an older analysis, and you want that exact run description to enter your local archival workflow.

You can reuse it during snapshot creation:

```bash
ncbi-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR" \
  --sra-xml /path/to/export.xml
```

The XML enters the preserved metadata structure and is processed through the same canonical parser.

If all you need is a manifest:

```bash
ncbi-bioproject manifest /path/to/export.xml \
  --output /path/to/manifest.tsv
```

For an XML or TSV whose filename does not reveal its format:

```bash
ncbi-bioproject download /path/to/input.data \
  --input-format xml \
  --outdir /data/PRJNAxxxxxx \
  --bioproject PRJNAxxxxxx \
  --dry-run
```

Long-term, the TSV manifest is usually easier for humans and scripts to inspect than the source XML, but retaining the raw XML preserves where that interpretation came from.

## 25. Real-world case: you have a v0.2.1 archive containing good data

This is one of the main reasons legacy handling in v0.3.0 is deliberately strict.

Suppose your existing directory looks like:

```text
PRJNAxxxxxx/
├── manifest.tsv
├── metadata/
│   └── ...
├── sra/
│   ├── SRR...
│   └── ...
└── logs/
```

There is no:

```text
provenance/archive.json
```

Ask:

```bash
ncbi-bioproject status /path/to/PRJNAxxxxxx
```

The answer should be:

```text
LEGACY
```

Crucially, v0.3.0 does not say:

> These files are old, but I will pretend that I downloaded them.

Nor does merely refreshing metadata convert the directory into a managed archive.

Instead, verify it:

```bash
ncbi-bioproject verify /path/to/PRJNAxxxxxx
```

If BioProject identity cannot be recovered safely from the existing snapshot, provide it explicitly:

```bash
ncbi-bioproject verify /path/to/PRJNAxxxxxx \
  --bioproject PRJNAxxxxxx
```

The entire authoritative SRA set is examined **before managed provenance is published**.

If even one required payload fails, the adoption fails and the archive remains genuinely:

```text
LEGACY
```

No partial `archive.json`, half-written acquisition ledger or misleading PASS attestation is left behind.

If every required artifact passes, v0.3.0 atomically creates the managed provenance bundle and the first PASS attestation.

Existing historical payloads are recorded as:

```text
legacy_observation
```

because the software knows when it observed and admitted them, not when their bytes were originally acquired.

The first verification also establishes a trustworthy local SHA-256 baseline.

This is archival honesty in practice.

## 26. Real-world case: the legacy archive is incomplete

Suppose an old v0.2 archive contains most but not all of the manifest's SRA objects.

You can rerun acquisition:

```bash
ncbi-bioproject download "$OLD_ARCHIVE/manifest.tsv" \
  --outdir "$OLD_ARCHIVE" \
  --bioproject PRJNAxxxxxx
```

Verified old files are retained. Missing objects are downloaded. Partial objects can be resumed.

If the entire required set is finally successful, v0.3.0 can bootstrap the legacy destination.

The resulting provenance remains nuanced.

Files that truly existed before v0.3 become `legacy_observation`.

A file downloaded completely by the current command can be recorded as `downloaded_fresh`.

A transfer completed from an inherited partial file remains `resumed_download`.

An exact-size inherited partial promoted after validation can remain `promoted_partial`.

Thus one BioProject archive can accurately record multiple acquisition histories without forcing them into a fictitious single “download date”.

## 27. Real-world case: someone gives you a directory full of SRA files but little else

A directory of data files is not automatically enough to establish an archive.

Suppose you receive:

```text
mystery-project/
└── sra/
    ├── SRR...
    ├── SRR...
    └── ...
```

There is no manifest telling the application which authoritative objects are expected, what sizes NCBI declared, or which upstream MD5 belongs to each accession.

The directory may be recognizable as legacy material, but verification cannot safely manufacture those missing facts.

If you know the BioProject, reconstruct metadata and a manifest first:

```bash
ncbi-bioproject snapshot PRJNAxxxxxx \
  --outdir /path/to/mystery-project
```

Because SRA holdings already make this recognizable as a legacy destination, snapshotting metadata does **not** silently create managed provenance.

The directory remains `LEGACY`.

Inspect the reconstructed manifest carefully against what you were given, then run:

```bash
ncbi-bioproject verify /path/to/mystery-project \
  --bioproject PRJNAxxxxxx
```

This is a good example of the philosophical difference between an archiver and a convenience script. When essential provenance is missing, the safest action is sometimes to refuse to guess.

## 28. Real-world case: `status` suddenly says `STALE`

Suppose yesterday:

```bash
ncbi-bioproject status "$OUTDIR"
```

returned:

```text
VERIFIED
```

Today it returns:

```text
STALE
```

Do not immediately assume sequence corruption.

Ask what changed.

Perhaps you refreshed metadata. Perhaps a backup/restore operation changed file timestamps. Perhaps a management script touched an SRA file without altering its bytes. Perhaps an acquisition ledger legitimately gained a new event.

`status` is designed to say:

> Something about the current observable archive no longer matches the state covered by the previous attestation.

Run:

```bash
ncbi-bioproject verify "$OUTDIR"
```

If verification succeeds, the archive receives a new PASS attestation and becomes current again.

This makes `STALE` useful as a low-cost trigger for expensive revalidation.

## 29. Real-world case: verification says `FAILED`

A `FAILED` state is more specific.

It means the archive could be interpreted, verification ran, and an actual integrity problem was found.

Inspect the newest file under:

```text
provenance/validations/
```

The attestation contains per-run information and a failures list.

Possible causes include a missing SRA object, size mismatch, upstream MD5 mismatch, local SHA-256 baseline mismatch, or a deep `vdb-validate` failure.

Resolve the underlying payload problem rather than editing provenance records.

After repair or reacquisition, run:

```bash
ncbi-bioproject verify "$OUTDIR"
```

again.

If the archive changed after the failure, the previous FAIL may first become `STALE`; a fresh verification is what establishes the new state.

## 30. Real-world case: status says `INVALID`

`INVALID` deserves more caution than `FAILED`.

It means the application cannot safely interpret archive provenance or control state.

Do not “fix” this by deleting `archive.json` or editing JSON until the status becomes more pleasant.

Investigate first.

Look at:

```text
provenance/archive.json
provenance/acquisitions.jsonl
provenance/validations/
manifest.tsv
metadata/snapshot.json
```

An `INVALID` state may indicate malformed structured provenance, contradictory archive identities, an unsupported schema, unsafe paths, or another condition where proceeding would require guessing.

For a preservation tool, guessing about provenance is worse than stopping.

## 31. Real-world case: you want to search for the publication associated with the accession

NCBI's curated database links are preferred when they exist, but literature relationships are not always complete.

You can add an opt-in Europe PMC accession search:

```bash
ncbi-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR" \
  --include-literature-search
```

Text-discovered associations are kept distinct from curated/database relationships.

This is scientifically useful because “this accession appears in this paper's text” is not the same evidence as “NCBI formally links this publication to this BioProject”.

Inspect:

```bash
column -t -s $'\t' \
  "$OUTDIR/metadata/derived/publications.tsv" | less -S
```

and, where useful:

```bash
column -t -s $'\t' \
  "$OUTDIR/metadata/derived/relationships.tsv" | less -S
```

The software preserves the distinction so you can make the biological judgement.

## 32. Keep the archive conservative and the analysis workspace flexible

An archive and an analysis project have different jobs.

The archive should answer:

> What did I preserve, from where, under which identity, and does it still verify?

An analysis workspace should answer:

> What transformations am I performing today to answer my scientific question?

That suggests a healthy separation such as:

```text
ARCHIVE/
└── PRJNA831841/
    ├── metadata/
    ├── provenance/
    ├── manifest.tsv
    ├── sra/
    └── fastq/

ANALYSIS/
└── my-population-genomics-project/
    ├── workflow/
    ├── config/
    ├── results/
    └── ...
```

It is perfectly reasonable for a pipeline to read or link to FASTQ derived from the archive. It is less desirable for transient alignment files, VCF experiments, plots and notebooks to accumulate inside the archival BioProject directory.

In particular, avoid casually modifying these managed elements:

```text
manifest.tsv
metadata/snapshot.json
metadata/raw/*
metadata/derived/*
provenance/*
sra/*
```

If they legitimately change, lifecycle state should change too.

FASTQ is explicitly derived and is not currently what archive verification attests. A `VERIFIED` archive does not imply that every downstream FASTQ, BAM or VCF has independently been certified.

## 33. Copying or restoring an archive

A BioProject archive can of course be backed up or moved.

Because the lifecycle uses a quick payload sentinel containing modification-time information, some copy or restore operations may conservatively turn:

```text
VERIFIED
```

into:

```text
STALE
```

even when file contents are unchanged.

This is preferable to assuming that a migration preserved all properties relevant to the previous attestation.

After a major storage migration or restore:

```bash
ncbi-bioproject status "$OUTDIR"
```

If the result is `STALE`, reverify:

```bash
ncbi-bioproject verify "$OUTDIR"
```

For particularly important long-term holdings, consider:

```bash
ncbi-bioproject verify "$OUTDIR" --deep
```

after migration.

## 34. Exit statuses matter when you automate this

The CLI uses exit values to distinguish operational outcomes:

|  Exit | Meaning                                                                                               |
| ----: | ----------------------------------------------------------------------------------------------------- |
|   `0` | Command succeeded; for `status`, archive is currently `VERIFIED`.                                     |
|   `1` | General command or persistent download failure.                                                       |
|   `2` | Invalid input, ambiguous configuration, or unmet verification prerequisite.                           |
|   `3` | Required metadata retrieval was incomplete.                                                           |
|   `4` | Snapshot usable but optional metadata were missing.                                                   |
|   `5` | Metadata validation, archive integrity verification, or `INVALID` status.                             |
|   `6` | `status` reports an actionable non-current state such as `LEGACY`, `UNVERIFIED`, `STALE` or `FAILED`. |
| `130` | Keyboard interruption.                                                                                |

This means a shell such as:

```bash
set -e
ncbi-bioproject status "$OUTDIR"
```

will stop when an archive is `UNVERIFIED` or `STALE`, even though the application itself behaved correctly.

For automation, interpret status explicitly rather than treating every non-zero value as equivalent.

## 35. A complete publication-to-archive session

The following is the core workflow collected in one place. The preceding sections explain why each command exists.

Start with the accession:

```bash
PROJECT="PRJNA831841"
DATA_ROOT="/Volumes/Bioinfo-1"
OUTDIR="$DATA_ROOT/$PROJECT"

export NCBI_EMAIL="your.name@example.org"

mkdir -p "$OUTDIR"
```

Create the metadata snapshot and manifest:

```bash
ncbi-bioproject snapshot "$PROJECT" \
  --outdir "$OUTDIR"
```

Confirm that this is now a managed but unverified archive:

```bash
ncbi-bioproject status "$OUTDIR"
```

Inspect the biology:

```bash
python -m json.tool \
  "$OUTDIR/metadata/derived/project.json" | less

column -t -s $'\t' \
  "$OUTDIR/metadata/derived/samples.tsv" | less -S

column -t -s $'\t' \
  "$OUTDIR/metadata/derived/sample_attributes.tsv" | less -S

head "$OUTDIR/manifest.tsv"
```

Validate metadata:

```bash
ncbi-bioproject validate "$OUTDIR"
```

Measure the acquisition before starting it:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --dry-run
```

Acquire the authoritative payloads:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --jobs 2
```

Verify the archive cryptographically:

```bash
ncbi-bioproject verify "$OUTDIR"
```

Confirm its lifecycle state:

```bash
ncbi-bioproject status "$OUTDIR"
```

Only when your downstream workflow requires it, derive FASTQ:

```bash
ncbi-bioproject download "$OUTDIR/manifest.tsv" \
  --outdir "$OUTDIR" \
  --mode fastq \
  --threads 8
```

At that point you have something more useful than “some downloaded sequencing files”.

You have an archive with a persistent identity, preserved source metadata, a reproducible acquisition manifest, authoritative SRA objects, per-artifact admission history, local SHA-256 evidence, and archive-wide validation attestations that can later be tested against the state you actually have.

## 36. The habit v0.3 is trying to encourage

The easiest way to use public genomic data is:

```text
find accession
→ download files
→ analyse
```

The difficulty appears later, when someone—including your future self—asks what those files are and whether they can still be trusted.

A more durable scientific habit is:

```text
find accession
→ understand the study
→ preserve metadata
→ define the expected dataset
→ measure the acquisition
→ acquire lossless source objects
→ record how they entered the archive
→ verify the complete archive
→ derive analysis formats
→ periodically ask whether the old attestation still applies
```

That is the conceptual reason `ncbi-sra-bioproject-downloader` evolved into `ncbi-bioproject-archiver`.

Version 0.2.1 concentrated on making the **transfer** reproducible and safe.

Version 0.3.0 extends the same discipline to the **life of the local dataset after the transfer is over**.

The distinction becomes increasingly important as genomic datasets become too large to reacquire casually, analyses extend over years, storage systems are migrated, and archived public data become inputs to several independent projects.

A sequencing archive should not depend on remembering what happened in an old terminal window.

Its identity, history and current integrity state should travel with the data.

## Further reading

For the shortest operational route, see [`quickstart.md`](quickstart.md). The architectural rationale and lifecycle invariants are described in [`decisions/archive-lifecycle.md`](decisions/archive-lifecycle.md). The lower-level implementation and metadata structure are summarized in [`design.md`](design.md), while recovery from network, storage and archive-state problems is covered in [`troubleshooting.md`](troubleshooting.md).

The repository [`README`](../README.md) remains the compact reference for installation, commands, output layout and current limitations.
