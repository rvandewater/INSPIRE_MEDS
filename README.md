# INSPIRE-MEDS

[![PyPI - Version](https://img.shields.io/pypi/v/INSPIRE_MEDS)](https://pypi.org/project/INSPIRE_MEDS/)
[![codecov](https://codecov.io/gh/rvandewater/INSPIRE_MEDS/graph/badge.svg?token=RW6JXHNT0W)](https://codecov.io/gh/rvandewater/INSPIRE_MEDS)
[![tests](https://github.com/rvandewater/INSPIRE_MEDS/actions/workflows/synthetic-etl.yaml/badge.svg)](https://github.com/rvandewater/INSPIRE_MEDS/actions/workflows/synthetic-etl.yaml)
[![code-quality](https://github.com/rvandewater/INSPIRE_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/rvandewater/INSPIRE_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/python-%3E%3D3.11.4%2C%20%3C3.14-blue?logo=python&logoColor=white)
![Static Badge](https://img.shields.io/badge/MEDS-0.4.1-blue)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/rvandewater/INSPIRE_MEDS#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/rvandewater/INSPIRE_MEDS/pulls)
[![contributors](https://img.shields.io/github/contributors/rvandewater/INSPIRE_MEDS.svg)](https://github.com/rvandewater/INSPIRE_MEDS/graphs/contributors)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.14891939.svg)](https://doi.org/10.5281/zenodo.14891939)

The INSPIRE dataset is a publicly available research dataset in perioperative medicine, which includes approximately 130,000 cases (50% of all surgical cases) who underwent anesthesia for surgery at an academic institution in South Korea between 2011 and 2020. This comprehensive dataset includes patient characteristics such as age, sex, American Society of Anesthesiologists physical status classification, diagnosis, surgical procedure code, department, and type of anesthesia. It also includes vital signs in the operating theatre, general wards, and intensive care units (ICUs), laboratory results from six months before admission to six months after discharge, and medication during hospitalization. Complications include total hospital and ICU length of stay and in-hospital death.
This pipeline extracts the INSPIRE dataset (from physionet, https://physionet.org/content/inspire/) into the MEDS format.

## Usage:

```bash
pip install INSPIRE_MEDS
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD

MEDS_extract-INSPIRE $ROOT_OUTPUT_DIR
```

That downloads the raw INSPIRE files, builds a small derived table (see
[Birth and death](#birth-and-death)), and runs the full eight-stage MEDS-Extract pipeline. To
reuse a download you already have:

```bash
MEDS_extract-INSPIRE $ROOT_OUTPUT_DIR --raw-input-dir $RAW_INPUT_DIR --do-download false
```

## Configuration

Nearly all of the pipeline is one file,
[`src/INSPIRE_MEDS/messy.yaml`](src/INSPIRE_MEDS/messy.yaml), registered under the
`MEDS_extract.pipelines` entry-point group. The exceptions are described under
[Birth and death](#birth-and-death) and [Code metadata](#code-metadata) below.

### Pseudo-timestamps

INSPIRE ships no wall-clock times. Every time column is an **offset in minutes**, anchored **per
patient** at that patient's own first hospital admission — `min(admission_time)` is 0 for all
99,886 subjects. Each patient's anchor is then placed at one shared literal origin, the midpoint
of the study window (2011-01-01 .. 2020-12-31) = **2016-01-01**.

So only relative differences *within* a subject are meaningful. The absolute dates in the MEDS
output are not real dates, and comparing them across subjects is meaningless.

### Null handling

A null component of a composite code drops the row under MEDS-Extract 0.7, where 0.6.x rendered
it as the literal `UNK` and kept the row. Components are therefore coalesced with `?? 'UNK'`
wherever a null must not drop the row.

Two are deliberately left uncoalesced. `route` on medications is never null in the release (0 of
10,854,338 rows), so a coalesce would be dead code. The demographic codes drop rather than mint
an `UNK` category, because a missing demographic is not an observed value.

### Birth and death

`MEDS_BIRTH` and `MEDS_DEATH` are properties of a **subject**, but INSPIRE records what they
derive from on `operations`, which is one row per **operation**:

- `age` is the age on the operation date, quantised to 5-year bins, so a patient with several
    operations in one admission can straddle a bin boundary and carry two ages.
- `inhosp_death_time` appears only on the rows of the admission during which the patient died,
    while `allcause_death_time` is invariant across all their rows.

MESSY cannot reduce a table over its own rows — self-joins are rejected and dftly is strictly
row-wise — so `INSPIRE_MEDS.pre_MEDS` reduces `operations` to one row per subject and the config
joins it back. Apart from the [code metadata](#code-metadata) step, that is the only Python this
package ships. It runs against a staging directory of symlinks, so the checksum-verified download is
never modified.

| Was (`pre_MEDS.py`)                                  | Now                                                                                                     |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `ORIGIN_PSUEDOTIME`                                  | `_origin: ("2016-01-01"::?"%Y-%m-%d")::datetime`                                                        |
| `+ pl.duration(minutes=offset)`                      | `$_origin + $<col>::minutes`                                                                            |
| `.sort(admission_time).group_by(subject_id).first()` | self-join `cols: {age: min, admission_time: min}` plus an `_is_first` guard on the patient-level events |
| `min(inhosp_death_time, allcause_death_time)`        | a dftly conditional                                                                                     |

## Code metadata

`metadata/codes.parquet` carries `code`, `description`, `parent_codes` and `code_template`. Up to
1.0.0 only the lab and vital codes had a description (from the release's `parameters.csv`) and no
code had a parent. The ETL now describes every code family whose meaning INSPIRE itself documents,
except the diagnoses, and writes no parent codes: the ICD-10-CM category titles and the
vocabulary parents come from suMEDS (see
[Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). Nothing in the event
data changes.

| Code family                                                                | Route                                                                                                                                         | Source                                                                                                                                                                                                                                                                            |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LAB//…`, `VITAL//…`, `WARD_VITAL//…`                                      | `_metadata: _self:` from the `parameters.csv` join (unchanged)                                                                                | the release's `parameters.csv`                                                                                                                                                                                                                                                    |
| `ADMISSION_DEPARTMENT//…`                                                  | `_metadata: department:` joins the release's own `department.csv` at ETL time                                                                 | the release's `department.csv` (nothing from it is copied into this repository)                                                                                                                                                                                                   |
| `DIAGNOSIS//ICD//10//…`                                                    | none in the ETL                                                                                                                               | the CMS ICD-10-CM category title, added by suMEDS                                                                                                                                                                                                                                 |
| `ICD10_PCS//…`                                                             | `_self` description: the code, cut to its first five characters, and what those characters are                                                | the column documentation ([PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/))                                                                                                                                                                                |
| `MEDICATION//…`                                                            | `_self` description naming the release's own `atc_code` and route                                                                             | the release's own `atc_code` (documented as WHO ATC, mapped by the dataset authors)                                                                                                                                                                                               |
| `SEX`, `RACE`, `ASA`, `EMERGENCY_OPERATION`, `ANESTHESIA_TYPE`             | `_self` templates                                                                                                                             | the column documentation ([PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/) and the column comments in `messy.yaml`); ASA classes paraphrase the ASA Physical Status Classification System (American Society of Anesthesiologists, last amended 2020-12-13) |
| the 19 literal codes (`OR_IN`, `WEIGHT`, `ICU_ADMISSION`, `MEDS_BIRTH`, …) | `src/INSPIRE_MEDS/metadata/static_code_descriptions.csv`, filled into the finished `codes.parquet` by the last step of `MEDS_extract-INSPIRE` | per row, in the table's `source` column: the PhysioNet INSPIRE 1.4.2 documentation section or the `messy.yaml` column comment                                                                                                                                                     |

Literal codes need the post-pipeline step because MEDS-Extract joins `_metadata` on a code's
components, which a literal code does not have, and the pre-existing `metadata/codes.parquet` route
it documents is not read by the standard pipeline. The step only fills null descriptions, never
adds codes, and rewrites the file with the MEDS code-metadata types (`string`, `list<string>`).

### Enriching code metadata with suMEDS

Vocabulary work is maintained once, in [suMEDS](https://github.com/rvandewater/SumMEDS), rather
than in every ETL. This package ships the rules for it,
[`src/INSPIRE_MEDS/sumeds_rules.yaml`](src/INSPIRE_MEDS/sumeds_rules.yaml):

```bash
pip install sumeds  # a release with rules support (newer than 0.3.2)
suMEDS-enrich $ROOT_OUTPUT_DIR/MEDS_cohort/metadata/codes.parquet -o codes_enriched.parquet \
    --rules src/INSPIRE_MEDS/sumeds_rules.yaml
# Optional: validate the ATC codes against your own OHDSI Athena download.
suMEDS-enrich $ROOT_OUTPUT_DIR/MEDS_cohort/metadata/codes.parquet -o codes_enriched.parquet \
    --rules src/INSPIRE_MEDS/sumeds_rules.yaml --athena-csv /path/to/athena
```

ICD-10-CM and ICD-10-PCS codes are validated against suMEDS's versioned ontology data
(sumeds-ontologies, pinned release), built from the public-domain CMS and CDC files. The rules add:

- **Diagnoses** (`inspire-icd10cm-categories`). INSPIRE documents its diagnoses as ICD-10-CM, cut
    to the first three characters, with a list of sensitive categories removed
    (`icd10_excluded.csv`). A code that is an ICD-10-CM category gets the CMS title and the parent
    `ICD10CM//<category>`. Codes that ICD-10-CM does not have (likely codes from the Korean KCD that
    were not converted) get neither.
- **Operations** (`inspire-icd10pcs-prefixes`). INSPIRE documents `icd10_pcs` as the first five
    characters of an ICD-10-PCS code (section, body system, root operation, body part, approach),
    mapped by hand from the operation name. A valid five-character prefix gets the parent
    `ICD10PCS//<prefix>` and its shorter prefixes (OMOP holds partial codes as
    `ICD10PCS Hierarchy` concepts).
- **Medications** (`inspire-atc-medications`). The parent is the dataset's own ATC code,
    `ATC//<atc_code>`, when it has the shape of an ATC code of any level. ATC is licensed, so
    suMEDS only tags it, unless your Athena download validates it and adds its ancestors.
- **Same-dataset parents** (`inspire-medication-atc-prefixes`). A medication code also gets the
    nearest shorter ATC code given by the same route that INSPIRE contains, e.g.
    `MEDICATION//N02BE//IV` for `MEDICATION//N02BE01//IV`. Diagnosis and operation codes all sit at
    one level (three and five characters), so they have no same-dataset parents.

suMEDS fills only missing descriptions, never removes or rewrites existing metadata, and writes
parents as `VOCABULARY//CODE`. The ETL alone no longer emits these parent codes or the diagnosis
descriptions.

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

## Citation

Please cite the following original paper when using the INSPIRE dataset:

```bibtex
@article{PhysioNet-inspire-1.4.2,
  author = {Lim, Leerang and Lee, Hyung-Chul},
  title = {{INSPIRE, a publicly available research dataset for perioperative medicine}},
  journal = {{PhysioNet}},
  year = {2026},
  month = jun,
  note = {Version 1.4.2},
  doi = {10.13026/1eay-yc85},
  url = {https://doi.org/10.13026/1eay-yc85}
}
```

Also cite the ETL framework used to extract the dataset:

```bibtex
@software{van_de_Water_INSPIRE_MEDS_ETL_2025,
author = {van de Water, Robin Philippus},
doi = {10.5281/zenodo.14891940},
license = {MIT},
month = feb,
title = {{INSPIRE\_MEDS ETL}},
url = {https://github.com/rvandewater/INSPIRE_MEDS},
year = {2025}
}
```

For citing MEDS in general:

```bibtex
@article{mcdermott2026meds,
  title={MEDS—An Emerging Data Standard and Ecosystem for Health AI Research},
  author={McDermott, Matthew BA and Steinberg, Ethan and Fries, Jason A and van de Water, Robin P and Pang, Chao and Rockenschaub, Patrick and Renc, Pawel and Oh, Jungwoo and Stankevi{\v{c}}i{\=u}t{\.e}, Kamil{\.e} and Xu, Justin and others},
  journal={NEJM AI},
  volume={3},
  number={6},
  pages={AIra2501253},
  year={2026},
  publisher={Massachusetts Medical Society}
}
```
