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
pgrep -af sra-bioproject
ps aux | grep '[s]ra-bioproject'
```

`launcher.log` captures shell, command lookup, and redirection failures. `download.log` exists only after Python validates and creates the output tree. Shell redirection happens before Python starts, so a missing redirected directory can prevent the application from running at all.

## Download Failures

Curl exit status 35 indicates an SSL/TLS handshake failure. The downloader uses `--retry-all-errors` and retry passes, but unstable links may benefit from:

```bash
sra-bioproject download manifest.tsv --outdir /data/my-project --jobs 1
```

`.part` files are resumable and should normally be left in place. Restart the same command safely. An oversized part is discarded automatically. A size or MD5 mismatch quarantines the object as `.bad.<timestamp>`; preserve it while checking storage and transport errors, then rerun.

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
