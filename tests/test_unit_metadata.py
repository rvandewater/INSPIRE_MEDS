"""`collapse_unit_metadata`: codes.parquet's `unit` becomes one String per code.

Synthetic codes.parquet files only; the full spec runs it in `test_run_with_enrichment.py`.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from INSPIRE_MEDS.unit_metadata import (
    add_unit_sibling_parents,
    MultipleUnitsError,
    collapse_unit,
    collapse_unit_file,
)


def _write(path: Path, table: pa.Table) -> Path:
    pq.write_table(table, path)
    return path


def test_list_units_collapse_to_one_string(tmp_path: Path):
    codes = pa.table(
        {
            "code": ["ONE", "DUPLICATES", "BLANKS", "EMPTY", "NULL", "PADDED"],
            "description": ["a", "b", "c", "d", "e", "f"],
            "unit": [
                ["mg/dL"],
                ["kg", "kg", "kg"],
                ["", "  ", None, "/min"],
                [],
                None,
                [" cm ", "cm"],
            ],
        }
    )
    path = _write(tmp_path / "codes.parquet", codes)
    collapse_unit_file(path)
    out = pq.read_table(path)
    assert out.schema.field("unit").type == pa.large_string()
    assert out.column("unit").to_pylist() == ["mg/dL", "kg", "/min", None, None, "cm"]
    # Every other column is untouched.
    assert out.drop_columns(["unit"]).equals(codes.drop_columns(["unit"]))


def test_units_stay_as_recorded():
    codes = pa.table({"code": ["A", "B"], "unit": [["Geen"], ["None"]]})
    assert collapse_unit(codes).column("unit").to_pylist() == ["Geen", "None"]


def test_several_units_are_an_error(tmp_path: Path):
    codes = pa.table(
        {
            "code": [f"C{i}" for i in range(8)],
            "unit": [["mg", "g"]] * 7 + [["mg"]],
        }
    )
    path = _write(tmp_path / "codes.parquet", codes)
    with pytest.raises(MultipleUnitsError, match="7 code") as error:
        collapse_unit_file(path)
    assert error.value.codes == [f"C{i}" for i in range(7)]
    assert "put the unit into the code" in str(error.value)
    # At most five examples are named.
    assert "'C4'" in str(error.value) and "'C5'" not in str(error.value)
    # The file is left as it was.
    assert pq.read_table(path).equals(codes)


def test_string_unit_passes_through(tmp_path: Path):
    codes = pa.table(
        {"code": ["A", "B"], "unit": pa.array([" mg ", None], pa.string())}
    )
    path = _write(tmp_path / "codes.parquet", codes)
    before = path.read_bytes()
    collapse_unit_file(path)
    assert path.read_bytes() == before
    assert collapse_unit(codes) is codes


def test_no_unit_column_is_a_no_op(tmp_path: Path):
    codes = pa.table({"code": ["A"], "description": ["a"]})
    path = _write(tmp_path / "codes.parquet", codes)
    before = path.read_bytes()
    collapse_unit_file(path)
    assert path.read_bytes() == before


def test_writes_to_another_file(tmp_path: Path):
    src = _write(tmp_path / "in.parquet", pa.table({"code": ["A"], "unit": [["mg"]]}))
    dst = tmp_path / "out" / "codes.parquet"
    collapse_unit_file(src, dst)
    assert pq.read_table(dst).column("unit").to_pylist() == ["mg"]


def test_other_types_are_rejected():
    with pytest.raises(TypeError):
        collapse_unit(pa.table({"code": ["A"], "unit": [1]}))


SIBLINGS = pa.table(
    {
        "code": [
            "LAB//hb//g/dL",  # siblings: same item, unit as the last component
            "LAB//hb//mmol/L",
            "VITAL//hr///min",  # a lone unit-suffixed code: no sibling, no parent
            "LAB//crp//mg",  # last component is not its unit
            "LAB//crp//g",  # ... nor here, so these two are not siblings
            "WEIGHT",
        ],
        "unit": [["g/dL"], ["mmol/L"], ["/min"], ["mg/L"], ["g/L"], ["kg"]],
        "parent_codes": pa.array(
            [["LOINC//718-7"], None, None, None, None, None], pa.list_(pa.string())
        ),
    }
)


def test_unit_siblings_get_their_stem_as_parent(tmp_path: Path):
    path = _write(tmp_path / "codes.parquet", SIBLINGS)
    collapse_unit_file(path)
    out = pq.read_table(path)
    assert out.num_rows == SIBLINGS.num_rows  # no row for the stem
    assert out.column("code").equals(SIBLINGS.column("code"))
    assert out.schema.field("parent_codes").type == pa.list_(pa.string())
    assert out.column("parent_codes").to_pylist() == [
        ["LOINC//718-7", "LAB//hb"],  # existing vocabulary parents stay first
        ["LAB//hb"],
        None,
        None,
        None,
        None,
    ]
    # Running it again adds nothing.
    collapse_unit_file(path)
    assert pq.read_table(path).equals(out)


def test_parent_codes_is_added_when_missing():
    out = add_unit_sibling_parents(
        collapse_unit(SIBLINGS.drop_columns(["parent_codes"]))
    )
    assert out.schema.field("parent_codes").type == pa.list_(pa.string())
    assert out.column("parent_codes").to_pylist()[:3] == [
        ["LAB//hb"],
        ["LAB//hb"],
        None,
    ]


def test_no_siblings_leaves_parent_codes_alone():
    codes = collapse_unit(SIBLINGS.slice(2))
    assert add_unit_sibling_parents(codes) is codes
    no_parents = codes.drop_columns(["parent_codes"])
    assert add_unit_sibling_parents(no_parents) is no_parents


def test_a_list_unit_is_not_used_for_siblings():
    """Siblings are found on the collapsed (String) unit only."""
    assert add_unit_sibling_parents(SIBLINGS) is SIBLINGS
