"""The ``inspire_unit_metadata`` post-stage: link unit siblings in ``codes.parquet``.

MEDS-Extract >= 0.9 writes a code's ``unit`` as one String (stripped, blanks dropped, and more than
one unit on a code fails the build; MEDS_extract#336), so this stage no longer collapses anything.
Units stay as recorded: nothing is normalised or renamed.

It links *unit siblings* (``add_unit_sibling_parents``): codes for the same item that differ only in
a last ``//<unit>`` component, e.g. ``X//item//mg`` and ``X//item//mcg``, get the code without that
component, ``X//item``, appended to their ``parent_codes``, and that stem gets a row of its own
(``add_unit_sibling_rows``) unless it already has one: the siblings' description if they all share
it, the vocabulary parents they all share, and a null unit. INSPIRE's lab and vital codes end in
their unit (``LAB//{item_name}//{Unit}``, ``VITAL//...``, ``WARD_VITAL//...``), so an item recorded
in two units gets such a stem. Without a String ``unit`` column, or without siblings, the file is
left as it is, so a second run changes nothing.

It runs as the first MEDS-Extract post-stage, before suMEDS, which the spec declares::

    etl:
      post_stages:
        - inspire_unit_metadata
        - sumeds_enrich: ...
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from MEDS_transforms.stages import Stage

logger = logging.getLogger(__name__)

STAGE_NAME = "inspire_unit_metadata"
UNIT_COLUMN = "unit"
CODE_COLUMN = "code"
PARENTS_COLUMN = "parent_codes"
DESCRIPTION_COLUMN = "description"
CODES_FILE = "codes.parquet"
#: The separator between code components; a unit sibling ends in ``SEPARATOR + unit``.
SEPARATOR = "//"


def unit_sibling_stems(table: pa.Table) -> pl.Series:
    """Per row, the code without its ``//<unit>`` suffix if the code has a unit sibling, else null.

    A code is unit-suffixed when its (String) ``unit`` is set and the code ends in ``//`` + unit;
    its stem is the code without that suffix. A stem shared by two or more distinct unit-suffixed
    codes makes them unit siblings.

    Examples:
        >>> t = pa.table({
        ...     "code": ["X//a//mg", "X//a//mcg", "X//b//mg", "X//c//mg", "X//c"],
        ...     "unit": ["mg", "mcg", "mg", "kg", None],
        ... })
        >>> unit_sibling_stems(t).to_list()
        ['X//a', 'X//a', None, None, None]
    """
    frame = pl.from_arrow(
        pa.table(
            {
                CODE_COLUMN: table.column(CODE_COLUMN).cast(pa.large_string()),
                UNIT_COLUMN: table.column(UNIT_COLUMN).cast(pa.large_string()),
            }
        )
    )
    code, unit = pl.col(CODE_COLUMN), pl.col(UNIT_COLUMN)
    suffixed = unit.is_not_null() & (unit != "") & code.str.ends_with(SEPARATOR + unit)
    stem = pl.when(suffixed).then(
        code.str.head(code.str.len_chars() - unit.str.len_chars() - len(SEPARATOR))
    )
    return (
        frame.lazy()
        .with_columns(_stem=stem)
        .select(
            pl.when(pl.col(CODE_COLUMN).n_unique().over("_stem") >= 2)
            .then(pl.col("_stem"))
            .alias("stem")
        )
        .collect()
        .get_column("stem")
    )


def add_unit_sibling_parents(table: pa.Table) -> pa.Table:
    """``table`` with each unit sibling's stem appended to its ``parent_codes``.

    Existing parents are kept and the stem is not added twice; ``parent_codes`` keeps its List
    type, or is added as ``list<string>`` if missing. A unit-suffixed code without a sibling gets
    no parent. ``add_unit_sibling_rows`` adds the stems' rows. Without a String ``unit`` column, or
    without siblings, ``table`` is returned as it is.

    Examples:
        >>> t = pa.table({
        ...     "code": ["X//a//mg", "X//a//mcg", "X//b//mg", "X//c//d"],
        ...     "unit": ["mg", "mcg", "mg", "mg"],
        ...     "parent_codes": [["ATC//A01"], None, None, None],
        ... })
        >>> add_unit_sibling_parents(t).column("parent_codes").to_pylist()
        [['ATC//A01', 'X//a'], ['X//a'], None, None]
        >>> twice = add_unit_sibling_parents(add_unit_sibling_parents(t))
        >>> twice.equals(add_unit_sibling_parents(t))
        True
    """
    if UNIT_COLUMN not in table.column_names:
        return table
    unit_type = table.schema.field(UNIT_COLUMN).type
    if not (pa.types.is_string(unit_type) or pa.types.is_large_string(unit_type)):
        return table
    stems = unit_sibling_stems(table)
    if stems.null_count() == len(stems):
        return table

    has_parents = PARENTS_COLUMN in table.column_names
    target = (
        table.schema.field(PARENTS_COLUMN).type
        if has_parents
        else pa.list_(pa.string())
    )
    parents = (
        table.column(PARENTS_COLUMN).cast(pa.large_list(pa.large_string()))
        if has_parents
        else pa.nulls(table.num_rows, pa.large_list(pa.large_string()))
    )
    frame = pl.from_arrow(pa.table({"parents": parents})).with_columns(stem=stems)
    parent, stem = pl.col("parents"), pl.col("stem")
    missing = stem.is_not_null() & ~parent.list.contains(stem).fill_null(False)
    frame = frame.with_columns(
        missing=missing,
        parents=pl.when(missing)
        .then(pl.concat_list(parent.fill_null(pl.lit([], pl.List(pl.String))), stem))
        .otherwise(parent),
    )
    if not frame.get_column("missing").any():
        return table
    column = frame.get_column("parents").to_arrow(compat_level=pl.CompatLevel.oldest())
    column = column.cast(target)
    if has_parents:
        index = table.column_names.index(PARENTS_COLUMN)
        return table.set_column(index, PARENTS_COLUMN, column)
    return table.append_column(pa.field(PARENTS_COLUMN, target), column)


def add_unit_sibling_rows(table: pa.Table) -> pa.Table:
    """``table`` with a row for each unit-sibling stem that has none yet.

    The new row's ``code`` is the stem; its ``description`` is the siblings' description if they
    all have the same non-null one, else null; its ``parent_codes`` are the parents every sibling
    has (in the first sibling's order, the stem itself left out), null if none; ``unit`` and every
    other column are null. An existing row for the stem is left as it is, so a second run adds
    nothing. If ``table`` is sorted by ``code``, the result is too; otherwise the rows are appended.

    Examples:
        >>> t = pa.table({
        ...     "code": ["X//a//mcg", "X//a//mg", "Y//b//g", "Y//b//kg"],
        ...     "description": ["A", "A", "B in g", "B in kg"],
        ...     "unit": ["mcg", "mg", "g", "kg"],
        ...     "parent_codes": [["ATC//A01", "X//a"], ["ATC//A01", "X//a"], None, None],
        ... })
        >>> out = add_unit_sibling_rows(t)
        >>> out.slice(0, 3).to_pylist()[0]
        {'code': 'X//a', 'description': 'A', 'unit': None, 'parent_codes': ['ATC//A01']}
        >>> out.column("code").to_pylist()
        ['X//a', 'X//a//mcg', 'X//a//mg', 'Y//b', 'Y//b//g', 'Y//b//kg']
        >>> out.to_pylist()[3]["description"] is None
        True
        >>> add_unit_sibling_rows(out) is out
        True
    """
    if UNIT_COLUMN not in table.column_names:
        return table
    unit_type = table.schema.field(UNIT_COLUMN).type
    if not (pa.types.is_string(unit_type) or pa.types.is_large_string(unit_type)):
        return table
    stems = unit_sibling_stems(table).to_list()
    if not any(stems):
        return table
    codes = table.column(CODE_COLUMN).to_pylist()
    existing = set(codes)
    has = set(table.column_names)
    descriptions = (
        table.column(DESCRIPTION_COLUMN).to_pylist()
        if DESCRIPTION_COLUMN in has
        else [None] * len(codes)
    )
    parents = (
        table.column(PARENTS_COLUMN).to_pylist()
        if PARENTS_COLUMN in has
        else [None] * len(codes)
    )
    groups: dict[str, list[int]] = {}
    for i, stem in enumerate(stems):
        if stem is not None and stem not in existing:
            groups.setdefault(stem, []).append(i)
    if not groups:
        return table

    rows = []
    for stem, members in groups.items():
        row = {CODE_COLUMN: stem}
        if DESCRIPTION_COLUMN in has:
            shared = {descriptions[i] for i in members}
            row[DESCRIPTION_COLUMN] = shared.pop() if len(shared) == 1 else None
        if PARENTS_COLUMN in has:
            lists = [[p for p in parents[i] or () if p != stem] for i in members]
            common = [
                p
                for p in dict.fromkeys(lists[0])
                if all(p in other for other in lists[1:])
            ]
            row[PARENTS_COLUMN] = common or None
        rows.append(row)
    new = pa.Table.from_pylist(rows, schema=table.schema)
    out = pa.concat_tables([table, new])
    if codes == sorted(codes, key=lambda c: (c is None, c)):
        out = out.sort_by(CODE_COLUMN)
    return out


def link_unit_siblings(table: pa.Table) -> pa.Table:
    """``add_unit_sibling_parents``, then ``add_unit_sibling_rows``; logs counts."""
    out = add_unit_sibling_parents(table)
    rows_before = out.num_rows
    out = add_unit_sibling_rows(out)
    if UNIT_COLUMN in out.column_names:
        stems = unit_sibling_stems(out)
        logger.info(
            f"{stems.drop_nulls().n_unique()} unit-sibling group(s); "
            f"{len(stems) - stems.null_count()} code row(s) have their stem as a parent; "
            f"{out.num_rows - rows_before} stem row(s) added"
        )
    return out


def link_unit_siblings_file(src: Path, dst: Path | None = None) -> None:
    """Run ``link_unit_siblings`` on the ``codes.parquet`` at ``src``, into ``dst`` or in place.

    The file is replaced atomically, and in place it is only rewritten if something changes.
    """
    src = Path(src)
    dst = src if dst is None else Path(dst)
    table = pq.read_table(src)
    out = link_unit_siblings(table)
    if out is table and src == dst:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp")
    pq.write_table(out, tmp)
    os.replace(tmp, dst)
    logger.info(f"Wrote {dst} with its unit siblings linked")


@Stage.register(stage_name=STAGE_NAME, is_metadata=True, default_config={})
def main(cfg) -> None:
    """Link unit siblings in the finished cohort's ``metadata/codes.parquet``."""
    if cfg.worker != 0:
        return
    link_unit_siblings_file(
        Path(cfg.stage_cfg.metadata_input_dir) / CODES_FILE,
        Path(cfg.stage_cfg.reducer_output_dir) / CODES_FILE,
    )
