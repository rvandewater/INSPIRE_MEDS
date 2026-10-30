"""The packaged literal-code descriptions and the config's description blocks stay in step."""

import polars as pl
import pyarrow.parquet as pq
import yaml
from meds import CodeMetadataSchema

from INSPIRE_MEDS import MESSY_CFG
from INSPIRE_MEDS.code_descriptions import (
    apply_static_descriptions,
    read_static_code_descriptions,
)

# Events whose codes suMEDS describes (sumeds_rules.yaml), so the config gives them no description.
SUMEDS_DESCRIBED = {"diagnosis/diagnosis"}


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


def test_static_table_covers_every_literal_code():
    static = set(read_static_code_descriptions()["code"])
    literal = {
        str(e["code"]) for _, _, e in _events() if _is_literal_code(str(e["code"]))
    }
    assert literal, "no literal-coded events found; the detection is stale"
    assert not sorted(literal - static), "literal codes without a description"
    assert not sorted(static - literal), "described codes the config no longer emits"


def test_component_coded_events_declare_a_description():
    """Every event whose code has components describes itself through a ``_metadata`` block.

    The exception is the diagnosis codes, which suMEDS describes with the ICD-10-CM category title.
    """
    undescribed = []
    for table, name, event in _events():
        if _is_literal_code(str(event["code"])):
            continue
        blocks = event.get("_metadata", {}).values()
        if not any(isinstance(b, dict) and "description" in b for b in blocks):
            undescribed.append(f"{table}/{name}")
    assert set(undescribed) == SUMEDS_DESCRIBED, undescribed


def test_config_emits_no_parent_codes():
    """Vocabulary parents (ICD10CM, ICD10PCS, ATC) come from suMEDS, not from the config."""
    for table, name, event in _events():
        for block in event.get("_metadata", {}).values():
            assert "parent_codes" not in block, f"{table}/{name}"
    for table, body in _cfg().items():
        if isinstance(body, dict) and isinstance(body.get("_table"), dict):
            assert "icd10" not in str(body["_table"].get("join", "")), table


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
