"""`MEDS_extract-INSPIRE`: the complete INSPIRE build, from download to enriched code metadata.

    MEDS_extract-INSPIRE ROOT [options] [meds-extract-run overrides ...] [-- suMEDS-enrich options ...]

Five steps, each of which is just the underlying tool:

1. `meds-extract-download` fetches the release from PhysioNet.
2. :mod:`INSPIRE_MEDS.pre_MEDS` reduces `operations` to one row per subject and stages it
   alongside symlinks to the raw tree.
3. `meds-extract-run` runs the canonical pipeline over that staged input, with the download
   step disabled because step 1 already did it.
4. :func:`INSPIRE_MEDS.code_descriptions.apply_static_descriptions` fills the descriptions of the
   literal codes (`OR_IN`, `WEIGHT`, `MEDS_BIRTH`, ...) into the finished `metadata/codes.parquet`;
   see :mod:`INSPIRE_MEDS.code_descriptions` for why no `_metadata` block can reach them.
5. suMEDS enriches `metadata/codes.parquet` in place with the rules this package registers under
   `INSPIRE`, exactly as `suMEDS-enrich CODES -o CODES --rules INSPIRE` would, with every argument
   after a `--`. It only fills what is missing, so the descriptions of steps 3 and 4 are kept.
   `--no-enrich` skips it.

Step 2 is the main reason this wrapper exists. `MEDS_BIRTH` and `MEDS_DEATH` need a per-subject
reduction that MESSY cannot express, and MEDS-Extract's `etl:` block deliberately does not let a
config insert its own stage into the pipeline. That is also why `suMEDS-extract spec=INSPIRE`,
which runs only `meds-extract-run` before suMEDS, is not the complete build. Everything else is unchanged: the
spec is still the shipped MESSY file, resolved by `pkg://` reference, and the stage DAG is still
the canonical one.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import sysconfig
from pathlib import Path

import sumeds
from sumeds.cli import build_enrich_parser, enrich_main

from . import PIPELINE_NAME
from .code_descriptions import apply_static_descriptions
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


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``MEDS_extract-INSPIRE``."""
    argv = sys.argv[1:] if argv is None else list(argv)
    # As with `suMEDS-extract`, the arguments after a `--` go to suMEDS-enrich.
    split = argv.index("--") if "--" in argv else len(argv)
    argv, enrich_args = argv[:split], argv[split + 1 :]

    p = argparse.ArgumentParser(
        prog="MEDS_extract-INSPIRE",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
        "--no-enrich",
        dest="enrich",
        action="store_false",
        help="Skip the suMEDS enrichment of metadata/codes.parquet (step 5).",
    )
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
    codes_fp = out.resolve() / "metadata" / "codes.parquet"
    enrich_argv = [str(codes_fp), "-o", str(codes_fp), "--rules", PIPELINE_NAME]
    enrich_argv += enrich_args
    if args.enrich:  # Fail on bad suMEDS options or rules now, not after the pipeline.
        sumeds.load_rules(build_enrich_parser().parse_args(enrich_argv).rules)

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

    if codes_fp.is_file():
        n_filled = apply_static_descriptions(codes_fp)
        logger.info(
            "Filled the descriptions of %d literal codes in %s", n_filled, codes_fp
        )
    else:  # pragma: no cover - the pipeline always writes it
        logger.warning(
            "%s not found; literal-code descriptions were not applied.", codes_fp
        )
    if args.enrich and (returncode := enrich_main(enrich_argv)):
        return returncode
    logger.info("Done. MEDS cohort at %s", out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
