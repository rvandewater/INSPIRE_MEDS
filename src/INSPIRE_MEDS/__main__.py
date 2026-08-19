"""`MEDS_extract-INSPIRE`: download, derive the subject side table, then run MEDS-Extract.

Three steps, each of which is just the underlying tool:

1. `meds-extract-download` fetches the release from PhysioNet.
2. :mod:`INSPIRE_MEDS.pre_MEDS` reduces `operations` to one row per subject and stages it
   alongside symlinks to the raw tree.
3. `meds-extract-run` runs the canonical pipeline over that staged input, with the download
   step disabled because step 1 already did it.

Step 2 is the only reason this wrapper exists. `MEDS_BIRTH` and `MEDS_DEATH` need a per-subject
reduction that MESSY cannot express, and MEDS-Extract's `etl:` block deliberately does not let a
config insert its own stage into the pipeline. Everything else is unchanged: the spec is still
the shipped MESSY file, resolved by `pkg://` reference, and the stage DAG is still the canonical
one.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import sysconfig
from pathlib import Path

from .pre_MEDS import stage_input

logger = logging.getLogger(__name__)

#: The shipped MESSY config, addressed the way every stage re-resolves it.
SPEC_REF = "pkg://INSPIRE_MEDS.messy.yaml"


def _script(name: str) -> str:
    """Resolve a console script in this environment rather than trusting ``PATH``."""
    exe = Path(sysconfig.get_path("scripts")) / name
    if not exe.exists():  # pragma: no cover - environment error
        raise FileNotFoundError(f"{name} is not installed alongside {sys.executable}.")
    return str(exe)


def _run(argv: list[str]) -> None:
    """Run a child command, streaming its output, and fail loudly."""
    logger.info("Running: %s", " ".join(argv))
    result = subprocess.run(argv, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{argv[0]} failed with return code {result.returncode}.")


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``MEDS_extract-INSPIRE``."""
    p = argparse.ArgumentParser(prog="MEDS_extract-INSPIRE", description=__doc__)
    p.add_argument(
        "root_output_dir",
        type=Path,
        help="Root for raw_input/, staged/ and MEDS_cohort/.",
    )
    p.add_argument(
        "--raw-input-dir", type=Path, default=None, help="Default: <root>/raw_input"
    )
    p.add_argument(
        "--output-dir", type=Path, default=None, help="Default: <root>/MEDS_cohort"
    )
    p.add_argument("--do-download", default="true", choices=("true", "false"))
    p.add_argument("--download-concurrency", type=int, default=4)
    p.add_argument(
        "overrides",
        nargs="*",
        help="Extra key=value overrides passed to meds-extract-run.",
    )
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    root = args.root_output_dir
    raw = args.raw_input_dir or root / "raw_input"
    out = args.output_dir or root / "MEDS_cohort"
    staged = root / "staged_input"

    if args.do_download == "true":
        _run(
            [
                _script("meds-extract-download"),
                f"spec={SPEC_REF}",
                f"output_dir={raw.resolve()!s}",
                "key=dataset",
                f"concurrency={args.download_concurrency}",
            ]
        )
    else:
        logger.info("do_download=false: using the existing raw input at %s", raw)

    stage_input(raw, staged)

    _run(
        [
            _script("meds-extract-run"),
            f"spec={SPEC_REF}",
            f"output_dir={out.resolve()!s}",
            "do_download=false",
            f"input_dir={staged.resolve()!s}",
            *args.overrides,
        ]
    )
    logger.info("Done. MEDS cohort at %s", out)


if __name__ == "__main__":  # pragma: no cover
    main()
