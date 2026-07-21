#!/usr/bin/env python3
"""Deterministic BOUND-agent-skill.zip builder.

Usage:
    python scripts/build_skill_zip.py [--out-dir release/skills]

Builds a byte-for-byte reproducible ZIP from the skills/bound/ directory
so that the same commit always produces the same ZIP.
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_SRC = REPO_ROOT / "skills" / "bound"

# Fixed timestamp for determinism: 2026-01-01 00:00:00 UTC
# Using a fixed date that's clearly in the past of any real release
# means the ZIP is reproducible regardless of when/where it's built.
DETERMINISTIC_TIMESTAMP = (2026, 1, 1, 0, 0, 0)

# Files/directories to exclude from the ZIP
EXCLUDE_PATTERNS = {
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    "Thumbs.db",
}

# Required files that MUST be in the skill
REQUIRED_FILES = {
    "bound/SKILL.md",
    "bound/agents/openai.yaml",
    "bound/references/integration-report.md",
}


def _should_exclude(path: Path) -> bool:
    """Check if a path matches any exclude pattern."""
    for part in path.parts:
        if part in EXCLUDE_PATTERNS:
            return True
    name = path.name
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return True
    return False


def build_skill_zip(*, out_dir: str | Path) -> Path:
    """Build a deterministic ZIP of the skills/bound directory.

    Returns the path to the created ZIP file.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    zip_path = out_path / "BOUND-agent-skill.zip"

    # Collect all files, sorted for determinism
    files_to_zip: list[Path] = sorted(
        p for p in SKILLS_SRC.rglob("*") if p.is_file() and not _should_exclude(p)
    )

    if not files_to_zip:
        print(f"❌ No files found in {SKILLS_SRC}", file=sys.stderr)
        sys.exit(1)

    with zipfile.ZipFile(
        zip_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=False,
    ) as zf:
        for file_path in files_to_zip:
            # Compute archive name relative to skills/ directory
            archive_name = str(file_path.relative_to(SKILLS_SRC.parent))
            info = zipfile.ZipInfo.from_file(file_path, archive_name)
            # Override timestamp for determinism
            info.date_time = DETERMINISTIC_TIMESTAMP
            # Remove Unix permissions / owner info
            info.external_attr = 0o644 << 16  # -rw-r--r--
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, file_path.read_bytes())

    # Validate ZIP structure
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        missing = REQUIRED_FILES - names
        if missing:
            print(
                f"❌ ZIP missing required files: {', '.join(sorted(missing))}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Verify no pycache or DS_Store leaked in
        for name in names:
            for pattern in EXCLUDE_PATTERNS:
                if pattern.startswith("*"):
                    if name.endswith(pattern[1:]):
                        print(
                            f"❌ ZIP contains excluded file: {name}",
                            file=sys.stderr,
                        )
                        sys.exit(1)

        # Check that all entries have the deterministic timestamp
        for name in names:
            info = zf.getinfo(name)
            if info.date_time != DETERMINISTIC_TIMESTAMP:
                print(
                    f"❌ ZIP entry {name} has non-deterministic timestamp "
                    f"{info.date_time}",
                    file=sys.stderr,
                )
                sys.exit(1)

    size = zip_path.stat().st_size
    print(f"✅ Created {zip_path} ({size} bytes, {len(files_to_zip)} files)")
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic BOUND-agent-skill.zip"
    )
    parser.add_argument(
        "--out-dir",
        default="release/skills",
        help="Output directory for the ZIP (default: release/skills)",
    )
    args = parser.parse_args()

    build_skill_zip(out_dir=args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())