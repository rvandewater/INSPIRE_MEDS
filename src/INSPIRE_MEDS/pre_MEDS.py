"""The ``inspire_subject_table`` pre-stage: the one-row-per-subject side table the MESSY config joins.

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
