"""The packaged code-metadata tables and the config blocks that consume them stay in step."""

import re

import polars as pl
import pyarrow.parquet as pq
import yaml
from meds import CodeMetadataSchema

from INSPIRE_MEDS import MESSY_CFG
from INSPIRE_MEDS.code_descriptions import (
    ICD10CM_PREFIX,
    ICD10PCS_PREFIX,
    apply_static_descriptions,
    expand_icd10pcs_rows,
    read_icd10cm_categories,
    read_icd10pcs_rows,
    read_static_code_descriptions,
    write_vocabulary_tables,
)
from INSPIRE_MEDS.pre_MEDS import stage_input

# ICD-10-PCS uses digits and the letters other than I and O.
PCS_CHAR = "[0-9A-HJ-NP-Z]"
# OMOP vocabulary_id of every parent code the config may emit.
PARENT_VOCABULARIES = {"ICD10CM", "ICD10PCS", "ATC"}


def _cfg() -> dict:
    return yaml.safe_load(MESSY_CFG.read_text())


def _events():
    for table, body in _cfg().items():
        if table in ("sources", "etl", "_defaults"):
            continue
        for name, event in body.items():
            if name != "_table":
                yield table, name, event


def _is_literal_code(code: str) -> bool:
    return "$" not in code


def test_icd10cm_table_is_complete_and_sourced():
    df = read_icd10cm_categories()
    assert df.height == 1918  # every 3-character category of ICD-10-CM FY2026
    assert not df["code"].is_duplicated().any()
    assert df["code"].str.contains(r"^[A-Z][0-9A-Z]{2}$").all()  # FY2026 adds QA0
    assert df["description"].str.len_chars().min() > 0
    assert df["source"].str.len_chars().min() > 0


def test_icd10pcs_rows_are_complete_and_sourced():
    rows = read_icd10pcs_rows()
    assert rows["table"].str.contains(f"^{PCS_CHAR}{{3}}$").all()
    assert rows["body_parts"].str.contains(f"^{PCS_CHAR}+$").all()
    assert rows["approaches"].str.contains(f"^{PCS_CHAR}+$").all()
    assert rows["source"].str.len_chars().min() > 0
    codes = expand_icd10pcs_rows(rows)
    assert codes.height == 34238  # every valid 5-character prefix of ICD-10-PCS FY2026
    assert not codes["code"].is_duplicated().any()
    assert codes["source"].str.len_chars().min() > 0


def test_static_table_covers_every_literal_code():
    static = set(read_static_code_descriptions()["code"])
    literal = {
        str(e["code"]) for _, _, e in _events() if _is_literal_code(str(e["code"]))
    }
    assert literal, "no literal-coded events found; the detection is stale"
    assert not sorted(literal - static), "literal codes without a description"
    assert not sorted(static - literal), "described codes the config no longer emits"


def test_component_coded_events_declare_a_description():
    """Every event whose code has components describes itself through a ``_metadata`` block."""
    undescribed = []
    for table, name, event in _events():
        if _is_literal_code(str(event["code"])):
            continue
        blocks = event.get("_metadata", {}).values()
        if not any(isinstance(b, dict) and "description" in b for b in blocks):
            undescribed.append(f"{table}/{name}")
    assert not undescribed, undescribed


def test_parent_codes_use_omop_vocabulary_ids():
    seen = set()
    for _table, _name, event in _events():
        for block in event.get("_metadata", {}).values():
            if "parent_codes" in block:
                vocabs = set(re.findall(r'f"(\w+)/\{', block["parent_codes"]))
                assert vocabs and vocabs <= PARENT_VOCABULARIES, block["parent_codes"]
                seen |= vocabs
    assert seen == PARENT_VOCABULARIES


def test_vocabulary_tables_match_the_config():
    cfg = _cfg()
    join = cfg["diagnosis"]["_table"]["join"][ICD10CM_PREFIX]
    assert join["right_on"] == "code" and join["cols"] == ["description"]
    assert {"code", "description"} <= set(read_icd10cm_categories().columns)
    block = cfg["operations"]["icd10_pcs"]["_metadata"][ICD10PCS_PREFIX]
    assert block == {"icd10_pcs": "$code", "parent_codes": 'f"ICD10PCS/{$code}"'}


def test_stage_input_writes_the_vocabulary_tables(tmp_path):
    raw, staged = tmp_path / "raw", tmp_path / "staged"
    raw.mkdir()
    pl.DataFrame(
        {
            "subject_id": [1],
            "admission_time": [0],
            "age": [50],
            "inhosp_death_time": [None],
            "allcause_death_time": [None],
        },
        schema_overrides={
            "inhosp_death_time": pl.Float64,
            "allcause_death_time": pl.Float64,
        },
    ).write_csv(raw / "operations.csv")
    stage_input(raw, staged)
    assert (staged / "operations.csv").is_symlink()
    assert pl.read_parquet(staged / f"{ICD10CM_PREFIX}.parquet").height == 1918
    pcs = pl.read_parquet(staged / f"{ICD10PCS_PREFIX}.parquet")
    assert pcs.height == 34238 and pcs.schema["code"] == pl.String
    assert [fp.name for fp in write_vocabulary_tables(tmp_path)] == [
        f"{ICD10CM_PREFIX}.parquet",
        f"{ICD10PCS_PREFIX}.parquet",
    ]


def test_apply_static_descriptions_fills_only_nulls(tmp_path):
    codes = pl.DataFrame(
        {
            "code": ["OR_IN", "MEDS_BIRTH", "DIAGNOSIS//ICD//10//I10"],
            "description": [
                None,
                "already extracted",
                "Essential (primary) hypertension",
            ],
            "parent_codes": [None, None, ["ICD10CM/I10"]],
            "code_template": [
                None,
                None,
                "f\"DIAGNOSIS//ICD//10//{$icd10_cm ?? 'UNK'}\"",
            ],
        }
    )
    fp = tmp_path / "codes.parquet"
    codes.write_parquet(fp)
    assert apply_static_descriptions(fp) == 1
    out = pl.read_parquet(fp)
    assert out.columns == codes.columns and out.height == codes.height
    by_code = dict(zip(out["code"], out["description"], strict=True))
    assert by_code["OR_IN"].startswith("Patient entered the operating room")
    assert by_code["MEDS_BIRTH"] == "already extracted"
    assert by_code["DIAGNOSIS//ICD//10//I10"] == "Essential (primary) hypertension"
    assert apply_static_descriptions(fp) == 0  # idempotent
    # The file must stay valid MEDS code metadata as read from disk (string, not large_string).
    CodeMetadataSchema.validate(pq.read_table(fp))
