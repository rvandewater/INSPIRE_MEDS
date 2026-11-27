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

meds-extract-run spec=INSPIRE output_dir=$MEDS_OUTPUT_DIR download_dest_dir=$RAW_INPUT_DIR
```

This one command gives the complete build. It downloads the raw INSPIRE files into
`$RAW_INPUT_DIR`, builds a small derived table (the `inspire_subject_table` pre-stage; see
[Birth and death](#birth-and-death)), runs the eight-stage MEDS-Extract pipeline, which also
describes the codes (see [Code metadata](#code-metadata)), links unit siblings (suMEDS's
`sumeds_link_unit_siblings` post-stage), writes `unit` in UCUM (suMEDS's `sumeds_normalize_units`
post-stage), and then fills the official ICD titles and the vocabulary
parents into `metadata/codes.parquet` with [suMEDS](https://github.com/rvandewater/SumMEDS) (the
`sumeds_enrich` post-stage, installed with this package; see
[Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). The MEDS dataset is written to `$MEDS_OUTPUT_DIR`. To reuse a download you already have:

```bash
meds-extract-run spec=INSPIRE output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR
```

Run `meds-extract-run --help` for every option. Rerunning the same command on an existing output
only runs the stages that have not finished (each finished stage leaves `.logs/<stage>.done` in
the output).

Intermediate stage outputs are deleted after a successful run, by default: the spec sets
MEDS-Extract's `etl.cleanup_intermediates: true`, so a finished `$MEDS_OUTPUT_DIR` holds only `data/`,
`metadata/`, `.logs/` and `.meds_extract_run/`. A failed run keeps them, so the same command resumes
where it stopped, and the same command on a finished output still does nothing. To keep them (to look
at a stage's output; `$MEDS_OUTPUT_DIR/.logs/pipeline.log` keeps the stage logs either way), add
`"overrides=['cleanup_intermediates=false']"`. Cleanup never deletes raw data: a download into the
default `$MEDS_OUTPUT_DIR/.meds_extract_run/raw_input` stays, and `download_dest_dir=` elsewhere lets other
output directories reuse it.

**`MEDS_extract-INSPIRE` is deprecated.** It was the complete build up to 1.0.0. It is kept for
one more release as an alias that prints the equivalent `meds-extract-run` command and runs it
(`MEDS_extract-INSPIRE ROOT` builds into `ROOT/MEDS_cohort` from `ROOT/raw_input`, as before,
and `--no-enrich` becomes `skip_stages=[sumeds_enrich]`). It no longer accepts suMEDS options
after a `--`; set them as [stage options](#enriching-code-metadata-with-sumeds) instead.

## Configuration

The whole pipeline is one file, [`src/INSPIRE_MEDS/messy.yaml`](src/INSPIRE_MEDS/messy.yaml),
registered under the `MEDS_extract.pipelines` entry-point group. Its `etl:` block names the
`inspire_subject_table` pre-stage (see [Birth and death](#birth-and-death)), the
`sumeds_link_unit_siblings`, `sumeds_normalize_units` and `sumeds_enrich` post-stages (in that order), and the release's license and PhysioNet page, which MEDS-Extract writes into
`metadata/dataset.json`.

### Pseudo-timestamps

INSPIRE ships no wall-clock times. Every time column is an **offset in minutes**, anchored **per
patient** at that patient's own first hospital admission — `min(admission_time)` is 0 for all
99,886 subjects. Each patient's anchor is then placed at one shared literal origin, the midpoint
of the study window (2011-01-01 .. 2020-12-31) = **2016-01-01**.

So only relative differences *within* a subject are meaningful. The absolute dates in the MEDS
output are not real dates, and comparing them across subjects is meaningless.

### Null handling

A null component of a composite code drops the row since MEDS-Extract 0.7, where 0.6.x rendered
it as the literal `UNK` and kept the row. Components are therefore coalesced with `?? 'UNK'`
wherever a null must not drop the row. The unit in a lab or vital code is stripped of surrounding
whitespace; missing or blank is `UNK`. MEDS-Extract compares units the same way when it writes `unit`
(stripped, blanks dropped), so ` mg` and `mg` give one code.

Two are deliberately left uncoalesced. `route` on medications is never null in the release (0 of
10,854,338 rows), so a coalesce would be dead code. The demographic codes drop rather than mint
an `UNK` category, because a missing demographic is not an observed value.

### Birth and death

`MEDS_BIRTH` and `MEDS_DEATH` are properties of a **subject**, but INSPIRE records what they
derive from on `operations`, which is one row per **operation**:

- `age` is the age on the operation date, rounded to the nearest five years, so a patient with several
    operations in one admission can straddle a bin boundary and carry two ages.
- `inhosp_death_time` appears only on the rows of the admission during which the patient died,
    while `allcause_death_time` is invariant across all their rows. The first is the actual time of
    death, the second 00:00 of the date the death was registered (see
    [Source caveats](#source-caveats-inspire-142)); follow-up ends on 2023-06-15.

MESSY cannot reduce a table over its own rows — self-joins are rejected and dftly is strictly
row-wise — so the `inspire_subject_table` pre-stage
([`src/INSPIRE_MEDS/pre_MEDS.py`](src/INSPIRE_MEDS/pre_MEDS.py), registered under the
`MEDS_transforms.stages` entry-point group) reduces `operations` to one row per subject, and the
config joins it back. That is the only Python step this package ships. `meds-extract-run` runs it
before `convert_to_parquet`; it writes `subjects.parquet` into its own directory
(`$MEDS_OUTPUT_DIR/inspire_subject_table`), which MEDS-Extract searches before the raw input, so
the checksum-verified download is never modified. Like every stage directory, it is deleted once the
run has succeeded, unless you pass `"overrides=['cleanup_intermediates=false']"`.

How the two are placed:

- **`MEDS_BIRTH`** is the date of the earliest operation with the smallest age of the first admission,
    minus that age. INSPIRE rounds ages to the nearest five years, so the recorded age is the centre of its
    bin (a true age between 2.5 years below and 2.5 years above it) and no offset is added: the midpoint
    rule of [alignEHR#54](https://github.com/rvandewater/alignEHR/issues/54). The birth date is uncertain by
    up to 2.5 years. There is no top-coding (see [Source caveats](#source-caveats-inspire-142)).
- **`MEDS_DEATH`** is `inhosp_death_time`, the actual time of an in-hospital death, where there is one, and
    otherwise `allcause_death_time`, 00:00 of the date the death was registered (a median of about 11 days
    after it). Follow-up ends on 2023-06-15. No `MEDS_DEATH` means no recorded death: for the about 2% of
    patients who could not be linked to the registry, vital status is unknown.

| Was (`pre_MEDS.py`)                                  | Now                                                                                                     |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `ORIGIN_PSUEDOTIME`                                  | `_origin: ("2016-01-01"::?"%Y-%m-%d")::datetime`                                                        |
| `+ pl.duration(minutes=offset)`                      | `$_origin + $<col>::minutes`                                                                            |
| `.sort(admission_time).group_by(subject_id).first()` | self-join `cols: {age: min, admission_time: min}` plus an `_is_first` guard on the patient-level events |
| `min(inhosp_death_time, allcause_death_time)`        | a dftly conditional                                                                                     |

### Stay and operation ids

INSPIRE has three levels of id: "A subject_id may be matched to one or more hadm_ids", and "A single
hadm_id may be matched with one or more op_ids"
([PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/)). Only `operations` carries
`hadm_id`; `operations` and `vitals` carry `op_id`; the other tables carry `subject_id` alone. Two
extension columns, both listed under `raw_source_id_columns` in `metadata/dataset.json`, keep them:

| column         | source               | on which events                                                                                                                                                                                                    |
| -------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `hadm_id`      | `operations.hadm_id` | the events of `operations` except the subject-level ones (`MEDS_BIRTH`, `SEX//…`, `RACE//…`, `MEDS_DEATH`) and `ICU_ADMISSION`/`ICU_DISCHARGE`                                                                     |
| `operation_id` | `operations.op_id`   | the operation events of `operations`: `OPERATION_ID`, `VITALDB_CASE_ID`, `OPERATION_DATE`, ASA, emergency, department, anesthesia type, ICD-10-PCS, and the operating-room, operation, anesthesia and bypass times |

