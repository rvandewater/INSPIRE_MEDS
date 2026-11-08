"""Every code family of the config is described, by a `_metadata` block or by suMEDS."""

import re

import yaml

from INSPIRE_MEDS import MESSY_CFG

# Events whose codes suMEDS describes (sumeds_rules.yaml), so the config gives them no description.
SUMEDS_DESCRIBED = {"diagnosis/diagnosis"}
# The literal codes of the config, each described by a constant `_self` block.
LITERAL_CODES = {
    "OPERATION_ID",
    "VITALDB_CASE_ID",
    "OPERATION_DATE",
    "MEDS_BIRTH",
    "WEIGHT",
    "HEIGHT",
    "OR_IN",
    "OR_OUT",
    "OPERATION_START",
    "OPERATION_END",
    "HOSPITAL_ADMISSION",
    "HOSPITAL_DISCHARGE",
    "ANESTHESIA_START",
    "ANESTHESIA_END",
    "CPB_ON",
    "CPB_OFF",
    "ICU_ADMISSION",
    "ICU_DISCHARGE",
    "MEDS_DEATH",
}
# A constant dftly string: a double-quoted string without column references.
CONSTANT = re.compile(r'^"[^"$]+"$')


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


def test_every_literal_code_has_one_constant_description():
    """Each literal-coded event describes its code with a constant `_self` block, once per code."""
    descriptions: dict[str, list[str]] = {}
    for table, name, event in _events():
        code = str(event["code"])
        if not _is_literal_code(code):
            continue
        blocks = event.get("_metadata", {})
        assert set(blocks) == {"_self"}, (
            f"{table}/{name}: literal codes take only a _self block"
        )
        description = blocks["_self"]["description"]
        assert CONSTANT.match(description), (
            f"{table}/{name}: {description!r} is not a constant"
        )
        descriptions.setdefault(code, []).append(description)
    assert set(descriptions) == LITERAL_CODES
    assert all(len(d) == 1 for d in descriptions.values()), descriptions


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
            assert "icd10" not in str(body["_table"].get("joins", "")), table
