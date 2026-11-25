"""The ``inspire_subject_table`` pre-stage: the one-row-per-subject side table the MESSY config joins.

`MEDS_BIRTH` and `MEDS_DEATH` are properties of a *subject*, but INSPIRE records the facts
they derive from on `operations`, which is one row per operation:

- `age` is the age on the operation date, rounded to the nearest five years. A subject with
  several operations during their first admission can therefore straddle a bin boundary and carry
  two different ages, which yields two birth dates five years apart.
- `inhosp_death_time` is recorded only on the rows of the admission during which the patient
  died, while `allcause_death_time` is invariant across all of a subject's rows. A row-wise
  expression sees different values on different rows and emits two death events.

MESSY cannot reduce a table over its own rows -- self-joins are rejected, and dftly is strictly
row-wise with no aggregates -- so the reduction happens here instead, and the event config joins
the result back per subject.

It runs as a MEDS-Extract pre-stage (MEDS-Extract 0.8.1 or later), which the spec declares::

    etl:
      pre_stages:
        - inspire_subject_table

``meds-extract-run`` runs it before ``convert_to_parquet``. It reads ``operations`` from the run's
``input_dir`` and writes ``subjects.parquet`` into its own output directory, which
``convert_to_parquet`` searches before ``input_dir``. The raw release is never modified.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import polars as pl
from MEDS_transforms.stages import Stage

logger = logging.getLogger(__name__)

STAGE_NAME = "inspire_subject_table"

#: Name of the derived table, matching the `subjects:` join target in `messy.yaml`.
SIDE_TABLE_NAME = "subjects"

#: The raw table every derived column comes from.
SOURCE_TABLE = "operations"

#: Minutes in a Julian year (365.25 days), the year the spec uses to turn ages into days.
MINUTES_PER_YEAR = 365.25 * 24 * 60


def build_subject_table(operations: pl.LazyFrame) -> pl.LazyFrame:
    """Reduce `operations` to one row per subject, carrying the birth and death inputs.

    Args:
        operations: The raw operations table.

    Returns:
        A frame of `subject_id`, `birth_age` and `death_min`, one row per subject.

    `birth_age` is the patient's estimated age, in years, at the start of their first admission
    (offset 0, the anchor of every INSPIRE offset): the smallest age recorded in that admission,
    minus the time from the anchor to the date of the earliest operation with that age. Age rises
    with time, so that is normally the first operation; taking the smallest age keeps the choice
    of 1.2.0 where a release's ages are not monotonic, and does not depend on row order. INSPIRE
    rounds ages to the nearest five years (INSPIRE team, personal communication, 2026-11-22), so
    the recorded age is already the centre of its bin and needs no half-bin or half-year offset;
    the spec places `MEDS_BIRTH` at the anchor minus `birth_age` years, i.e. at that operation's
    date minus the recorded age. `admission_time == 0` identifies the first admission because
    INSPIRE anchors each subject's offsets at their own first admission.

    `death_min` is the in-hospital death time when the subject has one, else the all-cause death
    time. `inhosp_death_time` is the actual time of death from the EMR, while
    `allcause_death_time` is 00:00 of the date the death was registered with the Ministry of the
    Interior and Safety, a median of about 11 days later (INSPIRE team, 2026-11-22), so the
    in-hospital time is preferred even where the registration date is earlier (a death registered
    on the day it happened). `death_min` is null only when the subject has neither record.

    Examples:
        >>> ops = pl.LazyFrame({
        ...     "subject_id": [1, 1, 2, 3, 4],
        ...     "admission_time": [0, 0, 0, 5, 0],
        ...     "opdate": [0, 1440, 2 * 1440, 1440, 0],
        ...     "age": [75, 80, 60, 40, 50],
        ...     "inhosp_death_time": [None, None, 3000.0, None, 4500.0],
        ...     "allcause_death_time": [1440.0, 1440.0, None, None, 4320.0],
        ... })
        >>> table = build_subject_table(ops).collect().sort("subject_id")
        >>> table.select("subject_id", "death_min")
        shape: (4, 2)
        ┌────────────┬───────────┐
        │ subject_id ┆ death_min │
        │ ---        ┆ ---       │
        │ i64        ┆ f64       │
        ╞════════════╪═══════════╡
        │ 1          ┆ 1440.0    │
        │ 2          ┆ 3000.0    │
        │ 3          ┆ null      │
        │ 4          ┆ 4500.0    │
        └────────────┴───────────┘
        >>> [None if a is None else round(a, 4) for a in table["birth_age"]]
        [75.0, 59.9945, None, 50.0]

        Subject 1 straddles a 5-year bin (75 and 80) and takes the smaller age, of its first
        operation;
        subject 2 was operated on two days after admission, so its age at the anchor is two days
        less than 60; subject 3's first admission is not in the table and has no death, so both
        are null. Subject 4's death was registered at 00:00 of the day it died in hospital: the
        in-hospital time wins.
    """
    first_admission = pl.col("admission_time") == 0
    age_at_anchor = pl.col("age") - pl.col("opdate") / MINUTES_PER_YEAR
    # The first-admission rows first, by age and operation date; null if there is none.
    birth_age = (
        pl.when(first_admission)
        .then(age_at_anchor)
        .sort_by([~first_admission, pl.col("age"), pl.col("opdate")], nulls_last=True)
        .first()
    )
    return operations.group_by("subject_id").agg(
        birth_age=birth_age.cast(pl.Float64),
        death_min=pl.coalesce(
            pl.col("inhosp_death_time").min(), pl.col("allcause_death_time").min()
        ).cast(pl.Float64),
    )


def write_subject_table(raw_input_dir: Path, output_dir: Path) -> Path:
    """Write the subject side table of the release in `raw_input_dir` to `output_dir`.

    Args:
        raw_input_dir: The INSPIRE release, as downloaded.
        output_dir: Where to write `subjects.parquet`; created if needed.

    Returns:
        The written file. It is replaced atomically, so a rerun gives the same file.

    Raises:
        FileNotFoundError: If the operations table is not present in `raw_input_dir`.
    """
    sources = sorted(Path(raw_input_dir).glob(f"{SOURCE_TABLE}.*"))
    if not sources:
        raise FileNotFoundError(
            f"No {SOURCE_TABLE} table under {raw_input_dir}. Expected the INSPIRE release: "
            "download it, or point input_dir at an existing copy."
        )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{SIDE_TABLE_NAME}.parquet"
    table = build_subject_table(
        pl.scan_csv(sources[0], infer_schema_length=None)
    ).collect()
    tmp = out.with_name(f".{out.name}.tmp")
    table.write_parquet(tmp)
    os.replace(tmp, out)
    logger.info(f"Wrote {table.height:,} subject rows to {out}")
    return out


@Stage.register(stage_name=STAGE_NAME, is_metadata=False, default_config={})
def main(cfg) -> None:
    """Write the `subjects` side table of the release in ``input_dir`` for the spec to join."""
    if cfg.worker != 0:
        return
    write_subject_table(Path(cfg.input_dir), Path(cfg.stage_cfg.output_dir))
