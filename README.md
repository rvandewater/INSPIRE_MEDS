# INSPIRE-MEDS

[![PyPI - Version](https://img.shields.io/pypi/v/INSPIRE_MEDS)](https://pypi.org/project/INSPIRE_MEDS/)
[![codecov](https://codecov.io/gh/rvandewater/INSPIRE_MEDS/graph/badge.svg?token=RW6JXHNT0W)](https://codecov.io/gh/rvandewater/INSPIRE_MEDS)
[![tests](https://github.com/rvandewater/INSPIRE_MEDS/actions/workflows/tests.yaml/badge.svg)](https://github.com/rvandewater/INSPIRE_MEDS/actions/workflows/tests.yml)
[![code-quality](https://github.com/rvandewater/INSPIRE_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/rvandewater/INSPIRE_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/-Python_3.11-blue?logo=python&logoColor=white)
![Static Badge](https://img.shields.io/badge/MEDS-0.3.3-blue)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/rvandewater/INSPIRE_MEDS#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/rvandewater/INSPIRE_MEDS/pulls)
[![contributors](https://img.shields.io/github/contributors/rvandewater/INSPIRE_MEDS.svg)](https://github.com/rvandewater/INSPIRE_MEDS/graphs/contributors)
[![DOI](https://zenodo.org/badge/918107518.svg)](https://doi.org/10.5281/zenodo.14891939)

The INSPIRE dataset is a publicly available research dataset in perioperative medicine, which includes approximately 130,000 cases (50% of all surgical cases) who underwent anesthesia for surgery at an academic institution in South Korea between 2011 and 2020. This comprehensive dataset includes patient characteristics such as age, sex, American Society of Anesthesiologists physical status classification, diagnosis, surgical procedure code, department, and type of anesthesia. It also includes vital signs in the operating theatre, general wards, and intensive care units (ICUs), laboratory results from six months before admission to six months after discharge, and medication during hospitalization. Complications include total hospital and ICU length of stay and in-hospital death.
This pipeline extracts the INSPIRE dataset (from physionet, https://physionet.org/content/inspire/) into the MEDS format.

## Usage:

```bash
pip install INSPIRE_MEDS
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD

meds-extract-run spec=INSPIRE output_dir=$MEDS_COHORT_DIR
```

That downloads the raw INSPIRE files and runs the full eight-stage MEDS-Extract pipeline. To
stage the raw data separately:

```bash
meds-extract-download spec=INSPIRE output_dir=$RAW_INPUT_DIR
meds-extract-run spec=INSPIRE output_dir=$MEDS_COHORT_DIR download_key=null input_dir=$RAW_INPUT_DIR
```

There is no longer a pre-MEDS step: it is all config.

## Configuration

**This package contains no ETL code.** The entire pipeline is one file,
[`src/INSPIRE_MEDS/configs/messy.yaml`](src/INSPIRE_MEDS/configs/messy.yaml), registered under the
`MEDS_extract.pipelines` entry-point group, and run with
`meds-extract-run spec=INSPIRE output_dir=...`.

INSPIRE stores only offsets (in minutes) from a single fixed origin shared by every patient; only
relative differences are meaningful. That origin is the midpoint of the study window
(2011-01-01 .. 2020-12-31) = **2016-01-01**, now written as a literal in `_table.cols` instead of
computed in Python.

| Was (`pre_MEDS.py`) | Now |
| --- | --- |
| `ORIGIN_PSUEDOTIME` | `_origin: ("2016-01-01"::?"%Y-%m-%d")::datetime` |
| `+ pl.duration(minutes=offset)` | `$_origin + $<col>::minutes` |
| `.sort(admission_time).group_by(subject_id).first()` | self-join `cols: {age: min, admission_time: min}` plus an `_is_first` guard on the patient-level events |
| `min(inhosp_death_time, allcause_death_time)` | a dftly conditional |


## MEDS-transforms settings

If you want to convert a large dataset, you can use parallelization with MEDS-transforms
(the MEDS-transformation step that takes the longest).

Using local parallelization with the `hydra-joblib-launcher` package, you can set the number of workers:

```
pip install hydra-joblib-launcher --upgrade
```

Then, you can set the number of workers as environment variable:

```bash
export N_WORKERS=8
```

Moreover, you can set the number of subjects per shard to balance the parallelization overhead based on how many
subjects you have in your dataset:

```bash
export N_SUBJECTS_PER_SHARD=100000
```

## The MIMIC-IV OMOP Dataset

We use the demo dataset for MIMIC-IV in the OMOP format, which is a subset of the MIMIC-IV dataset.
This dataset downloaded from Physionet does not include the standard dictionary linking definitions but should otherwise
be functional

## Particularities

- Care site is added to the visit as text
- Add support for care_site table (visit_detail)
