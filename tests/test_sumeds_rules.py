"""The packaged suMEDS rules load in suMEDS and match the code layouts of ``messy.yaml``.

suMEDS validates the rules file itself (``sumeds.load_rules``) and resolves codes with the rule
objects its enrichment uses, so these tests only state what INSPIRE's codes must resolve to. The
example codes are built from the code templates in ``messy.yaml`` with synthetic (public example)
values, so a change to a code layout that the rules no longer match fails here.
"""

import json
import re
from importlib.resources import files
from pathlib import Path

import polars as pl
import pytest
import yaml
from sumeds import EnrichmentConfig, OntologyRules, enrich_metadata, load_rules
from sumeds.ontology import INDEX_SCHEMA

from INSPIRE_MEDS import MESSY_CFG

RULES_FP = Path(__file__).parents[1] / "src" / "INSPIRE_MEDS" / "sumeds_rules.yaml"

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
# Hierarchy rule id -> example code -> the parent suMEDS adds from the other example codes.
EXPECTED_HIERARCHY = {
    "inspire-medication-atc-prefixes": {
        "MEDICATION//N02BE01//IV": ["MEDICATION//N02BE//IV"],
        "MEDICATION//N02BE//PO": ["MEDICATION//N02B//PO"],
    },
}
FIELD = re.compile(r"\{\$(\w+)(?: \?\? '([^']*)')?\}")


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


@pytest.fixture
def empty_ontology_source(tmp_path: Path) -> str:
    """An ontology source with no vocabularies, so enrichment reads nothing over the network."""
    index = {"schema": INDEX_SCHEMA, "vocabularies": {}}
    (tmp_path / "index.json").write_text(json.dumps(index))
    return str(tmp_path)


def test_rules_file_is_packaged():
    assert files("INSPIRE_MEDS").joinpath("sumeds_rules.yaml").is_file()


def test_rules_load_in_sumeds():
    """suMEDS rejects any malformed rule; each pattern must also span a whole code."""
    rules = load_rules(RULES_FP)
    for rule in (*rules.vocabulary_rules, *rules.hierarchy):
        if rule.pattern is not None:
            assert rule.pattern.pattern.startswith("^"), rule.id
            assert rule.pattern.pattern.endswith("$"), rule.id


def test_ontology_rules_match_the_config_code_layouts():
    rules = load_rules(RULES_FP).ontology
    examples = example_codes()
    assert set(EXPECTED_ONTOLOGY) <= examples, set(EXPECTED_ONTOLOGY) - examples

    resolved = {}
    for code in examples:
        hits = [
            (rule.id, value)
            for rule in rules
            if (value := rule.resolve(code)) is not None
        ]
        assert len(hits) <= 1, f"{code} matches several rules: {hits}"
        if hits:
            resolved[code] = hits[0]
    # Exactly the expected codes match, which excludes `UNK` codes and the malformed ATC code.
    assert resolved == EXPECTED_ONTOLOGY
    assert {rule_id for rule_id, _ in resolved.values()} == {rule.id for rule in rules}


def test_hierarchy_rules_add_the_expected_parents(empty_ontology_source: str):
    """Run suMEDS's own enrichment, offline, with the string hierarchy rules over the examples."""
    hierarchy = tuple(r for r in load_rules(RULES_FP).hierarchy if r.mode != "ontology")
    assert {rule.id for rule in hierarchy} == set(EXPECTED_HIERARCHY)
    examples = sorted(example_codes())
    for rule in hierarchy:
        expected = EXPECTED_HIERARCHY[rule.id]
        enriched = enrich_metadata(
            pl.LazyFrame({"code": examples}),
            EnrichmentConfig(
                rules=OntologyRules(hierarchy=(rule,)),
                ontology_source=empty_ontology_source,
            ),
        ).collect()
        parents = {
            row["code"]: row["parent_codes"]
            for row in enriched.select("code", "parent_codes").to_dicts()
            if row["parent_codes"]
        }
        assert expected.items() <= parents.items(), rule.id
        family = next(iter(expected)).split("//")[0]
        assert all(code.startswith(f"{family}//") for code in parents), rule.id
