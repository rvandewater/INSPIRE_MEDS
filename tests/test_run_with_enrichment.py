"""`MEDS_extract-INSPIRE` runs the ETL and then suMEDS enrichment, offline.

The runner builds the subject side table, runs MEDS-Extract, fills the literal-code descriptions,
and then hands `metadata/codes.parquet` to suMEDS with the rules this package registers under
`INSPIRE`. It runs here twice over the synthetic release of ``conftest.py``: once with
`--no-enrich`, the ETL's own output, and once with enrichment, which must only add to it. The
ontology source is a tiny pack repository in suMEDS's `sumeds-ontologies/0.1` layout, built here
with made-up titles for the synthetic ICD codes, so nothing is read over the network. An empty pack
source stands in for a site without internet access, where the enrichment soft-fails.
"""

import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from conftest import run_command
from meds import CodeMetadataSchema, DatasetMetadataSchema

RUNNER = "MEDS_extract-INSPIRE"

# vocabulary -> release -> code -> parent (None at the top). ICD-10-PCS holds every prefix of a
# code, so a five-character INSPIRE code has four ancestors.
PACKS = {
    "ICD10CM": ("fy2016-fy2027", {"I10": None, "E11": None, "J18": None}),
    "ICD10PCS": (
        "fy2016-fy2027",
        {
            "0": None,
            "0D": "0",
            "0DT": "0D",
            "0DTJ": "0DT",
            "0DTJ4": "0DTJ",
            "02": "0",
            "02H": "02",
            "02HV": "02H",
            "02HV3": "02HV",
            "5": None,
            "5A": "5",
            "5A1": "5A",
            "5A19": "5A1",
            "5A195": "5A19",
        },
    ),
}


def title(vocabulary: str, code: str) -> str:
    return f"Synthetic {vocabulary} title {code}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="session")
