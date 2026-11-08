"""The INSPIRE MEDS ETL.

The pipeline is `messy.yaml`, registered under the `MEDS_extract.pipelines` entry-point group, and
the complete build is one command:

    meds-extract-run spec=INSPIRE output_dir=$MEDS_OUTPUT_DIR download_dest_dir=$RAW_INPUT_DIR

The spec declares two extension stages around the canonical MEDS-Extract stages. MEDS_BIRTH and
MEDS_DEATH are per-subject facts derived from a per-operation table, and MESSY cannot reduce a
table over its own rows, so the `inspire_subject_table` pre-stage (`pre_MEDS`) writes a
one-row-per-subject side table that the config joins back. After the pipeline, suMEDS's
`sumeds_enrich` post-stage enriches `metadata/codes.parquet` with the rules registered under
`sumeds.rules` as `INSPIRE`.
"""

from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files

__package_name__ = "INSPIRE_MEDS"
try:
    __version__ = version(__package_name__)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"

# The name this ETL registers under `MEDS_extract.pipelines`; `meds-extract-run spec=INSPIRE`
# resolves it, and MEDS-Extract uses it as the default dataset name.
PIPELINE_NAME = "INSPIRE"

MESSY_CFG = files(__package_name__).joinpath("messy.yaml")

__all__ = ["MESSY_CFG", "PIPELINE_NAME", "__package_name__", "__version__"]
