"""The `inspire_subject_table` pre-stage, and the deprecated `MEDS_extract-INSPIRE` alias."""

import doctest
import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
from conftest import TABLES, etl_argv, run_command

from INSPIRE_MEDS import __main__ as alias
from INSPIRE_MEDS import pre_MEDS


def test_docstring_examples():
    assert doctest.testmod(pre_MEDS).failed == 0
    assert doctest.testmod(alias).failed == 0


def test_default_run_deletes_the_stage_dirs(plain_output: Path):
    """The spec sets `cleanup_intermediates: true`: a finished run keeps only the cohort and the run
    records, and every stage, the pre-stage included, stays marked done."""
    assert {p.name for p in plain_output.iterdir()} == {
        ".logs",
        ".meds_extract_run",
        "data",
        "metadata",
    }
    assert (plain_output / ".logs" / f"{pre_MEDS.STAGE_NAME}.done").exists()


def test_pre_stage_stages_the_side_table_and_leaves_the_release_alone(
    tmp_path: Path, raw_dir: Path
):
    """The side table lands in the stage's own dir, which `convert_to_parquet` reads first.

    The stage dirs are kept for this run (`cleanup_intermediates=false`), so the table can be read.
    """
    kept_output = tmp_path / "meds"
    run_command(
        etl_argv(
            kept_output,
            raw_dir,
            "skip_stages=[sumeds_enrich]",
            "overrides=['cleanup_intermediates=false']",
        )
    )
    assert not list(raw_dir.glob("subjects*")), "the raw release was modified"
    staged = pl.read_parquet(kept_output / pre_MEDS.STAGE_NAME / "subjects.parquet")
    expected = pre_MEDS.build_subject_table(
        pl.LazyFrame(TABLES["operations"])
    ).collect()
    assert staged.sort("subject_id").equals(expected.sort("subject_id"))
    assert (kept_output / ".logs" / f"{pre_MEDS.STAGE_NAME}.done").exists()

    events = pl.concat(
        [pl.read_parquet(fp) for fp in (kept_output / "data").rglob("*.parquet")]
    )
    per_subject = (
        events.filter(pl.col("code").is_in(["MEDS_BIRTH", "MEDS_DEATH"]))
        .group_by("code")
        .len()
    )
    # One birth per subject (subject 3's first admission is in the table), one death per subject
    # with a death record (subjects 1 and 2).
    assert dict(per_subject.iter_rows()) == {"MEDS_BIRTH": 3, "MEDS_DEATH": 2}


def test_birth_is_the_operation_date_minus_the_age_and_death_prefers_the_hospital_time(
    plain_output: Path,
):
    """Ages are rounded to the nearest five years, so the age is the centre of its bin: birth is the
    operation date minus the age, with no half-year offset. The in-hospital death time wins over
    00:00 of the registration date, even where that date is earlier."""
    events = pl.concat(
        [pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")]
    )
    times = dict(
        events.filter(pl.col("code").is_in(["MEDS_BIRTH", "MEDS_DEATH"]))
        .select(
            pl.concat_str("code", pl.col("subject_id").cast(pl.String), separator="/"),
            "time",
        )
        .iter_rows()
    )
    origin = datetime(2016, 1, 1)
    # Subject 1: ages 60 and 65 in one admission, the smaller on the operation at offset 0.
    assert times["MEDS_BIRTH/1"] == origin - timedelta(days=60 * 365.25)
    assert times["MEDS_BIRTH/2"] == origin - timedelta(days=40 * 365.25)
    assert times["MEDS_DEATH/1"] == origin + timedelta(minutes=20000)
    assert times["MEDS_DEATH/2"] == origin + timedelta(minutes=4500)


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
