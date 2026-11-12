"""A tiny, entirely synthetic INSPIRE release, and helpers to run the CLIs over it.

The tables have the column layout of the INSPIRE 1.4.2 files that ``messy.yaml`` and the
``inspire_subject_table`` pre-stage read, with made-up values: three subjects, public example ICD-10-CM, ICD-10-PCS and
WHO ATC codes in INSPIRE's truncated layouts, and a null in each column the config writes as
``UNK``. No real INSPIRE data is used.
"""

import os
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

# Offsets are minutes from each subject's first admission, as in the release.
OPERATIONS = {
    "op_id": [400000001, 400000002, 400000003, 400000004],
    "subject_id": [1, 1, 2, 3],
    "case_id": [1, 2, None, None],
    "opdate": [0, 1440, 0, 0],
    # Subject 1 straddles a 5-year age bin across two operations of one admission.
    "age": [60, 65, 40, 70],
    "sex": ["M", "M", "F", "F"],
    "weight": [70.5, 70.5, 55.0, 60.0],
    "height": [172.0, 172.0, 160.0, 155.0],
    "race": ["Asian", "Asian", "Asian", "Asian"],
    "asa": [2, 2, None, 3],
    "emop": [0, 1, 0, 0],
    "department": ["GS", "GS", "CS", "GS"],
    "antype": ["General", "General", "General", "Regional"],
    "icd10_pcs": ["0DTJ4", "5A195", "02HV3", None],
    "admission_time": [0, 0, 0, 0],
    "orin_time": [600, 2040, 300, 100],
    "orout_time": [780, 2200, 900, 200],
    "opstart_time": [640, 2060, 360, 120],
    "opend_time": [740, 2180, 840, 180],
    "anstart_time": [610, 2045, 310, 105],
    "anend_time": [770, 2190, 880, 190],
    "cpbon_time": [None, None, 400, None],
    "cpboff_time": [None, None, 800, None],
    "icuin_time": [None, 2210, 910, None],
    "icuout_time": [None, 4000, 3000, None],
    "discharge_time": [10080, 10080, 5000, 4000],
    "inhosp_death_time": [None, None, 4500, None],
    "allcause_death_time": [20000, 20000, 4500, None],
}

TABLES = {
    "operations": OPERATIONS,
    # U99 is not in the synthetic ontology pack of test_run_with_enrichment.py.
    "diagnosis": {
        "subject_id": [1, 1, 2, 2, 3],
        "chart_time": [-1000, -500, -100, -50, 10],
        "icd10_cm": ["I10", "E11", "J18", "U99", None],
    },
    "medications": {
        "subject_id": [1, 1, 2, 3],
        "chart_time": [650, 2100, 400, 150],
        "drug_name": ["acetaminophen", "acetaminophen", "sodium chloride", "unknown"],
        "atc_code": ["N02BE01", "N02BE", "B05XA03", None],
        "route": ["IV", "IV", "IV", "PO"],
    },
    "labs": {
        "subject_id": [1, 2, 3],
        "chart_time": [-60, -30, 3200],
        "item_name": ["hb", "wbc", "hb"],
        "value": [13.5, 7.1, 11.0],
    },
    "vitals": {
        "subject_id": [1, 2],
        "chart_time": [620, 320],
        "item_name": ["hr", "hr"],
        "value": [80.0, 95.0],
    },
    "ward_vitals": {
        "subject_id": [1],
        "chart_time": [3000],
        "item_name": ["hr"],
        "value": [72.0],
    },
    "parameters": {
        "Label": ["hb", "wbc", "hr"],
        "Unit": ["g/dL", "10^3/uL", "/min"],
        "Description": ["Hemoglobin", "White blood cell count", "Heart rate"],
    },
    "department": {
        "Abbreviations": ["GS", "CS"],
        "Full name": ["General surgery", "Cardiothoracic surgery"],
    },
}


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    """The synthetic release, one csv per table, as `meds-extract-download` would leave it."""
    root = tmp_path_factory.mktemp("inspire_raw")
    for name, columns in TABLES.items():
        pl.DataFrame(columns).write_csv(root / f"{name}.csv")
    return root


def run_command(
    argv: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run a CLI, failing the test with its full output if it does not succeed."""
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=None if env is None else {**os.environ, **env},
    )
    assert result.returncode == 0, (
        f"{argv[0]} failed with exit code {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def etl_argv(output: Path, raw: Path, *args: str) -> list[str]:
    """``meds-extract-run spec=INSPIRE`` over the release in ``raw``, into ``output``."""
    meds_extract_run = str(Path(sys.executable).parent / "meds-extract-run")
    return [
        meds_extract_run,
        "spec=INSPIRE",
        f"output_dir={output}",
        "do_download=false",
        f"input_dir={raw}",
        *args,
    ]


@pytest.fixture(scope="session")
def plain_output(tmp_path_factory, raw_dir: Path) -> Path:
    """The ETL without the suMEDS enrichment stage (``skip_stages=[sumeds_enrich]``)."""
    output = tmp_path_factory.mktemp("inspire_plain") / "meds"
    run_command(etl_argv(output, raw_dir, "skip_stages=[sumeds_enrich]"))
    return output