def ontology_source(tmp_path_factory) -> Path:
    """A local `sumeds-ontologies/0.1` repository holding the synthetic ICD packs."""
    root = tmp_path_factory.mktemp("ontologies")
    columns = ["code", "description", "parent", "first_year", "last_year"]
    vocabularies = {}
    for vocabulary, (release, rows) in PACKS.items():
        directory = root / vocabulary / release
        directory.mkdir(parents=True)
        by_shard: dict[str, list[list[str]]] = {}
        for code, parent in rows.items():
            by_shard.setdefault(code[:1], []).append(
                [code, title(vocabulary, code), parent or "", "", ""]
            )
        shards = {}
        for key, shard_rows in by_shard.items():
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="\n").writerows([columns, *shard_rows])
            data = buffer.getvalue().encode()
            (directory / f"{key}.csv").write_bytes(data)
            shards[key] = {
                "file": f"{key}.csv",
                "rows": len(shard_rows),
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        manifest = {
            "schema": "sumeds-ontology-pack/0.1",
            "vocabulary": vocabulary,
            "release": release,
            "columns": columns,
            "fields": {
                "code": "code",
                "description": "description",
                "parent": "parent",
            },
            "shard_key_length": 1,
            "rows": len(rows),
            "bytes": sum(s["bytes"] for s in shards.values()),
            "shards": shards,
        }
        manifest_bytes = json.dumps(manifest).encode()
        (directory / "manifest.json").write_bytes(manifest_bytes)
        vocabularies[vocabulary] = {
            "manifest": f"{vocabulary}/{release}/manifest.json",
            "sha256": _sha256(manifest_bytes),
            "release": release,
            "rows": manifest["rows"],
            "bytes": manifest["bytes"],
        }
    index = {"schema": "sumeds-ontologies/0.1", "vocabularies": vocabularies}
    (root / "index.json").write_text(json.dumps(index))
    return root


def _build(root: Path, raw_dir: Path, *extra: str) -> Path:
    run_command(
        [RUNNER, str(root), f"--raw-input-dir={raw_dir}", "--do-download=false", *extra]
    )
    return root / "MEDS_cohort"


@pytest.fixture(scope="session")
def plain_output(tmp_path_factory, raw_dir: Path) -> Path:
    return _build(tmp_path_factory.mktemp("inspire_plain"), raw_dir, "--no-enrich")


@pytest.fixture(scope="session")
def enriched_output(tmp_path_factory, raw_dir: Path, ontology_source: Path) -> Path:
    root = tmp_path_factory.mktemp("inspire_enriched")
    return _build(root, raw_dir, "--", "--ontology-source", str(ontology_source))


def _codes(output: Path) -> dict[str, dict]:
    df = pl.read_parquet(output / "metadata" / "codes.parquet")
    return {row["code"]: row for row in df.to_dicts()}


def _record(output: Path) -> dict:
    """The enrichment record in `metadata/dataset.json`, which must stay valid MEDS metadata."""
    metadata = json.loads((output / "metadata" / "dataset.json").read_text())
    DatasetMetadataSchema.validate(metadata)
    return metadata["sumeds_enrichment"]


@pytest.fixture(scope="session")
def plain_codes(plain_output: Path) -> dict[str, dict]:
    return _codes(plain_output)


@pytest.fixture(scope="session")
def enriched_codes(enriched_output: Path) -> dict[str, dict]:
    return _codes(enriched_output)


def test_codes_parquet_is_replaced_by_valid_meds_metadata(enriched_output: Path):
    metadata = enriched_output / "metadata"
    CodeMetadataSchema.validate(pq.read_table(metadata / "codes.parquet"))
    assert not list(metadata.glob(".*enriched*")), (
        "the temporary enrichment output was left behind"
    )


def test_data_matches_the_plain_etl(plain_output: Path, enriched_output: Path):
    def events(output: Path) -> pl.DataFrame:
        key = ["subject_id", "time", "code"]
        files = sorted((output / "data").rglob("*.parquet"))
        return pl.concat([pl.read_parquet(fp) for fp in files]).select(key).sort(key)

    assert events(enriched_output).equals(events(plain_output))


def test_enrichment_only_adds(plain_codes: dict, enriched_codes: dict):
    """suMEDS keeps every description the ETL wrote, and only extends its parent codes."""
    assert enriched_codes.keys() == plain_codes.keys()
    described = 0
    for code, plain in plain_codes.items():
        enriched = enriched_codes[code]
        if plain["description"] is not None:
            described += 1
            assert enriched["description"] == plain["description"], code
        old_parents = plain.get("parent_codes") or []
        assert (enriched["parent_codes"] or [])[: len(old_parents)] == old_parents, code
    # The ETL describes every code but the diagnoses, here through all three routes.
    assert described == len(plain_codes) - 5
    assert plain_codes["OR_IN"]["description"].startswith(
        "Patient entered the operating room"
    )
    assert plain_codes["LAB//hb//g/dL"]["description"] == "Hemoglobin"
    assert plain_codes["ADMISSION_DEPARTMENT//GS"]["description"].startswith(
        "Surgical department for admission: General surgery"
    )


def test_diagnoses_get_icd10cm_titles_and_parents(enriched_codes: dict):
    i10 = enriched_codes["DIAGNOSIS//ICD//10//I10"]
    assert i10["description"] == title("ICD10CM", "I10")
    assert i10["parent_codes"] == ["ICD10CM//I10"]
    # Codes that are not in the vocabulary, and the null code, stay unenriched.
    for code in ("DIAGNOSIS//ICD//10//U99", "DIAGNOSIS//ICD//10//UNK"):
        assert enriched_codes[code]["description"] is None, code
        assert not enriched_codes[code]["parent_codes"], code


def test_parent_names_map_parents_to_titles(enriched_output: Path):
    """suMEDS names the parents in a map from parent code to name (key-value pairs in Arrow)."""
    table = pq.read_table(enriched_output / "metadata" / "codes.parquet")
    names = dict(
        zip(table["code"].to_pylist(), table["parent_names"].to_pylist(), strict=True)
    )
    assert dict(names["DIAGNOSIS//ICD//10//I10"]) == {
        "ICD10CM//I10": title("ICD10CM", "I10")
    }
    assert dict(names["ICD10_PCS//0DTJ4"])["ICD10PCS//0DT"] == title("ICD10PCS", "0DT")


def test_operations_get_icd10pcs_prefixes(plain_codes: dict, enriched_codes: dict):
    pcs = enriched_codes["ICD10_PCS//0DTJ4"]
    assert pcs["description"] == plain_codes["ICD10_PCS//0DTJ4"]["description"]
    assert pcs["parent_codes"] == [
        f"ICD10PCS//{p}" for p in ("0DTJ4", "0DTJ", "0DT", "0D", "0")
    ]
    assert not enriched_codes["ICD10_PCS//UNK"]["parent_codes"]


def test_medications_get_atc_and_same_dataset_parents(enriched_codes: dict):
    parents = enriched_codes["MEDICATION//N02BE01//IV"]["parent_codes"]
    assert "ATC//N02BE01" in parents
    assert "MEDICATION//N02BE//IV" in parents
    assert "ATC//B05XA03" in enriched_codes["MEDICATION//B05XA03//IV"]["parent_codes"]
    assert not enriched_codes["MEDICATION//UNK//PO"]["parent_codes"]
    assert not enriched_codes["OR_IN"]["parent_codes"]


def test_bad_suMEDS_options_fail_before_the_pipeline(tmp_path: Path, raw_dir: Path):
    argv = [
        RUNNER,
        str(tmp_path / "root"),
        f"--raw-input-dir={raw_dir}",
        "--do-download=false",
    ]
    result = subprocess.run(
        [*argv, "--", "--no-such-option"], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "--no-such-option" in result.stderr
    assert not (tmp_path / "root").exists(), (
        "the pipeline ran despite invalid suMEDS options"
    )


def test_dataset_json_records_the_enrichment(
    enriched_output: Path, enriched_codes: dict
):
    record = _record(enriched_output)
    assert record["enriched"] is True
    assert record["rules"] == "INSPIRE"
    assert record["ontology_source"] == "local"
    coverage = record["coverage"]
    assert coverage["codes"] == len(enriched_codes)
    assert coverage["described"][1] > coverage["described"][0]
    assert coverage["with_parent_codes"][1] > coverage["with_parent_codes"][0]


def test_a_failed_enrichment_keeps_the_build_and_can_be_retried(
    tmp_path: Path,
    raw_dir: Path,
    plain_output: Path,
    enriched_output: Path,
    ontology_source: Path,
):
    """Without packs (as without internet) the build succeeds; `suMEDS-enrich OUT` finishes it."""
    no_packs = tmp_path / "no-packs"
    no_packs.mkdir()
    argv = [
        RUNNER,
        str(tmp_path / "root"),
        f"--raw-input-dir={raw_dir}",
        "--do-download=false",
    ]
    result = run_command([*argv, "--", "--ontology-source", str(no_packs)])
    assert "once the cause is fixed, enrich it with" in result.stderr
    output = tmp_path / "root" / "MEDS_cohort"
    codes = output / "metadata" / "codes.parquet"
    plain = plain_output / "metadata" / "codes.parquet"
    assert codes.read_bytes() == plain.read_bytes()
    record = _record(output)
    assert record["enriched"] is False
    assert record["rules"] == "INSPIRE"
    assert record["retry"].startswith("suMEDS-enrich <MEDS_ROOT>")

    # The packs directory is a path, which the record never holds, so it is given again.
    sumeds_enrich = str(Path(sys.executable).parent / "suMEDS-enrich")
    run_command([sumeds_enrich, str(output), "--ontology-source", str(ontology_source)])
    assert _record(output)["enriched"] is True
    direct = pq.read_table(enriched_output / "metadata" / "codes.parquet")
    assert pq.read_table(codes).equals(direct)


def test_strict_makes_a_failed_enrichment_an_error(tmp_path: Path, raw_dir: Path):
    no_packs = tmp_path / "no-packs"
    no_packs.mkdir()
    argv = [
        RUNNER,
        str(tmp_path / "root"),
        f"--raw-input-dir={raw_dir}",
        "--do-download=false",
        "--",
        "--ontology-source",
        str(no_packs),
        "--strict",
    ]
    result = subprocess.run(argv, capture_output=True, text=True)
    assert result.returncode == 1
    assert "suMEDS-enrich: error:" in result.stderr
