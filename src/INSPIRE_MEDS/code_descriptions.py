"""Packaged code metadata for ``metadata/codes.parquet``: descriptions and vocabulary parents.

INSPIRE describes its lab and vital items in ``parameters.csv`` and its departments in
``department.csv``; the config joins both at ETL time. Every other code family is described from
the PhysioNet documentation, and the diagnosis and operation codes get parents in the vocabulary
they are coded in, so OMOP/Athena tooling can resolve them (see the README section "Code
metadata"). Three routes, because MEDS-Extract attaches metadata differently by code shape:

* **Component-coded events** (demographics, ASA, departments, operations, diagnoses,
  medications): ``_metadata`` blocks in ``messy.yaml``. Two of them read a vocabulary table
  shipped here, which :func:`write_vocabulary_tables` writes next to the raw tables when the
  input is staged:

  - ``icd10cm_categories`` (the ICD-10-CM 3-character categories, CMS FY2026), joined onto
    ``diagnosis`` for the category title and the ``ICD10CM/`` parent;
  - ``icd10pcs_codes`` (every valid 5-character ICD-10-PCS prefix, CMS FY2026), the
    ``_metadata`` source of the ``ICD10PCS/`` parent of the operation codes. It is shipped
    compactly as the rows of the CMS tables (``icd10pcs_rows.csv``) and expanded on staging.

  Both are complete public vocabularies, independent of the data, so a code that is not in them
  gets no parent rather than a dangling one.
* **Literal codes** (``OR_IN``, ``WEIGHT``, ``MEDS_BIRTH``, ...): ``static_code_descriptions.csv``.
  A literal code has no components for ``_metadata`` to join on, and the pre-existing
  ``codes.parquet`` route MEDS-Extract documents for this case is not reached by the standard
  pipeline, so ``MEDS_extract-INSPIRE`` fills these in after the pipeline with
  :func:`apply_static_descriptions`.

Every shipped table records the source of each row in a ``source`` column. The two vocabulary
tables are regenerated from the CMS release files with :func:`build_icd10cm_categories` and
:func:`build_icd10pcs_rows`.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

METADATA_DIR = Path(__file__).parent / "metadata"

#: Input prefixes the config reads the vocabulary tables under; must match ``messy.yaml``.
ICD10CM_PREFIX = "icd10cm_categories"
ICD10PCS_PREFIX = "icd10pcs_codes"

ICD10CM_CSV = METADATA_DIR / "icd10cm_categories.csv"
ICD10PCS_ROWS_CSV = METADATA_DIR / "icd10pcs_rows.csv"
STATIC_DESCRIPTIONS_CSV = METADATA_DIR / "static_code_descriptions.csv"

ICD10CM_SOURCE = "cms:icd10cm_order_2026"
ICD10PCS_SOURCE = "cms:icd10pcs_tables_2026"


def read_icd10cm_categories(path: Path = ICD10CM_CSV) -> pl.DataFrame:
    """Read the ICD-10-CM category table (``code``, ``description``, ``source``), all String.

    Examples:
        >>> df = read_icd10cm_categories()
        >>> df.columns
        ['code', 'description', 'source']
        >>> df.filter(pl.col("code") == "I10")["description"].item()
        'Essential (primary) hypertension'
        >>> df["code"].str.len_chars().unique().to_list(), df["code"].is_duplicated().any()
        ([3], False)
    """
    return pl.read_csv(path, infer_schema=False)


def read_icd10pcs_rows(path: Path = ICD10PCS_ROWS_CSV) -> pl.DataFrame:
    """Read the ICD-10-PCS table rows (``table``, ``body_parts``, ``approaches``, ``source``).

    Each row is one row of a CMS ICD-10-PCS table cut after the fifth axis: ``table`` is the
    first three characters (section, body system, root operation), ``body_parts`` the valid
    fourth characters and ``approaches`` the valid fifth characters of that row.

    Examples:
        >>> rows = read_icd10pcs_rows()
        >>> rows.columns
        ['table', 'body_parts', 'approaches', 'source']
        >>> rows["table"].str.len_chars().unique().to_list()
        [3]
    """
    return pl.read_csv(path, infer_schema=False)


def expand_icd10pcs_rows(rows: pl.DataFrame) -> pl.DataFrame:
    """Expand table rows into the distinct valid 5-character ICD-10-PCS prefixes (``code``).

    Examples:
        >>> rows = pl.DataFrame({"table": ["0DT", "0DT"], "body_parts": ["J", "JK"], "approaches": ["04", "0"],
        ...                      "source": ["x", "x"]})
        >>> expand_icd10pcs_rows(rows)["code"].to_list()
        ['0DTJ0', '0DTJ4', '0DTK0']
        >>> codes = expand_icd10pcs_rows(read_icd10pcs_rows())["code"]
        >>> codes.len(), "0DTJ4" in codes, "0DTJX" in codes
        (34238, True, False)
    """
    codes = {
        table + body_part + approach: source
        for table, body_parts, approaches, source in rows.select(
            "table", "body_parts", "approaches", "source"
        ).iter_rows()
        for body_part in body_parts
        for approach in approaches
    }
    return pl.DataFrame(sorted(codes.items()), schema=["code", "source"], orient="row")


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


def write_vocabulary_tables(output_dir: Path) -> list[Path]:
    """Write the two vocabulary tables where the extraction pipeline reads their prefixes.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fps = write_vocabulary_tables(Path(d))
        ...     [(fp.name, pl.read_parquet(fp).height) for fp in fps]
        [('icd10cm_categories.parquet', 1918), ('icd10pcs_codes.parquet', 34238)]
    """
    output_dir = Path(output_dir)
    cm_fp = output_dir / f"{ICD10CM_PREFIX}.parquet"
    pcs_fp = output_dir / f"{ICD10PCS_PREFIX}.parquet"
    read_icd10cm_categories().write_parquet(cm_fp)
    expand_icd10pcs_rows(read_icd10pcs_rows()).write_parquet(pcs_fp)
    return [cm_fp, pcs_fp]


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


def build_icd10cm_categories(order_fp: Path) -> pl.DataFrame:
    """Build ``icd10cm_categories.csv`` from a CMS ICD-10-CM order file (``icd10cm_order_YYYY.txt``).

    The order file is fixed width: the code in columns 7-13 and the long description from column
    78. A category is a 3-character code. The CMS ICD-10-CM files are in the public domain.

    Examples:
        >>> import tempfile
        >>> lines = [
        ...     f"{'00001':5} {'I10':7} 1 {'Essential (primary) hypertension':60} Essential (primary) hypertension",
        ...     f"{'00002':5} {'I110':7} 1 {'Hypertensive heart disease w heart failure':60} Hypertensive heart disease with heart failure",
        ... ]
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = Path(d) / "order.txt"
        ...     _ = fp.write_text("\\n".join(lines) + "\\n")
        ...     build_icd10cm_categories(fp).rows()
        [('I10', 'Essential (primary) hypertension', 'cms:icd10cm_order_2026')]
    """
    rows = []
    for line in Path(order_fp).read_text(encoding="latin-1").splitlines():
        code = line[6:13].strip()
        if len(code) == 3:
            rows.append((code, line[77:].strip(), ICD10CM_SOURCE))
    return (
        pl.DataFrame(rows, schema=["code", "description", "source"], orient="row")
        .unique("code")
        .sort("code")
    )


def build_icd10pcs_rows(tables_xml_fp: Path) -> pl.DataFrame:
    """Build ``icd10pcs_rows.csv`` from the CMS ICD-10-PCS tables (``icd10pcs_tables_YYYY.xml``).

    Every ``pcsRow`` contributes its table (axes 1-3), its fourth-axis and its fifth-axis
    characters; rows that differ only after the fifth axis collapse into one. The CMS
    ICD-10-PCS files are in the public domain.

    Examples:
        >>> import tempfile
        >>> xml = '''<ICD10PCS.tabular><pcsTable>
        ...   <axis pos="1"><label code="0">Medical and Surgical</label></axis>
        ...   <axis pos="2"><label code="D">Gastrointestinal System</label></axis>
        ...   <axis pos="3"><label code="T">Resection</label></axis>
        ...   <pcsRow><axis pos="4"><label code="J">Appendix</label></axis>
        ...           <axis pos="5"><label code="0">Open</label><label code="4">Percutaneous Endoscopic</label></axis>
        ...   </pcsRow></pcsTable></ICD10PCS.tabular>'''
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = Path(d) / "tables.xml"
        ...     _ = fp.write_text(xml)
        ...     build_icd10pcs_rows(fp).rows()
        [('0DT', 'J', '04', 'cms:icd10pcs_tables_2026')]
    """
    rows = set()
    for table in ET.parse(tables_xml_fp).getroot().iter("pcsTable"):
        axes = {int(a.get("pos")): a for a in table.findall("axis")}
        prefix = "".join(axes[pos].find("label").get("code") for pos in (1, 2, 3))
        for row in table.findall("pcsRow"):
            row_axes = {int(a.get("pos")): a for a in row.findall("axis")}
            body_parts, approaches = (
                "".join(label.get("code") for label in row_axes[pos].findall("label"))
                for pos in (4, 5)
            )
            rows.add((prefix, body_parts, approaches, ICD10PCS_SOURCE))
    return pl.DataFrame(
        sorted(rows),
        schema=["table", "body_parts", "approaches", "source"],
        orient="row",
    )
