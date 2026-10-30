"""The packaged suMEDS rules parse, and their patterns match the code layouts of ``messy.yaml``.

The example codes are built from the code templates in ``messy.yaml`` with synthetic (public
example) values, so a change to a code layout that the rules no longer match fails here.
"""

import re
from importlib.resources import files
from pathlib import Path

import yaml

from INSPIRE_MEDS import MESSY_CFG

RULES_FP = Path(__file__).parents[1] / "src" / "INSPIRE_MEDS" / "sumeds_rules.yaml"

RULE_KINDS = {"schema", "ontology", "crosswalk", "hierarchy"}
ONTOLOGY_KEYS = {"id", "pattern", "vocabulary", "normalize", "validate"}
HIERARCHY_KEYS = {
    "id",
    "pattern",
    "group",
    "separator",
    "mode",
    "ignore",
    "nearest_only",
}
FROM_ONTOLOGY_KEYS = {"id", "from_ontology", "rules", "nearest_only"}
VALIDATE_MODES = {"pack", "athena", "none"}

# Synthetic column values: public example codes in INSPIRE's truncated layouts, plus null (None).
VALUES = {
    "icd10_cm": ["I10", "E11", "J18", None],
    "icd10_pcs": ["0DTJ4", "5A195", "02HV3", None],
    "atc_code": ["N02BE01", "N02BE", "N02B", "N02", "N", "B05XA03", "N2BE", None],
    "route": ["IV", "PO"],
}
# The ontology rule each example code must resolve through (codes not listed match no rule).
EXPECTED_ONTOLOGY = {
    "DIAGNOSIS//ICD//10//I10": ("inspire-icd10cm-categories", "I10"),
    "DIAGNOSIS//ICD//10//E11": ("inspire-icd10cm-categories", "E11"),
    "DIAGNOSIS//ICD//10//J18": ("inspire-icd10cm-categories", "J18"),
    "ICD10_PCS//0DTJ4": ("inspire-icd10pcs-prefixes", "0DTJ4"),
    "ICD10_PCS//5A195": ("inspire-icd10pcs-prefixes", "5A195"),
    "ICD10_PCS//02HV3": ("inspire-icd10pcs-prefixes", "02HV3"),
    **{
        f"MEDICATION//{atc}//{route}": ("inspire-atc-medications", atc)
        for atc in ["N02BE01", "N02BE", "N02B", "N02", "N", "B05XA03"]
        for route in VALUES["route"]
    },
}
# Hierarchy rule id -> example codes it must match, with the value of its `group`.
EXPECTED_HIERARCHY = {
    "inspire-medication-atc-prefixes": {
        "MEDICATION//N02BE01//IV": "N02BE01",
        "MEDICATION//N02BE//PO": "N02BE",
    },
}
FIELD = re.compile(r"\{\$(\w+)(?: \?\? '([^']*)')?\}")


def load_rules() -> dict:
    return yaml.safe_load(RULES_FP.read_text())


def code_templates() -> list[str]:
    cfg = yaml.safe_load(MESSY_CFG.read_text())
    templates = []
    for table, body in cfg.items():
        if table in ("sources", "etl", "_defaults"):
            continue
        templates += [str(e["code"]) for name, e in body.items() if name != "_table"]
    return templates


def render(template: str, values: dict[str, str | None]) -> str | None:
    """Render a config code expression; None if a column without a default is null."""
    head = template.split(" if ")[0].strip()
    if not head.startswith('f"'):
        return head.strip('"')
    missing = False

    def field(match: re.Match) -> str:
        nonlocal missing
        value = values.get(match.group(1), "X")
        if value is None and match.group(2) is None:
            missing = True
        return match.group(2) if value is None else value

    code = FIELD.sub(field, head[2:-1])
    return None if missing else code


def example_codes() -> set[str]:
    value_sets = [{"route": route} for route in VALUES["route"]]
    for column in ("icd10_cm", "icd10_pcs", "atc_code"):
        value_sets = [{**vs, column: v} for vs in value_sets for v in VALUES[column]]
    return {render(t, vs) for t in code_templates() for vs in value_sets} - {None}


def test_rules_file_is_packaged():
    assert files("INSPIRE_MEDS").joinpath("sumeds_rules.yaml").is_file()


def test_rules_file_parses_and_is_well_formed():
    rules = load_rules()
    assert set(rules) <= RULE_KINDS
    assert rules["schema"] == "sumeds-rules/0.1"
    ids = [
        r["id"]
        for kind in ("ontology", "crosswalk", "hierarchy")
        for r in rules.get(kind) or []
    ]
    assert len(ids) == len(set(ids)), "duplicate rule ids"

    ontology_ids = set()
    for rule in rules["ontology"]:
        assert set(rule) <= ONTOLOGY_KEYS and {"id", "pattern", "vocabulary"} <= set(
            rule
        ), rule["id"]
        assert "code" in re.compile(rule["pattern"]).groupindex, rule["id"]
        assert rule["pattern"].startswith("^") and rule["pattern"].endswith("$"), rule[
            "id"
        ]
        assert rule.get("validate", "pack") in VALIDATE_MODES, rule["id"]
        assert rule["vocabulary"].strip() and "/" not in rule["vocabulary"], rule["id"]
        for step in rule.get("normalize") or []:
            assert set(step) == {"pattern", "replace"}
            re.compile(step["pattern"])
        ontology_ids.add(rule["id"])

    for rule in rules["hierarchy"]:
        if rule.get("from_ontology"):
            assert set(rule) <= FROM_ONTOLOGY_KEYS, rule["id"]
            assert set(rule.get("rules") or []) <= ontology_ids, rule["id"]
            continue
        assert set(rule) <= HIERARCHY_KEYS, rule["id"]
        pattern = re.compile(rule["pattern"])
        assert rule["pattern"].startswith("^") and rule["pattern"].endswith("$"), rule[
            "id"
        ]
        assert rule["group"] in pattern.groupindex, rule["id"]
        assert (rule.get("mode") == "prefix") != ("separator" in rule), rule["id"]


def test_ontology_rules_match_the_config_code_layouts():
    rules = load_rules()["ontology"]
    compiled = [(r["id"], re.compile(r["pattern"])) for r in rules]
    examples = example_codes()
    assert set(EXPECTED_ONTOLOGY) <= examples, set(EXPECTED_ONTOLOGY) - examples

    resolved = {}
    for code in examples:
        hit = next(
            ((rule_id, m) for rule_id, p in compiled if (m := p.search(code))), None
        )
        if hit:
            resolved[code] = (hit[0], hit[1].group("code"))
    # Exactly the expected codes match, which excludes `UNK` codes and the malformed ATC code.
    assert resolved == EXPECTED_ONTOLOGY
    assert {rule_id for rule_id, _ in resolved.values()} == {r["id"] for r in rules}


def test_hierarchy_rules_match_the_config_code_layouts():
    rules = {
        r["id"]: r for r in load_rules()["hierarchy"] if not r.get("from_ontology")
    }
    assert set(rules) == set(EXPECTED_HIERARCHY)
    examples = example_codes()
    for rule_id, expected in EXPECTED_HIERARCHY.items():
        rule = rules[rule_id]
        pattern = re.compile(rule["pattern"])
        matched = {
            code: m.group(rule["group"])
            for code in examples
            if (m := pattern.search(code))
        }
        assert expected.items() <= matched.items(), rule_id
        family = next(iter(expected)).split("//")[0]
        assert all(code.startswith(f"{family}//") for code in matched), rule_id
