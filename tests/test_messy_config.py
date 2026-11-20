"""Validate the bundled INSPIRE MESSY config against installed MEDS-Extract."""

import json

import polars as pl
import pytest
from meds import DatasetMetadataSchema

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
    assert [stage.name for stage in cfg.etl.pre_stages] == ["inspire_subject_table"]
    assert [stage.name for stage in cfg.etl.post_stages] == [
        "sumeds_link_unit_siblings",
        "sumeds_normalize_units",
        "sumeds_enrich",
    ]
    assert cfg.etl.post_stages[2].options == {"rules": PIPELINE_NAME}


def test_dataset_json_describes_the_release(plain_output):
    """The `etl:` descriptors reach `metadata/dataset.json`, which stays valid MEDS metadata."""
    metadata = json.loads((plain_output / "metadata" / "dataset.json").read_text())
    DatasetMetadataSchema.validate(metadata)
    assert metadata["license"] == "Korea Credentialed Health Data License-1-0-0"
    assert metadata["description_uri"] == "https://physionet.org/content/inspire/1.4.2/"
    assert metadata["raw_source_id_columns"] == ["hadm_id", "operation_id"]


def test_stay_and_operation_ids(plain_output):
    """`hadm_id` and `operation_id` come from `operations` and are set where they are unambiguous."""
    events = pl.concat(
        [pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")]
    )
    table = events["source_block"].str.split("/").list.first()
    ops = events.filter(table == "operations")
    # Subject-level events, and the ICU stay, which the release repeats across operations.
    idless_codes = ["MEDS_BIRTH", "MEDS_DEATH", "ICU_ADMISSION", "ICU_DISCHARGE"]
    idless = ops.filter(
        pl.col("code").is_in(idless_codes)
        | pl.col("code").str.starts_with("SEX//")
        | pl.col("code").str.starts_with("RACE//")
    )
    assert idless.height > 0
    assert idless["hadm_id"].is_null().all()
    assert idless["operation_id"].is_null().all()
    other_ops = ops.join(idless, on=ops.columns, how="anti", nulls_equal=True)
    assert other_ops["hadm_id"].is_not_null().all()
    # Subject 1's two operations share one admission.
    assert set(
        other_ops.filter(pl.col("subject_id") == 1)["hadm_id"].unique().to_list()
    ) == {200000001}
    # Every operation event names its operation; the OPERATION_ID event repeats it in text_value.
    op_events = other_ops.filter(pl.col("operation_id").is_not_null())
    assert set(op_events["operation_id"].unique().to_list()) == {
        400000001,
        400000002,
        400000003,
        400000004,
    }
    op_id_events = ops.filter(pl.col("code") == "OPERATION_ID")
    assert (
        op_id_events["text_value"] == op_id_events["operation_id"].cast(pl.String)
    ).all()
    per_admission = other_ops.filter(
        pl.col("code").is_in(
            ["WEIGHT", "HEIGHT", "HOSPITAL_ADMISSION", "HOSPITAL_DISCHARGE"]
        )
    )
    assert per_admission.height > 0
    assert per_admission["operation_id"].is_null().all()
    # No other table carries either id.
    assert (
        events.filter(table != "operations")
        .select(
            pl.col("hadm_id").is_null().all() & pl.col("operation_id").is_null().all()
        )
        .item()
    )


def test_sources_declare_dataset_version(cfg: MessyConfig):
    assert cfg.sources_version == "1.4.2"


def test_registered_pipeline_name_resolves():
    cfg = MessyConfig.load(PIPELINE_NAME)
    assert cfg.registered_name == PIPELINE_NAME
    assert [table.input_prefix for table in cfg.event_tables]
