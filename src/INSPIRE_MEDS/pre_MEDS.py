#!/usr/bin/env python

"""Performs pre-MEDS data wrangling for INSPIRE.
Adapted from: https://github.com/prockenschaub/AUMCdb_MEDS/blob/main/src/AUMCdb_MEDS/pre_MEDS.py
See the docstring of `main` for more information.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import hydra
import polars as pl
from loguru import logger
from MEDS_transforms.utils import get_shard_prefix, hydra_loguru_init, write_lazyframe
from omegaconf import DictConfig, OmegaConf

from INSPIRE_MEDS import TABLE_PROCESSOR_CFG

ADMISSION_ID = "op_id"
SUBJECT_ID = "subject_id"
ORIGIN_PSUEDOTIME = pl.datetime(year=2011, month=1, day=1) + 0.5 * (
    pl.datetime(year=2020, month=12, day=31) - pl.datetime(year=2011, month=1, day=1)
)


def load_raw_inspire_file(fp: Path, **kwargs) -> pl.LazyFrame:
    """Load a raw INSPIRE file into a Polars DataFrame.

    Args:
        fp: The path to the INSPIRE file.

    Returns:
        The Polars DataFrame containing the INSPIRE data.
    Example:
        >>> with pl.Config(tbl_cols=4):
        >>>     load_raw_inspire_file("tests/operations_synthetic.csv").collect().
        ┌────────────┬───────┬───┬───────────────┬───────────────┐
        │ subject_id ┆ op_id ┆ … ┆ date_of_birth ┆ date_of_death │
        │ ---        ┆ ---   ┆   ┆ ---           ┆ ---           │
        │ str        ┆ str   ┆   ┆ str           ┆ str           │
        ╞════════════╪═══════╪═══╪═══════════════╪═══════════════╡
        └────────────┴───────┴───┴───────────────┴───────────────┘
    """
    return pl.scan_csv(fp, infer_schema_length=10000000, encoding="utf8-lossy", **kwargs)


def get_patient_link(df: pl.LazyFrame) -> pl.LazyFrame:
    """
    Process the operations table to get the patient table and the link table.

    As INSPIRE stores only offset times, note here that we add a CONSTANT TIME ACROSS ALL PATIENTS for the
    true timestamp of their health system admission. This is acceptable because in INSPIRE ONLY RELATIVE
    TIME DIFFERENCES ARE MEANINGFUL, NOT ABSOLUTE TIMES.

    The output of this process is ultimately converted to events via the `patient` key in the
    `configs/event_configs.yaml` file.
    """
    # All patients who received surgery under general, neuraxial, regional, and monitored anesthesia care
    # between January 2011 and December 2020 at SNUH were included.
    # TODO: Check if we can find a more sophisticated way to calculate the origin pseudotime
    # df.sort(SUBJECT_ID, ADMISSION_ID)
    origin_pseudotime = ORIGIN_PSUEDOTIME
    age_in_years = pl.col("age")
    age_in_days = age_in_years * 365.25
    # We assume that the patient was born at the midpoint of the year as we don't know the actual birthdate
    pseudo_date_of_birth = origin_pseudotime - pl.duration(days=(age_in_days - 365.25 / 2))
    pseudo_date_of_death = origin_pseudotime + pl.duration(
        minutes=pl.when(pl.col("inhosp_death_time") < pl.col("allcause_death_time"))
        .then(pl.col("inhosp_death_time"))
        .otherwise(pl.col("allcause_death_time"))
        .cast(pl.Int64)
    )

    return (
        df.sort(by="admission_time")
        .group_by(SUBJECT_ID)
        .first()
        .select(
            SUBJECT_ID,
            pseudo_date_of_birth.alias("date_of_birth"),
            "sex",
            origin_pseudotime.alias("first_admitted_at_time"),
            pseudo_date_of_death.alias("date_of_death"),
        ),
        df.select(SUBJECT_ID, ADMISSION_ID),
    )


def join_and_get_pseudotime_fntr(
    table_name: str,
    offset_col: str | list[str],
    pseudotime_col: str | list[str],
    output_data_cols: list[str] | None = None,
    warning_items: list[str] | None = None,
) -> Callable[[pl.LazyFrame, pl.LazyFrame], pl.LazyFrame]:
    """Returns a function that joins a dataframe to the `patient` table and adds pseudotimes.
    Also raises specified warning strings via the logger for uncertain columns.
    All args except `table_name` are taken from the table_preprocessors.yaml.
    Args:
        table_name: name of the INSPIRE table that should be joined
        offset_col: list of all columns that contain time offsets since the patient's first admission
        pseudotime_col: list of all timestamp columns derived from `offset_col` and the linked `patient`
            table
        output_data_cols: list of all data columns included in the output
        warning_items: any warnings noted in the table_preprocessors.yaml

    Returns:
        Function that expects the raw data stored in the `table_name` table and the joined output of the
        `process_patient_and_admissions` function. Both inputs are expected to be `pl.DataFrame`s.

    Examples:
        >>> func = join_and_get_pseudotime_fntr(
        ...     "operations",
        ...     ["admission_time", "icuin_time", "icuout_time", "orin_time", "orout_time",
        ...      "opstart_time", "opend_time", "discharge_time", "anstart_time", "anend_time",
        ...      "cpbon_time", "cpboff_time", "inhosp_death_time", "allcause_death_time", "opdate"],
        ...     ["admission_time", "icuin_time", "icuout_time", "orin_time", "orout_time",
        ...      "opstart_time", "opend_time", "discharge_time", "anstart_time", "anend_time",
        ...      "cpbon_time", "cpboff_time", "inhosp_death_time", "allcause_death_time", "opdate"],
        ...     ["subject_id", "op_id", "age", "antype", "sex", "weight", "height", "race", "asa",
        ...      "case_id", "hadm_id", "department", "emop", "icd10_pcs", "date_of_birth",
        ...      "date_of_death"],
        ...     ["How should we deal with op_id and subject_id?"]
        ... )
        >>> df = load_raw_inspire_file("tests/operations_synthetic.csv")
        >>> raw_admissions_df = load_raw_inspire_file("tests/operations_synthetic.csv")
        >>> patient_df, link_df = get_patient_link(raw_admissions_df)
        >>> processed_df = func(df, patient_df)
        >>> type(processed_df)
        >>> <class 'polars.lazyframe.frame.LazyFrame'>
    """

    if output_data_cols is None:
        output_data_cols = []

    if isinstance(offset_col, str):
        offset_col = [offset_col]
    if isinstance(pseudotime_col, str):
        pseudotime_col = [pseudotime_col]

    if len(offset_col) != len(pseudotime_col):
        raise ValueError(
            "There must be the same number of `offset_col`s and `pseudotime_col`s specified. Got "
            f"{len(offset_col)} and {len(pseudotime_col)}, respectively."
        )

    def fn(df: pl.LazyFrame, patient_df: pl.LazyFrame) -> pl.LazyFrame:
        f"""Takes the {table_name} table and converts it to a form that includes pseudo-timestamps.

        The output of this process is ultimately converted to events via the `{table_name}` key in the
        `configs/event_configs.yaml` file.

        Args:
            df: The raw {table_name} data.
            patient_df: The processed patient data.

        Returns:
            The processed {table_name} data.
        """

        pseudotimes = [
            (ORIGIN_PSUEDOTIME + pl.duration(minutes=pl.col(offset))).alias(pseudotime)
            for pseudotime, offset in zip(pseudotime_col, offset_col)
        ]

        if warning_items:
            warning_lines = [
                f"NOT SURE ABOUT THE FOLLOWING for {table_name} table. Check with the INSPIRE team:",
                *(f"  - {item}" for item in warning_items),
            ]
            logger.warning("\n".join(warning_lines))
        logger.info(f"Joining {table_name} to patient table...")
        logger.info(df.collect_schema())
        # Join the patient table to the data table, INSPIRE only has subject_id as key
        return df.join(patient_df, on=SUBJECT_ID, how="inner").select(
            SUBJECT_ID, ADMISSION_ID, *pseudotimes, *output_data_cols
        )

    return fn


def process_operations(raw_df: pl.LazyFrame, department_df: pl.LazyFrame) -> pl.LazyFrame:
    """Processes the operations table to include pseudotimes.

    Args:
        df: The raw operations data.
        patient_df: The processed patient data.

    Returns:
        The processed operations data.
    """
    # All patients who received surgery under general, neuraxial, regional, and monitored anesthesia care
    # between January 2011 and December 2020 at SNUH were included.

    raw_df = raw_df.join(department_df, left_on="department", right_on="Abbreviations", how="left")
    raw_df = raw_df.drop("department")
    raw_df = raw_df.with_columns(pl.col("Full name").alias("department"))
    return raw_df


def process_abbreviations(raw_df: pl.LazyFrame, table, parameters: pl.LazyFrame) -> pl.LazyFrame:
    """Processes the labs table to include additional parameters.

    Args:
        raw_df: The raw labs data.
        table: The table name to join with the parameters.
        parameters: The parameters data to join with the labs data.

    Returns:
        The processed labs data.
    """
    # Join the raw labs data with the parameters data
    parameters = parameters.with_columns(pl.all().name.to_lowercase())

    # Select the right table
    parameters = parameters.filter(pl.col("table") == table)

    # Join
    processed_df = raw_df.join(parameters, left_on="item_name", right_on="label", how="left")
    return processed_df


@hydra.main(version_base=None, config_path="configs", config_name="pre_MEDS")
def main(cfg: DictConfig):
    """Performs pre-MEDS data wrangling for INSPIRE.

    Inputs are the raw INSPIRE files, read from the `input_dir` config parameter. Output files are written
    in processed form and as Parquet files to the `cohort_dir` config parameter. Hydra is used to manage
    configuration parameters and logging.
    """

    hydra_loguru_init()

    logger.info(f"Loading table preprocessors from {TABLE_PROCESSOR_CFG}...")
    preprocessors = OmegaConf.load(TABLE_PROCESSOR_CFG)
    functions = {}
    for table_name, preprocessor_cfg in preprocessors.items():
        print(f"  Adding preprocessor for {table_name}:\n{OmegaConf.to_yaml(preprocessor_cfg)}")
        functions[table_name] = join_and_get_pseudotime_fntr(table_name=table_name, **preprocessor_cfg)
    raw_cohort_dir = Path(cfg.input_dir)
    MEDS_input_dir = Path(cfg.output_dir)

    patient_out_fp = MEDS_input_dir / "patient.parquet"
    link_out_fp = MEDS_input_dir / "link_patient_to_admission.parquet"

    if patient_out_fp.is_file():
        logger.info(f"Reloading processed patient df from {str(patient_out_fp.resolve())}")
        patient_df = pl.read_parquet(patient_out_fp, use_pyarrow=True).lazy()
        link_df = pl.read_parquet(link_out_fp, use_pyarrow=True).lazy()
    else:
        logger.info("Processing operations table first...")

        admissions_fp = raw_cohort_dir / "operations.csv"
        logger.info(f"Loading {str(admissions_fp.resolve())}...")
        raw_admissions_df = load_raw_inspire_file(admissions_fp)

        logger.info("Processing patient table...")

        patient_df, link_df = get_patient_link(raw_admissions_df)
        write_lazyframe(patient_df, patient_out_fp)
        write_lazyframe(link_df, link_out_fp)

    patient_df = patient_df.join(link_df, on=SUBJECT_ID)

    all_fps = [fp for fp in raw_cohort_dir.glob("*.csv")]

    unused_tables = {}

    parameters = load_raw_inspire_file(raw_cohort_dir / "parameters.csv")
    for in_fp in all_fps:
        pfx = get_shard_prefix(raw_cohort_dir, in_fp)
        if pfx in unused_tables:
            logger.warning(f"Skipping {pfx} as it is not supported in this pipeline.")
            continue
        elif pfx not in functions:
            logger.warning(f"No function needed for {pfx}. For INSPIRE, THIS IS UNEXPECTED")
            continue

        out_fp = MEDS_input_dir / f"{pfx}.parquet"

        if out_fp.is_file():
            print(f"Done with {pfx}. Continuing")
            continue

        out_fp.parent.mkdir(parents=True, exist_ok=True)

        st = datetime.now()
        logger.info(f"Processing {pfx}...")
        df = load_raw_inspire_file(in_fp)
        if pfx in ["labs", "vitals", "ward_vitals"]:
            df = process_abbreviations(df, pfx, parameters)
        if in_fp == raw_cohort_dir / "operations.csv":
            department_fp = raw_cohort_dir / "department.csv"
            department_df = load_raw_inspire_file(department_fp)
            df = process_operations(df, department_df)
        fn = functions[pfx]
        processed_df = fn(df, patient_df)
        # Sink throws errors, so we use collect instead
        processed_df.sink_parquet(out_fp)
        # processed_df.collect().write_parquet(out_fp)
        logger.info(f"  * Processed and wrote to {str(out_fp.resolve())} in {datetime.now() - st}")

    logger.info(f"Done! All dataframes processed and written to {str(MEDS_input_dir.resolve())}")


if __name__ == "__main__":
    main()
