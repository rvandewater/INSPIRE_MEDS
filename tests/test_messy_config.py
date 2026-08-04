"""Validate the bundled MESSY config against the installed MEDS-Extract.

These tests need no raw data and no credentials, so they run in CI on every push. They are the
regression net for the 0.7 migration: a MESSY file that stops parsing (a bad dftly expression, a
stray 0.6.x key, an `etl:` option MEDS-Extract does not accept) fails here rather than several
stages into a multi-hour extraction run.
"""

import pytest

from MEDS_extract.config import MessyConfig

from INSPIRE_MEDS import MESSY_CFG, PIPELINE_NAME


@pytest.fixture(scope="module")
def cfg() -> MessyConfig:
    return MessyConfig.load(MESSY_CFG)


@pytest.fixture(scope="module")
def by_prefix(cfg: MessyConfig) -> dict:
    return {t.input_prefix: t for t in cfg.event_tables}


@pytest.fixture
def dummy_credentials(monkeypatch):
    """Satisfy the `${oc.env:...}` interpolations in the `sources:` block.

    `selected_sources()` resolves interpolations for the selected bucket, so inspecting the
    declared sources needs the credential vars to exist. The values are never used -- nothing here
    touches the network -- but without this the test only passes on a machine that happens to have
    real credentials exported.
    """
    monkeypatch.setenv("DATASET_DOWNLOAD_USERNAME", "not-a-real-user")
    monkeypatch.setenv("DATASET_DOWNLOAD_PASSWORD", "not-a-real-password")


def test_messy_config_parses(cfg: MessyConfig):
    """Every event table, code expression, and time cast in the config is valid dftly."""
    tables = cfg.event_tables
    assert tables, "MESSY config declares no event tables."
    for table in tables:
        assert table.events, f"Table {table.input_prefix!r} declares no events."


def test_expected_tables(by_prefix: dict):
    """The migrated config covers exactly the tables the 0.6.x event config covered."""
    assert set(by_prefix) == {
        "operations",
        "labs",
        "medications",
        "diagnosis",
        "vitals",
        "ward_vitals",
    }
    assert len(by_prefix["operations"].events) == 30


def test_subject_id_is_subject_id(cfg: MessyConfig):
    """Every table inherits the global `_defaults.subject_id`."""
    for table in cfg.event_tables:
        assert table.subject_id_node is not None, table.input_prefix
        assert table.subject_id_node.referenced_columns == {"subject_id"}


def test_diagnosis_reads_the_icd10_column(by_prefix: dict):
    """The diagnosis code reads `icd10_cm` rather than a literal.

    The 0.6.x config wrote `col(icd10_cm` -- with no closing paren -- so YAML parsed it as the
    plain string "col(icd10_cm" and every diagnosis code was emitted as the literal
    `DIAGNOSIS//ICD//10//col(icd10_cm` instead of the patient's actual ICD-10-CM code. This test
    pins the fix.
    """
    dx = by_prefix["diagnosis"].events[0]
    assert "icd10_cm" in dx.code_source_columns
    assert "col(icd10_cm" not in dx.raw_code


def test_value_columns_are_column_reads_not_literals(by_prefix: dict):
    """`numeric_value`/`text_value` read columns, not bare-string literals.

    The 0.6.x config wrote `numeric_value: value` / `text_value: description`, which under dftly
    would be the *literal* strings rather than raw-column reads -- a silent data-corruption trap
    rather than a load error, so it is worth pinning.
    """
    for prefix in ("labs", "vitals", "ward_vitals"):
        cols = by_prefix[prefix].events[0].referenced_columns
        assert "value" in cols, prefix
        assert "description" in cols, prefix

    assert "drug_name" in by_prefix["medications"].events[0].referenced_columns


def test_physionet_source_declares_unarchive(cfg: MessyConfig, dummy_credentials):
    """Archive members are expanded by the download layer, not by ETL code.

    `auto` infers the format per member and is a no-op on non-archives (`README.md`, `*.csv.gz`
    -- gz compression is not a tar archive), so declaring it source-level is safe even though the
    release mixes archive and non-archive members under one SHA256SUMS.
    """
    sources = cfg.selected_sources("dataset")
    assert sources, "no sources declared in the `dataset` bucket"
    for src in sources:
        assert getattr(src, "_unarchive", None) == "auto", type(src).__name__


def test_etl_block(cfg: MessyConfig):
    """The reserved `etl:` block carries the dataset identity and stage options."""
    assert cfg.etl.dataset_name == "INSPIRE"
    assert cfg.etl.stage_options["n_subjects_per_shard"] == 1000


def test_sources_declare_dataset_version(cfg: MessyConfig):
    """`sources.dataset_version` is what stamps `etl_metadata.dataset_version` on the output."""
    assert cfg.sources_version == "1.4.2"


def test_registered_pipeline_name_resolves():
    """The `MEDS_extract.pipelines` entry point resolves to the bundled MESSY file.

    This is what makes `meds-extract-run spec=INSPIRE output_dir=...` work.
    """
    cfg = MessyConfig.load(PIPELINE_NAME)
    assert cfg.registered_name == PIPELINE_NAME
    assert [t.input_prefix for t in cfg.event_tables]
