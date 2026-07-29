#!/usr/bin/env python3
"""Convert an NCBI SRA XML export to the project's stable TSV format."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sra_bioproject.cli import main


raise SystemExit(main(["manifest", *sys.argv[1:]]))