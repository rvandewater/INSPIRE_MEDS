"""`MEDS_extract-INSPIRE`: a deprecated alias of `meds-extract-run spec=INSPIRE`, kept for one release.

INSPIRE-MEDS 1.0.0 shipped this command as a runner around `meds-extract-run`. The spec now declares
everything that runner added (the `inspire_subject_table` pre-stage, the literal-code descriptions
and the `sumeds_enrich` post-stage), and `meds-extract-run` downloads the release itself, so the
complete build is one command:

    meds-extract-run spec=INSPIRE output_dir=OUT download_dest_dir=RAW

This alias only translates the old arguments to that command and runs it:

    MEDS_extract-INSPIRE ROOT [--raw-input-dir RAW] [--output-dir OUT] [--do-download true|false]
        [--download-concurrency N] [--no-enrich] [meds-extract-run overrides ...]

`RAW` defaults to `ROOT/raw_input` and `OUT` to `ROOT/MEDS_cohort`, as before; `--no-enrich` becomes
`skip_stages=[sumeds_enrich]`. suMEDS options after a `--` are no longer accepted: set the
`sumeds_enrich` stage's options with `"overrides=['++stage_cfg.strict=true']"` instead. The alias
will be removed in the next release.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import sysconfig
from pathlib import Path

from . import PIPELINE_NAME

DEPRECATION = (
    "MEDS_extract-INSPIRE is deprecated and will be removed in the next release; run\n"
    "    {command}\n"
    "instead (see the README)."
)


def translate(argv: list[str]) -> list[str]:
    """The `meds-extract-run` arguments for the old `MEDS_extract-INSPIRE` arguments.

    Examples:
        >>> translate(["/data/inspire"])  # doctest: +NORMALIZE_WHITESPACE
        ['spec=INSPIRE', 'output_dir=/data/inspire/MEDS_cohort',
         'download_dest_dir=/data/inspire/raw_input', 'download_concurrency=4']
        >>> translate(["/r", "--raw-input-dir=/raw", "--do-download=false", "--no-enrich", "seed=2"])
        ['spec=INSPIRE', 'output_dir=/r/MEDS_cohort', 'do_download=false', 'input_dir=/raw', 'skip_stages=[sumeds_enrich]', 'seed=2']
    """  # noqa: E501
    if "--" in argv:
        raise SystemExit(
            "MEDS_extract-INSPIRE: suMEDS options after `--` are no longer supported. Set the "
            "sumeds_enrich stage's options with \"overrides=['++stage_cfg.strict=true']\" (see the "
            "README), or enrich afterwards with `suMEDS-enrich OUT ...`."
        )
    p = argparse.ArgumentParser(prog="MEDS_extract-INSPIRE", description=__doc__)
    p.add_argument("root_output_dir", type=Path)
    p.add_argument("--raw-input-dir", type=Path, default=None)
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument("--do-download", default="true", choices=("true", "false"))
    p.add_argument("--download-concurrency", type=int, default=4)
    p.add_argument("--no-enrich", dest="enrich", action="store_false")
    p.add_argument("overrides", nargs="*")
    args = p.parse_args(argv)

    raw = args.raw_input_dir or args.root_output_dir / "raw_input"
    out = args.output_dir or args.root_output_dir / "MEDS_cohort"
    run = [f"spec={PIPELINE_NAME}", f"output_dir={out}"]
    if args.do_download == "true":
        run += [
            f"download_dest_dir={raw}",
            f"download_concurrency={args.download_concurrency}",
        ]
    else:
        run += ["do_download=false", f"input_dir={raw}"]
    if not args.enrich:
        run.append("skip_stages=[sumeds_enrich]")
    return run + args.overrides


def main(argv: list[str] | None = None) -> int:
    """Entry point for the deprecated `MEDS_extract-INSPIRE`."""
    run = translate(sys.argv[1:] if argv is None else list(argv))
    print(
        DEPRECATION.format(command=" ".join(["meds-extract-run", *run])),
        file=sys.stderr,
    )
    meds_extract_run = Path(sysconfig.get_path("scripts")) / "meds-extract-run"
    return subprocess.run([str(meds_extract_run), *run], check=False).returncode


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
