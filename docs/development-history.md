# Development History

The Git history deliberately retains the incident sequence as working snapshots.

1. `prototype-v1.0` is the initial project-specific resumable downloader. Its default path contained `/Volumes/Bionfo-1`, while the real volume was `/Volumes/Bioinfo-1`.
2. Shell output redirection is opened before Python starts. Redirecting to a path on the misspelled or unmounted destination could therefore fail before the application created logging or emitted its own diagnosis.
3. `prototype-v1.1` corrected the typo and checked that a named macOS volume existed and was writable before resolving and creating project directories.
4. A later transfer encountered a transient curl TLS failure, exit status 35. Curl's existing retry policy did not include every error class.
5. In v1.1, the first failed future entered exception handling that cancelled every pending future and returned from the batch. Already running work could finish, which explained why only two later downloads completed, but queued accessions never started.
6. `prototype-v1.2` added `curl --retry-all-errors`, stopped cancelling the executor, collected failures independently, and made later passes contain only failed runs. Persistent failures are written to `logs/failed_accessions.txt`.

Separate commits and tags preserve what changed, why observed behavior looked partial, and which fix addressed each failure. The reusable package follows in later commits; the exact final prototype remains under `legacy/` for comparison.

1. Version 0.2.0 adds comprehensive BioProject metadata snapshots. The existing SRA parser remains canonical for manual and retrieved XML, while a reusable Entrez client preserves raw records and produces normalized project, sample, run, publication, relationship, and linked-resource products with checksum provenance.
2. Version 0.3.0 transitions the project from a downloader-centric tool to a BioProject archiver with durable archive identity, append-only admission provenance, archive-wide verification attestations, lifecycle status, and compatibility-preserving product renaming.
