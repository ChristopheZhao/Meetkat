#!/usr/bin/env python3
"""Generate `docs/decisions/INDEX.md` from every decision record in
`docs/decisions/`.

Usage:
    python scripts/generate_decisions_index.py
    python scripts/generate_decisions_index.py --check    # CI / pre-commit

`--check` exits non-zero if the on-disk INDEX.md differs from the freshly
generated content. Suitable for a CI step or a pre-commit hook that
gates merges on the index being up to date.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from decision_room.runtime.decision_index import (  # noqa: E402
    parse_decision_record,
    render_index,
)


_SKIP_FILES = frozenset({"INDEX.md", "README.md"})


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--decisions-dir",
        default=str(ROOT / "docs" / "decisions"),
        help="Directory containing decision record .md files (default: docs/decisions)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Where to write the INDEX (default: <decisions-dir>/INDEX.md)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if on-disk INDEX is stale; do not write.",
    )
    args = parser.parse_args()

    decisions_dir = Path(args.decisions_dir)
    if not decisions_dir.is_dir():
        print(f"error: {decisions_dir} is not a directory", file=sys.stderr)
        return 2

    output_path = Path(args.output) if args.output else decisions_dir / "INDEX.md"

    summaries = []
    skipped: list[str] = []
    for path in sorted(decisions_dir.glob("*.md")):
        if path.name in _SKIP_FILES:
            continue
        summary = parse_decision_record(path)
        if summary is None:
            skipped.append(path.name)
            continue
        summaries.append(summary)

    content = render_index(summaries)

    if args.check:
        existing = (
            output_path.read_text(encoding="utf-8")
            if output_path.exists()
            else ""
        )
        if existing != content:
            print(
                f"INDEX is stale; rerun without --check to regenerate "
                f"{output_path.relative_to(ROOT) if output_path.is_relative_to(ROOT) else output_path}",
                file=sys.stderr,
            )
            return 1
        print(f"INDEX up to date ({len(summaries)} records).")
        return 0

    output_path.write_text(content, encoding="utf-8")
    rel = (
        output_path.relative_to(ROOT)
        if output_path.is_relative_to(ROOT)
        else output_path
    )
    print(f"Wrote {rel} ({len(summaries)} records).")
    if skipped:
        print(f"Skipped (no decision-record header): {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
