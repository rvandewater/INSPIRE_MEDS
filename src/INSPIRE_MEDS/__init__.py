"""The INSPIRE MEDS ETL.

Nearly all of the pipeline is `messy.yaml`, registered under the `MEDS_extract.pipelines`
entry-point group. The one exception is a small pre-MEDS reduction: MEDS_BIRTH and MEDS_DEATH
are per-subject facts derived from a per-operation table, and MESSY cannot reduce a table over
its own rows, so `pre_MEDS.build_subject_table` produces a one-row-per-subject side table that
the config joins back. `MEDS_extract-INSPIRE` is the complete build: it chains download, that
step, `meds-extract-run`, a step that describes the literal codes in `metadata/codes.parquet`, and
suMEDS enrichment of that file with the rules registered under `sumeds.rules` as `INSPIRE`:

    MEDS_extract-INSPIRE $ROOT_OUTPUT_DIR

Running `meds-extract-run spec=INSPIRE` directly also works, provided the side table already
exists in the input directory, but it skips the last two steps.
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
