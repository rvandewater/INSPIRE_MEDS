"""The `inspire_subject_table` pre-stage, and the deprecated `MEDS_extract-INSPIRE` alias."""

import doctest
import sys
from pathlib import Path

import polars as pl
from conftest import TABLES, run_command

from INSPIRE_MEDS import __main__ as alias
from INSPIRE_MEDS import pre_MEDS


def test_docstring_examples():
    assert doctest.testmod(pre_MEDS).failed == 0
    assert doctest.testmod(alias).failed == 0


def test_pre_stage_stages_the_side_table_and_leaves_the_release_alone(
    plain_output: Path, raw_dir: Path
):
    """The side table lands in the stage's own dir, which `convert_to_parquet` reads first."""
    assert not list(raw_dir.glob("subjects*")), "the raw release was modified"
    staged = pl.read_parquet(plain_output / pre_MEDS.STAGE_NAME / "subjects.parquet")
    expected = pre_MEDS.build_subject_table(
        pl.LazyFrame(TABLES["operations"])
    ).collect()
    assert staged.sort("subject_id").equals(expected.sort("subject_id"))
    assert (plain_output / ".logs" / f"{pre_MEDS.STAGE_NAME}.done").exists()

    events = pl.concat(
        [pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")]
    )
    per_subject = (
        events.filter(pl.col("code").is_in(["MEDS_BIRTH", "MEDS_DEATH"]))
        .group_by("code")
        .len()
    )
    # One birth per subject (subject 3's first admission is in the table), one death per subject
    # with a death record (subjects 1 and 2).
    assert dict(per_subject.iter_rows()) == {"MEDS_BIRTH": 3, "MEDS_DEATH": 2}


def test_deprecated_alias_runs_meds_extract_run(
    tmp_path: Path, raw_dir: Path, plain_output: Path
):
    alias_exe = str(Path(sys.executable).parent / "MEDS_extract-INSPIRE")
    argv = [
        alias_exe,
        str(tmp_path),
        f"--raw-input-dir={raw_dir}",
        "--do-download=false",
        "--no-enrich",
    ]
    result = run_command(argv)
    assert "MEDS_extract-INSPIRE is deprecated" in result.stderr
    assert "meds-extract-run spec=INSPIRE" in result.stderr
    built = tmp_path / "MEDS_cohort" / "metadata" / "codes.parquet"
    assert (
        built.read_bytes() == (plain_output / "metadata" / "codes.parquet").read_bytes()
    )
