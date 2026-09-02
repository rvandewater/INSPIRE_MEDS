"""Build the one-row-per-subject side table the MESSY config joins against.

`MEDS_BIRTH` and `MEDS_DEATH` are properties of a *subject*, but INSPIRE records the facts
they derive from on `operations`, which is one row per operation:

- `age` is the age on the operation date, quantised to 5-year bins. A subject with several
  operations during their first admission can therefore straddle a bin boundary and carry two
  different ages, which yields two birth dates exactly five years apart.
- `inhosp_death_time` is recorded only on the rows of the admission during which the patient
  died, while `allcause_death_time` is invariant across all of a subject's rows. A row-wise
  expression sees different values on different rows and emits two death events.

MESSY cannot reduce a table over its own rows -- self-joins are rejected, and dftly is strictly
row-wise with no aggregates -- so the reduction happens here instead, and the event config joins
the result back per subject.

The output is written into a staging directory alongside symlinks to the raw release, so the
credentialed raw tree the downloader verified is never modified.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

#: Name of the derived table, matching the `subjects:` join target in `messy.yaml`.
SIDE_TABLE_NAME = "subjects"

#: The raw table every derived column comes from.
SOURCE_TABLE = "operations"


def build_subject_table(operations: pl.LazyFrame) -> pl.LazyFrame:
    """Reduce `operations` to one row per subject, carrying the birth and death inputs.

    Args:
        operations: The raw operations table.

    Returns:
        A frame of `subject_id`, `birth_age` and `death_min`, one row per subject.

    `birth_age` is the *smallest* age recorded at the subject's first admission. Age rises
    monotonically with time, so the minimum is the age at the earliest operation of that
    admission -- the observation closest to the admission itself, and the only choice that does
    not depend on row order. `admission_time == 0` identifies the first admission because
    INSPIRE anchors each subject's offsets at their own first admission.

    `death_min` is the earliest death time from either source, taken over all of the subject's
    rows, and is null only when the subject has neither record.

    Examples:
        >>> ops = pl.LazyFrame({
        ...     "subject_id": [1, 1, 2, 3],
        ...     "admission_time": [0, 0, 0, 5],
        ...     "age": [75, 80, 60, 40],
        ...     "inhosp_death_time": [None, None, 100.0, None],
        ...     "allcause_death_time": [1440.0, 1440.0, None, None],
        ... })
        >>> build_subject_table(ops).collect().sort("subject_id")
        shape: (3, 3)
        ┌────────────┬───────────┬───────────┐
        │ subject_id ┆ birth_age ┆ death_min │
        │ ---        ┆ ---       ┆ ---       │
        │ i64        ┆ i64       ┆ f64       │
        ╞════════════╪═══════════╪═══════════╡
        │ 1          ┆ 75        ┆ 1440.0    │
        │ 2          ┆ 60        ┆ 100.0     │
        │ 3          ┆ null      ┆ null      │
        └────────────┴───────────┴───────────┘

        Subject 1 straddles a 5-year bin (75 and 80) and takes the earlier age; subject 2 has
        only an in-hospital death and keeps it; subject 3's first admission is not in the table
        and has no death, so both are null.
    """
    first_admission_age = (
        pl.when(pl.col("admission_time") == 0).then(pl.col("age")).otherwise(None).min()
    )
    return operations.group_by("subject_id").agg(
        birth_age=first_admission_age,
        death_min=pl.min_horizontal(
            pl.col("inhosp_death_time").min(), pl.col("allcause_death_time").min()
        ),
    )


def stage_input(raw_input_dir: Path, staging_dir: Path) -> Path:
    """Mirror `raw_input_dir` into `staging_dir` by symlink and add the derived side table.

    The raw release is left untouched: every entry is symlinked, so the staging directory costs
    nothing on disk and the checksum-verified files are never rewritten.

    Args:
        raw_input_dir: The downloaded INSPIRE release.
        staging_dir: Where to build the augmented input tree.

    Returns:
        The staging directory, ready to pass as `input_dir`.

    Raises:
        FileNotFoundError: If the operations table is not present in `raw_input_dir`.
    """
    sources = sorted(raw_input_dir.glob(f"{SOURCE_TABLE}.*"))
    if not sources:
        raise FileNotFoundError(
            f"No {SOURCE_TABLE} table under {raw_input_dir}. Expected the INSPIRE release; "
            "run the download step first, or point --raw-input-dir at an existing copy."
        )

    staging_dir.mkdir(parents=True, exist_ok=True)
    for entry in raw_input_dir.iterdir():
        link = staging_dir / entry.name
        if not link.is_symlink() and not link.exists():
            link.symlink_to(entry.resolve())

    out = staging_dir / f"{SIDE_TABLE_NAME}.parquet"
    table = build_subject_table(
        pl.scan_csv(sources[0], infer_schema_length=None)
    ).collect()
    table.write_parquet(out)
    logger.info(f"Wrote {table.height:,} subject rows to {out}")
    return staging_dir
