"""Validate the bundled INSPIRE MESSY config against installed MEDS-Extract."""

import pytest

from MEDS_extract.config import MessyConfig

from INSPIRE_MEDS import MESSY_CFG, PIPELINE_NAME

EXPECTED_TABLES = {
    "operations": {
        "op_id",
        "case_id",
        "opdate",
        "age",
        "sex",
        "weight",
        "height",
        "race",
        "asa",
        "emop",
        "department",
        "antype",
        "icd10_pcs",
        "orin_time",
        "orout_time",
        "opstart_time",
        "opend_time",
        "admission_time",
        "discharge_time",
        "anstart_time",
        "anend_time",
        "cpbon_time",
        "cpboff_time",
        "icuin_time",
        "icuout_time",
        "death_time",
    },
    "labs": {"lab"},
    "medications": {"medication"},
    "diagnosis": {"diagnosis"},
    "vitals": {"vital"},
    "ward_vitals": {"ward_vital"},
}


@pytest.fixture(scope="module")
def cfg() -> MessyConfig:
    return MessyConfig.load(MESSY_CFG)


@pytest.fixture(scope="module")
def by_prefix(cfg: MessyConfig) -> dict:
    return {table.input_prefix: table for table in cfg.event_tables}


@pytest.fixture
def dummy_credentials(monkeypatch):
    """Satisfy source credential interpolations without touching the network."""
    monkeypatch.setenv("DATASET_DOWNLOAD_USERNAME", "not-a-real-user")
    monkeypatch.setenv("DATASET_DOWNLOAD_PASSWORD", "not-a-real-password")


def test_messy_config_parses(cfg: MessyConfig):
    for table in cfg.event_tables:
        assert table.events, f"Table {table.input_prefix!r} declares no events."


def test_expected_tables_and_events(by_prefix: dict):
    assert {
        prefix: {event.name for event in table.events}
        for prefix, table in by_prefix.items()
    } == EXPECTED_TABLES


def test_subject_id_is_inspire_subject_id(cfg: MessyConfig):
    for table in cfg.event_tables:
        assert table.subject_id_node is not None, table.input_prefix
        assert table.subject_id_node.referenced_columns == {"subject_id"}


def test_value_columns_are_column_reads(by_prefix: dict):
    for table_name, event_name in (
        ("labs", "lab"),
        ("vitals", "vital"),
        ("ward_vitals", "ward_vital"),
    ):
        assert {"value", "item_name"} <= by_prefix[table_name].events[
            0
        ].referenced_columns


def test_physionet_source_targets_inspire_release(cfg: MessyConfig, dummy_credentials):
    sources = cfg.selected_sources("dataset")
    assert len(sources) == 1
    assert (
        getattr(sources[0], "_base_url", None)
        == "https://physionet.org/files/inspire/1.4.2/"
    )


def test_etl_block(cfg: MessyConfig):
    assert cfg.etl.dataset_name == "INSPIRE"
    assert cfg.etl.stage_options["n_subjects_per_shard"] == 1000


def test_sources_declare_dataset_version(cfg: MessyConfig):
    assert cfg.sources_version == "1.4.2"


def test_registered_pipeline_name_resolves():
    cfg = MessyConfig.load(PIPELINE_NAME)
    assert cfg.registered_name == PIPELINE_NAME
    assert [table.input_prefix for table in cfg.event_tables]
