# Troubleshooting

## Destination Problems

List mounted macOS volumes and check the requested spelling:

```bash
ls -la /Volumes
test -d /Volumes/Research && test -w /Volumes/Research
```

"Destination volume is not mounted" usually means the disk is detached or its name is misspelled. A permission error means the mount exists but the current user cannot write it. Fix the mount or permissions; do not redirect logs into a directory that does not exist.

If no files appear after launch, inspect both layers:

```bash
cat /data/my-project/logs/launcher.log
tail -100 /data/my-project/logs/download.log
pgrep -af ncbi-bioproject
ps aux | grep '[n]cbi-bioproject'
```

`launcher.log` captures shell, command lookup, and redirection failures. `download.log` exists only after Python validates and creates the output tree. Shell redirection happens before Python starts, so a missing redirected directory can prevent the application from running at all.

## Download Failures

Curl exit status 35 indicates an SSL/TLS transport failure (for example `Recv failure: Connection reset by peer`).

Curl exit status 60 indicates TLS certificate-chain verification failed (for example `SSL certificate problem: unable to get local issuer certificate`). This is usually a trust-path issue between your host and the endpoint (enterprise proxy/VPN TLS inspection, stale trust store, or intermittent resolver/network path problems).

The downloader uses `--retry-all-errors` and retry passes, but unstable links may still benefit from lowering concurrency:

```bash
ncbi-bioproject download manifest.tsv --outdir /data/my-project --jobs 1
```

For exit 60, verify the endpoint and trust path explicitly:

```bash
curl -Iv https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR12345678/SRR12345678
```

If your environment requires a custom CA chain (for example an enterprise interception CA), use curl with that bundle:

```bash
curl --cacert /path/to/ca-bundle.pem -I https://sra-pub-run-odp.s3.amazonaws.com/...
```

Avoid disabling certificate verification (for example `-k` or `--insecure`) for production downloads.

`.part` files are resumable and should normally be left in place. Restart the same command safely. An oversized part is discarded automatically. An exact-size part is promoted only after verification. A size or MD5 mismatch quarantines the object as `.bad.<timestamp>`; preserve it while checking storage and transport errors, then rerun.

Check capacity before or during a run:

```bash
df -h /data/my-project
du -sh /data/my-project/{sra,tmp,fastq} 2>/dev/null
```

FASTQ mode needs much more headroom than SRA-only mode.

After all retry passes, `logs/failed_accessions.txt` lists only accessions still incomplete. Rerunning the same command skips verified files and retries those runs. Compare expected and completed counts with:

```bash
expected=$(($(wc -l < /data/my-project/manifest.tsv) - 1))
complete=$(find /data/my-project/sra -type f ! -name '*.part' ! -name '*.bad.*' | wc -l)
printf 'expected=%s complete=%s\n' "$expected" "$complete"
```

The count is a progress aid, not integrity proof; the application performs the authoritative size and MD5 checks when restarted.

## Metadata Retrieval

Set `NCBI_EMAIL` to a real contact address. `NCBI_API_KEY` is optional and is never written to snapshot provenance. HTTP 429 and transient 5xx/TLS failures are retried with bounded backoff; persistent required-service failures exit with status 3.

A snapshot with optional sources missing has `status: partial`, records warnings, and exits with status 4. Missing publication rows may simply mean no NCBI link exists; Europe PMC accession searching is optional and must be requested explicitly.

BioSample attributes are heterogeneous. Inspect `metadata/derived/sample_attributes.tsv` when a field is absent from the stable wide table. Runs whose BioSample cannot be resolved remain visible in `runs.tsv` and should be investigated through validation output.

To rebuild normalized products without network access:

```bash
ncbi-bioproject metadata-normalize --metadata-dir /data/PRJNA/metadata --manifest /data/PRJNA/manifest.tsv
```

Use `--refresh` to perform a transactional snapshot replacement: new metadata is built in staging and only swapped in after success, then the previous state is archived. Validate checksums and manifest consistency with `ncbi-bioproject validate /data/PRJNA`.

Use `ncbi-bioproject status /data/PRJNA` to check whether the latest passing archive attestation still applies to the current manifest, provenance, and observed payload sentinel. `status` is read-only and may conservatively report `STALE` after timestamp-only changes.

Use `ncbi-bioproject verify /data/PRJNA` to reread authoritative SRA payloads cryptographically. Legacy pre-v0.3 archives remain `LEGACY` until a complete verification succeeds. If any required SRA fails during a legacy bootstrap attempt, no managed provenance is published.

`--delete-sra-after-fastq` is rejected in v0.3.0 because SRA remains the authoritative archived payload.
