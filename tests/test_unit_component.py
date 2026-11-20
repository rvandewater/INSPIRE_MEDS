"""A lab or vital code's unit component is the `parameters.csv` Unit stripped, and `UNK` when it
is missing or blank.

MEDS-Extract compares units stripped and drops blank ones when it writes `codes.parquet`, so a
code that kept the whitespace would split one unit over several codes (` mg`, `mg`, `mg `), and a
blank one would give a code ending in whitespace (or in `//`) with no unit. The expressions are
evaluated straight from the shipped config with dftly, as MEDS-Extract evaluates them.
"""

from pathlib import Path

import polars as pl
import pytest
import yaml
from dftly import Parser

CONFIG_FP = Path(__file__).parents[1] / "src" / "INSPIRE_MEDS" / "messy.yaml"


@pytest.mark.parametrize(
    "table,event,prefix",
    [
        ("labs", "lab", "LAB"),
        ("vitals", "vital", "VITAL"),
        ("ward_vitals", "ward_vital", "WARD_VITAL"),
    ],
)
def test_unit_component_is_stripped_and_blank_is_unk(table, event, prefix):
    block = yaml.safe_load(CONFIG_FP.read_text())[table]
    units = ["mg", " mg", "mg ", "\tmg\n", "mL / h", "  ", "", None]
    raw = pl.DataFrame(
        {"Unit": units, "item_name": ["x"] * len(units)},
        schema_overrides={"Unit": pl.String},
    )
    helper = {"_value_unit": block["_table"]["cols"]["_value_unit"]}
    with_cols = raw.with_columns(**Parser.to_polars(helper))
    codes = with_cols.select(**Parser.to_polars({"code": block[event]["code"]}))[
        "code"
    ].to_list()
    expected = ["mg", "mg", "mg", "mg", "mL / h", "UNK", "UNK", "UNK"]
    assert codes == [f"{prefix}//x//{u}" for u in expected]
    # `unit` stays the raw column; MEDS-Extract strips it when it writes `codes.parquet`.
    assert block[event]["_metadata"]["_self"]["unit"] == "$Unit"