`hadm_id` is the name the MIMIC-IV ETL and the other MEDS ETLs give the hospital admission
([alignEHR#55](https://github.com/rvandewater/alignEHR/issues/55)). `WEIGHT`, `HEIGHT`,
`HOSPITAL_ADMISSION` and `HOSPITAL_DISCHARGE` are per admission and carry `hadm_id` only. The
`OPERATION_ID` event still holds `op_id` in its `text_value`.

`operation_id` tells apart the attributes of several operations on the same day: those codes are placed
at the operation date, so before it two operations of one subject on one day with, say, the same
anesthesia type gave one event; now they give one per operation (about 900 such events in 1.4.2).

Two places are left without an id on purpose:

- **The ICU stay.** `icuin_time` and `icuout_time` are recorded on each operation the ICU stay
    follows, and in 1.4.2 about 1,800 ICU admissions appear, at the same time, on operations of more than
    one admission. They belong to no single operation or admission, so `ICU_ADMISSION` and
    `ICU_DISCHARGE` carry neither id (and the release has no ICU-stay id for an `icustay_id`).
- **The intraoperative vitals.** `vitals` has an `op_id`, but where two operations of a subject
    overlap, the release repeats the same measurements under each of them: in 1.4.2, about 323,000 vital
    events merge rows of more than one operation. With an `operation_id` each would become one event per
    operation, about 334,000 extra rows. Tie a vital to its operation by time instead (between the
    operation's `OR_IN` and `OR_OUT`).

## Source caveats (INSPIRE 1.4.2)

The INSPIRE team answered our questions about the 1.4.2 release (INSPIRE team, personal communication,
2026-11-22; to be corrected in the INSPIRE documentation from v1.5). Where their answer differs from the
[PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/) page, the answer is what this ETL
documents. The counts are aggregates we measured on the 1.4.2 release; no row of it is quoted. The codes
do not change.

- **Diagnoses are KCD, not ICD-10-CM.** `diagnosis.icd10_cm` holds the codes recorded in the SNUH EMR in
    the Korean Standard Classification of Diseases (KCD), which is based on WHO ICD-10 and adds Korean
    codes (e.g. U codes). The edition is the one in force on the diagnosis date: KCD-6 until 2015, KCD-7
    from 2016, KCD-8 from 2021. The release has no calendar dates (see
    [Pseudo-timestamps](#pseudo-timestamps)), so the edition of a diagnosis cannot be recovered from it.
    Only the first three characters are released. Most coincide with ICD-10-CM categories, so the code
    keeps its `DIAGNOSIS//ICD//10CM//<code>` label and suMEDS's ICD-10-CM title; 71 of the 1,136 codes do
    not (67 that ICD-10-CM never had, and 4 that exist only as pre-release ICD-10-CM draft concepts), and
    get no title or parent ([#30](https://github.com/rvandewater/INSPIRE_MEDS/issues/30)).
- **Diagnosis times are dates.** `diagnosis.chart_time` is 00:00 of the diagnosis date recorded in the
    EMR (all 2,464,620 rows are at 00:00), not the time the diagnosis was made, and a diagnosis entered at
    or after discharge can carry a date later than the clinical event. The team recommends not using the
    diagnoses of an admission as features during that admission. Diagnoses carry no admission id, so how
    many are dated late cannot be measured. By date, 9.2% of the rows fall within one of the patient's
    admissions (admission day to discharge day), 4.8% within 7 days after a discharge and outside any
    admission, and 24.1% before the first admission; the rest are outpatient-dated.
- **Procedures.** In 1.4.2, `operations.icd10_pcs` was predicted from the procedure name by a model; the
    ICD-10-PCS 2022 table was used only to fix laterality. v1.5 re-derives it from the ICD-10-PCS 2022
    code set.
- **Age** is rounded to the **nearest** five years in all releases (not floored): the level 90 covers
    ages 87.50 to 89.98, nobody is 90 or older, and there is no top-coding. The level 20 starts at 18.03
    in v1.2 to v1.4.x; from v1.5, operations of patients under 19 are excluded. In 1.4.2 the levels run
    from 20 to 90. See [Birth and death](#birth-and-death) for how `MEDS_BIRTH` uses it.
- **Death.** `allcause_death_time` is the date the death was registered in the Resident Registration
    System of the Ministry of the Interior and Safety (MOIS), from a linkage snapshot of 2023-06-15,
    stored as 00:00 of the registration date (all values are at 00:00). Registration follows the death by
    a median of about 11 days: for the 923 patients with both death times in 1.4.2, the registration date
    is a median of 10.6 days (5th to 95th percentile 2.4 to 33.9) after the in-hospital death time.
    `inhosp_death_time` is the actual time of death from the EMR. **Follow-up ends on 2023-06-15**; every
    operation (2011 to 2020) has at least 896 days of follow-up. 2.1% of the patients could not be linked
    to the registry, and for them a missing `allcause_death_time` means unknown vital status, not
    survival; 1.4.2 cannot tell them apart from linked survivors (v1.5 adds `allcause_death`: 1
    registered, 0 linked without a death, null not linked).
- **No LOINC mapping** of the labs from the source team yet; it may come later.
- **Medications** are administered orders (drug name, route, time); doses and amounts were not
    extracted. A combination product is one row per component. The released file has only `drug_name`
    and `atc_code`: the `drug_name2/3` and `atc_code2/3` in `schema.csv` are an error there, and this ETL
    never read them.
- **ICU stays.** `icuin_time` and `icuout_time` are meant as the ICU stay after each operation (ICU
    admissions without surgery are out of scope). In v1.3 to v1.4.2, the stay was linked to operations by
    patient only, and about 30% of the linked rows point to the wrong stay, often one in a later hospital
    admission. Measured on 1.4.2: 19,195 operations have an ICU stay; on 4,887 of them (25.5%) it begins
    after the operation's hospital discharge, and 5,817 (30.3%) do not meet the v1.5 rule below. Of the
    15,977 distinct ICU stays (the `ICU_ADMISSION` events), 2,667 (16.7%) begin neither during nor within
    24 hours after any operation of the same admission. The events keep their times (each is one of the
    patient's ICU admissions), so the effect in MEDS is that `ICU_ADMISSION` and `ICU_DISCHARGE` include
    ICU stays that do not follow surgery. No event is dropped or moved; see
    [#32](https://github.com/rvandewater/INSPIRE_MEDS/issues/32) for a proposed flag. v1.5 links a stay
    only if it begins during the operation or within 24 hours after leaving the operating room, within
    the same admission.
- **`icd10_excluded.csv`**: only the matching diagnosis rows were removed; the patients and their other
    records remain.

**What changes in v1.5** (being finalised, on PhysioNet and KHDP): `icd10_pcs` from the ICD-10-PCS 2022
code set; operations under 19 excluded; an `allcause_death` linkage column; ICU stays linked per
operation and admission; corrected documentation (KCD, dates, age, death); and a fix of a `case_id`
issue. This ETL targets 1.4.2 until it is updated for v1.5.

## Code metadata

`metadata/codes.parquet` carries `code`, `description`, `parent_codes`, `unit`, `unit_source_value` and `code_template`. Up to
1.0.0 only the lab and vital codes had a description (from the release's `parameters.csv`) and no
code had a parent. The ETL now describes every code family whose meaning INSPIRE itself documents,
except the diagnoses, and writes no parent codes: the ICD-10-CM category titles and the
vocabulary parents come from suMEDS, whose `sumeds_enrich` stage runs last (see
[Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). Nothing in the event
data changes.

| Code family                                                                | Route                                                                                          | Source                                                                                                                                                                                                                                                                            |
| -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LAB//…`, `VITAL//…`, `WARD_VITAL//…`                                      | `_metadata: _self:` from the `parameters.csv` join (unchanged)                                 | the release's `parameters.csv`                                                                                                                                                                                                                                                    |
| `ADMISSION_DEPARTMENT//…`                                                  | `_metadata: department:` joins the release's own `department.csv` at ETL time                  | the release's `department.csv` (nothing from it is copied into this repository)                                                                                                                                                                                                   |
| `DIAGNOSIS//ICD//10CM//…`                                                  | none in the ETL                                                                                | the CMS ICD-10-CM category title, added by suMEDS                                                                                                                                                                                                                                 |
| `ICD//10PCS//…`                                                            | `_self` description: the code, cut to its first five characters, and what those characters are | the column documentation ([PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/))                                                                                                                                                                                |
| `MEDICATION//…`                                                            | `_self` description naming the release's own `atc_code` and route                              | the release's own `atc_code` (documented as WHO ATC, mapped by the dataset authors)                                                                                                                                                                                               |
| `SEX`, `RACE`, `ASA`, `EMERGENCY_OPERATION`, `ANESTHESIA_TYPE`             | `_self` templates                                                                              | the column documentation ([PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/) and the column comments in `messy.yaml`); ASA classes paraphrase the ASA Physical Status Classification System (American Society of Anesthesiologists, last amended 2020-12-13) |
| the 19 literal codes (`OR_IN`, `WEIGHT`, `ICU_ADMISSION`, `MEDS_BIRTH`, …) | constant `_self` blocks on the events that emit them                                           | per event, in a `# Source:` comment: the PhysioNet INSPIRE 1.4.2 documentation section or the column comment                                                                                                                                                                      |

**Units.** Every numeric code family also gets a `unit` column, the unit as recorded, then written in UCUM:
`LAB//…`, `VITAL//…` and `WARD_VITAL//…` take the `Unit` of the release's `parameters.csv` (already
the code's last segment, so one unit per code; an item without a `Unit` has the `//UNK` code and a
null `unit`). `WEIGHT` and `HEIGHT`, whose unit is not in the data, take `kg` and `cm` as stated on
the [PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/) page (the cited source is
in a comment in `messy.yaml`). No code changes.

`unit` is a nullable String: the unit of the code's numeric values, one per code, written in
[UCUM](https://ucum.org/) by suMEDS's `sumeds_normalize_units` post-stage (`10^3/uL` becomes
`10*9/L`, the same quantity). The spelling as recorded stays in `unit_source_value`, a spelling suMEDS
does not know is kept as it is, and the codes keep their names (`LAB//wbc//10^3/uL` stays). MEDS-Extract 0.9 and later write it as one String per code and
fail the build if a code has more than one unit
([MEDS_extract#336](https://github.com/mmcdermott/MEDS_extract/pull/336); none does in INSPIRE
1.4.2). The first post-stage, suMEDS's shared `sumeds_link_unit_siblings`
([docs](https://github.com/rvandewater/suMEDS/blob/main/docs/unit_siblings.md)), gives *unit siblings*, codes that differ only in their last `//<unit>` component, the code
without it as an in-dataset parent in `parent_codes` (e.g. `LAB//<item>` for `LAB//<item>//<unit>`),
and adds a row for that parent code unless one exists: the siblings' description if they all share
it (or, if they differ only by their own unit, that description without it), the vocabulary parents
they all share, and a null `unit`. A `//UNK` code of the same item joins its siblings once the item
has a real unit. With `link_single_units: true`, a code ending in its unit gets its stem also when
its item has a single unit, so every `LAB`, `VITAL` and `WARD_VITAL` code with a recorded unit has
its unit-free stem (`LAB//<item>//<unit>` gets `LAB//<item>`). It runs before the enrichment, so the enrichment sees the finished table. INSPIRE's `parameters.csv` has one unit per item, so 1.4.2 has no unit
siblings, and its descriptions state no units (no `parameters.csv` description of an item without a
`Unit` names one), so no unit is taken from description text.

A literal code has no components to join a dictionary on, so its description is a constant
`_self` block on the event that emits it (MEDS-Extract 0.8.1 or later), e.g.
`description: '"Patient entered the operating room (INSPIRE operations.orin_time)"'`.

### Enriching code metadata with suMEDS

Vocabulary work is maintained once, in [suMEDS](https://github.com/rvandewater/SumMEDS), rather
than in every ETL. This package ships the rules for it,
[`src/INSPIRE_MEDS/sumeds_rules.yaml`](src/INSPIRE_MEDS/sumeds_rules.yaml), registered in suMEDS's
`sumeds.rules` entry-point group under the same name as the MEDS-Extract spec, `INSPIRE`, which is
how the spec's `sumeds_enrich: {rules: INSPIRE}` finds them; `--rules INSPIRE` works for any suMEDS
command. The `sumeds_enrich` post-stage enriches `metadata/codes.parquet` in place, after the ETL's
own descriptions; the table is replaced only once the enriched one is complete, and it stays valid
MEDS code metadata. By default the ICD data comes from suMEDS's ontology packs, fetched from GitHub
on first use and cached; no Athena download is needed.

Enrichment is on by default and soft-fails: if it cannot finish, for example on a server without
internet access, `metadata/codes.parquet` is left as the ETL wrote it and the build still succeeds.
The warning, with the command that enriches it later, is in `$MEDS_OUTPUT_DIR/.logs/pipeline.log`.
`metadata/dataset.json` tells whether a build was enriched: `sumeds_enrichment.enriched` is `true`
or `false`, next to the rules, options and coverage used, and the error if it failed.

```bash
jq .sumeds_enrichment.enriched "$MEDS_OUTPUT_DIR/metadata/dataset.json"
```

**Finish the enrichment later.** MEDS-Extract marks the stage done even when it soft-fails, so
rerunning `meds-extract-run` does not try again. Run `suMEDS-enrich` on the build instead; it
reuses the rules and options recorded in `dataset.json`:

```bash
suMEDS-enrich "$MEDS_OUTPUT_DIR"
# Air-gapped: from a copy of the sumeds-ontologies packs directory instead of GitHub
suMEDS-enrich "$MEDS_OUTPUT_DIR" --ontology-source /path/to/sumeds-ontologies/ontologies --offline
```

**Skip it.** `skip_stages=[sumeds_enrich]` builds without the enrichment and without a record. That
build, or one made with an earlier release of this package, has no record to reuse, so add
`--rules INSPIRE` to `suMEDS-enrich` later:

```bash
meds-extract-run spec=INSPIRE output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR \
	skip_stages=[sumeds_enrich]
```

**Stage options for one run.** `meds-extract-run` passes `overrides=` to every stage, so set a
`sumeds_enrich` option (the `suMEDS-enrich` options, spelled with underscores) with `++stage_cfg.`:
the `++` adds the key to the stages that do not declare it, which ignore it. `strict` makes a failed
enrichment fail the run (no `.done` marker, so the same command retries the stage):

```bash
# Fail the run if the enrichment fails
meds-extract-run spec=INSPIRE ... "overrides=['++stage_cfg.strict=true']"
# Also validate the ATC codes against your own OHDSI Athena download
meds-extract-run spec=INSPIRE ... "overrides=['++stage_cfg.athena_csv=/path/to/athena']"
# Use only already cached packs (no network), from a local pack directory
meds-extract-run spec=INSPIRE ... "overrides=['++stage_cfg.offline=true','++stage_cfg.ontology_source=/path/to/packs']"
```

`SUMEDS_ONTOLOGY_SOURCE=/path/to/packs` in the environment sets the pack source too.

**Clusters without internet on compute nodes.** Fill a shared cache once, from a login node, then
run offline on the compute nodes with the same `XDG_CACHE_HOME`:

```bash
export XDG_CACHE_HOME=/shared/project/cache
suMEDS-enrich --prefetch --rules INSPIRE # login node: downloads the pinned pack release
meds-extract-run spec=INSPIRE output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR \
	"overrides=['++stage_cfg.offline=true']" # compute node: no network requests
```

ICD-10-CM and ICD-10-PCS codes are validated against suMEDS's versioned ontology data
(sumeds-ontologies, pinned release), built from the CMS and CDC files. The rules add:

- **Diagnoses** (`inspire-icd10cm-categories`). INSPIRE's diagnoses are KCD codes cut to the first
    three characters, with a list of sensitive categories removed (`icd10_excluded.csv`); the PhysioNet
    page calls them ICD-10-CM (see [Source caveats](#source-caveats-inspire-142)). A code that is also an
    ICD-10-CM category gets the CMS title and the parent `ICD10CM//<category>`. KCD categories that
    ICD-10-CM does not have (71 in 1.4.2) get neither.
- **Operations** (`inspire-icd10pcs-prefixes`). INSPIRE documents `icd10_pcs` as the first five
    characters of an ICD-10-PCS code (section, body system, root operation, body part, approach). In
    1.4.2 it was predicted from the operation name by a model (see
    [Source caveats](#source-caveats-inspire-142)). A valid five-character prefix gets the parent
    `ICD10PCS//<prefix>` and its shorter prefixes (OMOP holds partial codes as
    `ICD10PCS Hierarchy` concepts).
- **Medications** (`inspire-atc-medications`). The parent is the dataset's own ATC code,
    `ATC//<atc_code>`, when it has the shape of an ATC code of any level. ATC is licensed, so
    suMEDS only tags it, unless your Athena download validates it and adds its ancestors.
- **Same-dataset parents** (`inspire-medication-atc-prefixes`). A medication code also gets the
    nearest shorter ATC code given by the same route that INSPIRE contains, e.g.
    `MEDICATION//N02BE//IV` for `MEDICATION//N02BE01//IV`. Diagnosis and operation codes all sit at
    one level (three and five characters), so they have no same-dataset parents.

suMEDS fills only missing descriptions, never removes or rewrites existing metadata, writes
parents as `VOCABULARY//CODE`, and names them in `parent_names`, a map from parent code to name.
A build with `skip_stages=[sumeds_enrich]` has none of these parent codes or the diagnosis
descriptions.

## Parallelism

To convert the full release faster, give `meds-extract-run` a runner file; its `parallelize:` block
becomes every stage's default (this uses the `hydra-joblib-launcher` package, the
`local_parallelism` extra):

```bash
pip install "INSPIRE_MEDS[local_parallelism]"
printf 'parallelize:\n  n_workers: 8\n  launcher: joblib\n' >runner.yaml
meds-extract-run spec=INSPIRE output_dir=$MEDS_OUTPUT_DIR do_download=false \
	input_dir=$RAW_INPUT_DIR stage_runner_fp=runner.yaml
```

## Summary statistics

[`tests/regression/full_summary_stats.json`](tests/regression/full_summary_stats.json) is a
[meds_summary_stats](https://github.com/Medical-Event-Data-Standard/meds_summary_stats) fingerprint of
the MEDS dataset this ETL builds from the full INSPIRE 1.4.2 release with `meds-extract-run spec=INSPIRE`, so its code-metadata coverage includes the suMEDS enrichment. It covers subject, event and measurement
counts, digests of the code vocabulary, per-subject, value and time distributions, code-metadata
coverage, the splits and the schema. It names no code and reports no cell of fewer than 20 subjects and
no minimum or maximum, which is what makes it safe to publish. Because it is committed next to the ETL,
its diff shows what an ETL change did to the output, and two ETL versions can be compared without
access to the data.

Regenerate it in every pull request that changes the output, from a build of that branch:

```bash
# Build the release with this checkout, then summarize it
scripts/summary_stats.sh full --input-dir $RAW_INPUT_DIR
# Or summarize a build of this checkout you already have
scripts/summary_stats.sh full --meds-dir $MEDS_OUTPUT_DIR
```

Both print how the statistics changed against the committed baseline before overwriting it. Add
`--compare` to check a build without writing anything; it exits 1 when
[`tests/regression/policy.yaml`](tests/regression/policy.yaml) flags an error. Rebuilding the same
commit reproduces the baseline exactly, so any finding is a change in the output. CI cannot rebuild the
credentialed release, so the [Summary stats workflow](.github/workflows/summary-stats.yaml) only warns
when a pull request changes `src/` (the config, the pre-stage or the suMEDS rules) without regenerating
the baseline. A MEDS-Extract, suMEDS or polars upgrade can also move the output, so check it with
`--compare`.

## Breaking changes

### 1.3.0 (2026-11-27)

- **`MEDS_BIRTH` moves about half a year later.** Up to 1.2.0 it was the first admission minus (age +
    0.5 years). INSPIRE rounds ages to the nearest five years (INSPIRE team, personal communication,
    2026-11-22), so the recorded age is already the centre of its bin. `MEDS_BIRTH` is now the operation
    date minus the age: of the earliest operation with the smallest age in the first admission (the
    midpoint rule of [alignEHR#54](https://github.com/rvandewater/alignEHR/issues/54)). On the full 1.4.2
    release, every one of the 99,886 births moves later, by 181.6 to 187.6 days at the 0th to 95th
    percentile (median 183.6 days): 182.625 days for the dropped half-year, plus the days from the
    admission to that operation. Codes, values and the number of events are unchanged. To migrate, an age
    computed from `MEDS_BIRTH` is now about 0.5 years smaller and no longer needs a correction; to
    reproduce the old time, subtract 182.625 days and the days from the first `HOSPITAL_ADMISSION` to that
    operation's `OPERATION_DATE`.
- **`MEDS_DEATH` prefers the in-hospital death time.** Up to 1.2.0 it was the earlier of
    `inhosp_death_time` and `allcause_death_time`. `allcause_death_time` is 00:00 of the date the death was
    registered, so where the death was registered on the day it happened, the earlier value was 00:00 of
    that day, before the actual time of death. `MEDS_DEATH` is now `inhosp_death_time` where there is one,
    else `allcause_death_time`. On the full 1.4.2 release, fewer than 20 of the 13,323 deaths (0.5% of the
    923 patients with both times) move, each later and within the same day except one; the number of deaths
    is unchanged. To migrate, nothing is needed for tasks that use death at day resolution or longer
    horizons; to reproduce the old time, take the earlier of the two source columns.

### 1.2.0 (2026-11-20)

- Rebuilt on the full release; `tests/regression/full_summary_stats.json` regenerated.

- **ICD codes name the vocabulary and its edition apart, `ICD//<edition>//<code>`.** Diagnoses
    `DIAGNOSIS//ICD//10//<icd10_cm>` are now `DIAGNOSIS//ICD//10CM//<icd10_cm>`, and operations
    `ICD10_PCS//<icd10_pcs>` are now `ICD//10PCS//<icd10_pcs>`: the layout every MEDS ETL of this family
    uses for ICD (`ICD//10CM`, `ICD//9CM`, `ICD//10PCS`, `ICD//10GM`, `ICD//10CN`). The code after the
    edition is unchanged, and so are its `ICD10CM//…` and `ICD10PCS//…` parents. To migrate, rename codes
    with the regexes `^DIAGNOSIS//ICD//10//` → `DIAGNOSIS//ICD//10CM//` and `^ICD10_PCS//` →
    `ICD//10PCS//`.

- **The unit in a lab or vital code is stripped of surrounding whitespace, and a blank one is
    `UNK`** (`LAB//{item}//{unit}`, `VITAL//{item}//{unit}`, `WARD_VITAL//{item}//{unit}`). Before,
    the `parameters.csv` Unit was copied as recorded: a padded unit (` mg`) gave a code of its own,
    and a blank or empty one gave a code ending in whitespace or in `//`, with no `unit`. No output
    change on the synthetic data; codes change only where a unit was padded or blank. To migrate,
    strip the last part of an old code, and write `UNK` where it is then empty:
    `^((?:LAB|VITAL|WARD_VITAL)//.+?//)\s*(\S(?:.*\S)?)?\s*$` → `\1\2`.

- **`codes.parquet` gains unit-free stem rows and stem parents for single-unit codes**
    ([#27](https://github.com/rvandewater/INSPIRE_MEDS/pull/27)). suMEDS's `sumeds_link_unit_siblings` now runs with `link_single_units: true`:
    every code that ends in its unit gets the code without the unit (its stem) as its last
    `parent_codes` entry, and the stem gets a row of its own (no unit, no events), also when the
    item has a single unit (`LAB//{item}//{unit}` gets `LAB//{item}`, likewise `VITAL` and `WARD_VITAL`). Before, only items recorded in two or more units did.
    Events are unchanged. On a synthetic (MESSY-synth) build, 24 codes gain a stem parent and 24 stem rows are added (8 each `LAB`, `VITAL`, `WARD_VITAL`). To migrate, nothing is needed unless a tool expects every
    `codes.parquet` row to have events: drop the stem rows (no `code_template`, no `unit`), or
    ignore a parent that is the code without its last `//` component.

- **New columns `hadm_id` and `operation_id`.** `hadm_id` is the hospital admission
    (`operations.hadm_id`) and `operation_id` the operation (`operations.op_id`), on the events of
    `operations` listed under [Stay and operation ids](#stay-and-operation-ids); both are listed under
    `raw_source_id_columns` in `metadata/dataset.json`, which listed none before. Codes are unchanged.
    Rows that two operations of one subject shared are now written once per operation, each with its
    own `operation_id`. The static (untimed) `VITALDB_CASE_ID` event carries `operation_id` too, so it
    is written once per operation instead of once per patient: each VitalDB case id belongs to one
    operation, and the `operation_id` links a VitalDB recording to the right surgery. On the full
    1.4.2 release this adds 24,334 static rows (106,626 → 130,960 `VITALDB_CASE_ID` rows) and 909
    timed operation-date events (two operations of one subject on one day, same code and value), so
    measurements go from 140,689,363 to 140,714,606. Subjects, events (unique subject–time pairs)
    and observed codes are unchanged. Nothing needs migrating; to get the old rows back, drop the new
    columns and deduplicate: `df.drop("hadm_id", "operation_id").unique(maintain_order=True)`.

- **Operation and anaesthesia intervals are `OPERATION//START`, `OPERATION//END`, `ANESTHESIA//START` and
    `ANESTHESIA//END`** ([alignEHR#53](https://github.com/rvandewater/alignEHR/issues/53)), not
    `OPERATION_START`, `OPERATION_END`, `ANESTHESIA_START` and `ANESTHESIA_END`. The token is now the second
    part, the spelling all MEDS ETLs move to for the two ends of an interval, so a start pairs with its end by
    swapping `START` for `END`. Events, times and descriptions are otherwise unchanged. `OR_IN`/`OR_OUT` and
    `CPB_ON`/`CPB_OFF` keep their names.

    | Old                | New                 |
    | ------------------ | ------------------- |
    | `OPERATION_START`  | `OPERATION//START`  |
    | `OPERATION_END`    | `OPERATION//END`    |
    | `ANESTHESIA_START` | `ANESTHESIA//START` |
    | `ANESTHESIA_END`   | `ANESTHESIA//END`   |

    To migrate, rewrite old codes with the regex `^(OPERATION|ANESTHESIA)_(START|END)$` → `\1//\2`.

- **Intermediate stage outputs are no longer kept.** Up to 1.1.0, a run left each stage's output in
    `<output_dir>/<stage>/` (`convert_to_parquet/`, `convert_to_MEDS_events/`, `inspire_subject_table/`, `sumeds_enrich/`, …). The spec now sets MEDS-Extract's
    `etl.cleanup_intermediates: true`, so a successful run deletes those directories and keeps only
    `data/`, `metadata/`, `.logs/` and `.meds_extract_run/`. The MEDS data and metadata are
    unchanged. To migrate, add `"overrides=['cleanup_intermediates=false']"` to `meds-extract-run`
    wherever you read a stage directory after the run.

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
