"""`meds-extract-run spec=INSPIRE` builds what the `MEDS_extract-INSPIRE` runner of 1.0.0 built.

Up to 291c948, `MEDS_extract-INSPIRE` staged the raw release with the `subjects` side table, ran
`meds-extract-run` on it, and then filled the descriptions of the literal codes from a packaged
CSV into the finished `codes.parquet`. The spec now does both itself, with the
`inspire_subject_table` pre-stage and constant `_self` blocks. `regression/runner_291c948_fixture.json`
holds what the runner built from the synthetic release of `conftest.py` (with `--no-enrich`): every
code, its description and its number of events. The one difference is intended: literal codes now
also carry their `code_template`, as every other described code does. The descriptions follow the spec:
they are updated here when it rewords them (as for the INSPIRE team's 2026-11-22 answers), while the
codes and event counts stay those of the runner.
"""

import json
from pathlib import Path

import polars as pl

GOLDEN = Path(__file__).parent / "regression" / "runner_291c948_fixture.json"


def test_same_codes_descriptions_and_event_counts(plain_output: Path):
    golden = json.loads(GOLDEN.read_text())
    events = pl.concat(
        [pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")]
    )
    counts = dict(events.group_by("code").len().iter_rows())
    codes = pl.read_parquet(plain_output / "metadata" / "codes.parquet")
    built = {
        code: {"description": description, "events": counts[code]}
        for code, description in codes.select("code", "description").iter_rows()
        if code in counts
    }
    # The only codes without events are unit-free stems (suMEDS's `sumeds_link_unit_siblings`, which
    # the runner predates): each is another code without its unit component.
    stems = set(codes["code"]) - set(counts)
    assert all(any(c.startswith(f"{stem}//") for c in counts) for stem in stems), stems
    # Since then the fixture gained a lab item with a blank unit (`plt`, conftest.py), which gives
    # one more event under `LAB//plt//UNK`; the padded units of `wbc` and `hr` give the same codes.
    expected = {
        **golden["codes"],
        "LAB//plt//UNK": {"description": "Platelet count", "events": 1},
    }
    assert events.height == golden["events"] + 1
    assert built == expected


def test_literal_codes_carry_their_code_template(plain_output: Path):
    table = pl.read_parquet(plain_output / "metadata" / "codes.parquet")
    templates = dict(table.select("code", "code_template").iter_rows())
    assert templates["OR_IN"] == "OR_IN"
    assert templates["MEDS_BIRTH"] == "MEDS_BIRTH"
