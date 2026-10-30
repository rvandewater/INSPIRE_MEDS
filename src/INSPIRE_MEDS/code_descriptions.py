"""Packaged code metadata for ``metadata/codes.parquet``: descriptions of literal codes.

INSPIRE describes its lab and vital items in ``parameters.csv`` and its departments in
``department.csv``; the config joins both at ETL time. Every other code family is described from
the PhysioNet documentation (see the README section "Code metadata"). Two routes, because
MEDS-Extract attaches metadata differently by code shape:

* **Component-coded events** (demographics, ASA, departments, operations, medications):
  ``_metadata`` blocks in ``messy.yaml``.
* **Literal codes** (``OR_IN``, ``WEIGHT``, ``MEDS_BIRTH``, ...): ``static_code_descriptions.csv``.
  A literal code has no components for ``_metadata`` to join on, and the pre-existing
  ``codes.parquet`` route MEDS-Extract documents for this case is not reached by the standard
  pipeline, so ``MEDS_extract-INSPIRE`` fills these in after the pipeline with
  :func:`apply_static_descriptions`.

Vocabulary work is not done here: the ICD-10-CM category titles of the diagnosis codes and the
``ICD10CM``, ``ICD10PCS`` and ``ATC`` parent codes come from suMEDS, with the rules in
``sumeds_rules.yaml``. The shipped table records the source of each row in a ``source`` column.
"""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

METADATA_DIR = Path(__file__).parent / "metadata"

STATIC_DESCRIPTIONS_CSV = METADATA_DIR / "static_code_descriptions.csv"


def read_static_code_descriptions(path: Path = STATIC_DESCRIPTIONS_CSV) -> pl.DataFrame:
    """Read the descriptions of literal codes in the MEDS ``codes.parquet`` shape.

    Only ``code`` and ``description`` are emitted; the ``source`` column documents the table
    itself and is not written into the dataset.

    Examples:
        >>> df = read_static_code_descriptions()
        >>> df.columns
        ['code', 'description']
        >>> df.filter(pl.col("code") == "CPB_ON")["description"].item()
        'Start of cardiopulmonary bypass (INSPIRE operations.cpbon_time)'
        >>> df["code"].is_duplicated().any()
        False
    """
    return pl.read_csv(path, infer_schema=False).select("code", "description")


def apply_static_descriptions(
    codes_fp: Path, static: pl.DataFrame | None = None
) -> int:
    """Fill the ``description`` of literal codes in a finished ``codes.parquet``, in place.

    Only codes whose description is null are filled (an extracted description always wins,
    matching MEDS-Extract's own precedence), and only codes present in the file are touched:
    the vocabulary is never extended. The file is replaced atomically and is validated against
    the MEDS code-metadata schema before it is written: polars materializes strings as Arrow
    ``large_string``, which that schema rejects, so the MEDS columns are cast back to the types
    the pipeline itself writes (``string``, ``list<string>``).

    Args:
        codes_fp: The ``metadata/codes.parquet`` of a finished extraction.
        static: Override of the packaged table (``code``, ``description``), for tests.

    Returns:
        The number of codes whose description was filled.

    Examples:
        >>> import tempfile
        >>> static = pl.DataFrame({"code": ["MEDS_BIRTH", "NOT_EMITTED"], "description": ["Birth", "x"]})
        >>> codes = pl.DataFrame(
        ...     {"code": ["MEDS_BIRTH", "DIAGNOSIS//ICD//10//I10", "SEX//F"],
        ...      "description": [None, "Essential (primary) hypertension", None],
        ...      "parent_codes": [None, ["ICD10CM/I10"], None]}
        ... )
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = Path(d) / "codes.parquet"
        ...     codes.write_parquet(fp)
        ...     n = apply_static_descriptions(fp, static)
        ...     out = pl.read_parquet(fp)
        >>> n
        1
        >>> out.sort("code").select("code", "description").to_dicts()  # doctest: +NORMALIZE_WHITESPACE
        [{'code': 'DIAGNOSIS//ICD//10//I10', 'description': 'Essential (primary) hypertension'},
         {'code': 'MEDS_BIRTH', 'description': 'Birth'},
         {'code': 'SEX//F', 'description': None}]
        >>> out.columns == codes.columns and out.height == codes.height
        True
    """
    codes_fp = Path(codes_fp)
    if static is None:
        static = read_static_code_descriptions()
    codes = pl.from_arrow(pq.read_table(codes_fp))
    if "description" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.String).alias("description"))
    filled = (
        codes.join(static.rename({"description": "_static"}), on="code", how="left")
        .with_columns(pl.coalesce("description", "_static").alias("description"))
        .drop("_static")
        .select(codes.columns)
    )
    n_filled = int(
        filled["description"].is_not_null().sum()
        - codes["description"].is_not_null().sum()
    )

    out = filled.to_arrow()
    meds_types = {
        "code": pa.string(),
        "description": pa.string(),
        "parent_codes": pa.list_(pa.string()),
    }
    out = out.cast(
        pa.schema(
            [pa.field(f.name, meds_types.get(f.name, f.type)) for f in out.schema]
        )
    )
    CodeMetadataSchema.validate(out)

    tmp_fp = codes_fp.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, codes_fp)
    return n_filled
