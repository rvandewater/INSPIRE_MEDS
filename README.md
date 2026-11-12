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
describes the codes (see [Code metadata](#code-metadata)), and then fills the official ICD titles
and the vocabulary parents into `metadata/codes.parquet` with
[suMEDS](https://github.com/rvandewater/SumMEDS) (the `sumeds_enrich` post-stage, installed with
this package; see [Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). The
MEDS dataset is written to `$MEDS_OUTPUT_DIR`. To reuse a download you already have:

```bash
meds-extract-run spec=INSPIRE output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR
```

Run `meds-extract-run --help` for every option. Rerunning the same command on an existing output
only runs the stages that have not finished (each finished stage leaves `.logs/<stage>.done` in
the output).

**`MEDS_extract-INSPIRE` is deprecated.** It was the complete build up to 1.0.0. It is kept for
one more release as an alias that prints the equivalent `meds-extract-run` command and runs it
(`MEDS_extract-INSPIRE ROOT` builds into `ROOT/MEDS_cohort` from `ROOT/raw_input`, as before,
and `--no-enrich` becomes `skip_stages=[sumeds_enrich]`). It no longer accepts suMEDS options
after a `--`; set them as [stage options](#enriching-code-metadata-with-sumeds) instead.

## Configuration

The whole pipeline is one file, [`src/INSPIRE_MEDS/messy.yaml`](src/INSPIRE_MEDS/messy.yaml),
registered under the `MEDS_extract.pipelines` entry-point group. Its `etl:` block names the
`inspire_subject_table` pre-stage (see [Birth and death](#birth-and-death)) and the `sumeds_enrich`
post-stage, and the release's license and PhysioNet page, which MEDS-Extract writes into
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
row-wise — so the `inspire_subject_table` pre-stage
([`src/INSPIRE_MEDS/pre_MEDS.py`](src/INSPIRE_MEDS/pre_MEDS.py), registered under the
`MEDS_transforms.stages` entry-point group) reduces `operations` to one row per subject, and the
config joins it back. That is the only Python step this package ships. `meds-extract-run` runs it
before `convert_to_parquet`; it writes `subjects.parquet` into its own directory
(`$MEDS_OUTPUT_DIR/inspire_subject_table`), which MEDS-Extract searches before the raw input, so
the checksum-verified download is never modified.

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
vocabulary parents come from suMEDS, whose `sumeds_enrich` stage runs last (see
[Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). Nothing in the event
data changes.

| Code family                                                                | Route                                                                                          | Source                                                                                                                                                                                                                                                                            |
| -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LAB//…`, `VITAL//…`, `WARD_VITAL//…`                                      | `_metadata: _self:` from the `parameters.csv` join (unchanged)                                 | the release's `parameters.csv`                                                                                                                                                                                                                                                    |
| `ADMISSION_DEPARTMENT//…`                                                  | `_metadata: department:` joins the release's own `department.csv` at ETL time                  | the release's `department.csv` (nothing from it is copied into this repository)                                                                                                                                                                                                   |
| `DIAGNOSIS//ICD//10//…`                                                    | none in the ETL                                                                                | the CMS ICD-10-CM category title, added by suMEDS                                                                                                                                                                                                                                 |
| `ICD10_PCS//…`                                                             | `_self` description: the code, cut to its first five characters, and what those characters are | the column documentation ([PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/))                                                                                                                                                                                |
| `MEDICATION//…`                                                            | `_self` description naming the release's own `atc_code` and route                              | the release's own `atc_code` (documented as WHO ATC, mapped by the dataset authors)                                                                                                                                                                                               |
| `SEX`, `RACE`, `ASA`, `EMERGENCY_OPERATION`, `ANESTHESIA_TYPE`             | `_self` templates                                                                              | the column documentation ([PhysioNet INSPIRE 1.4.2](https://physionet.org/content/inspire/1.4.2/) and the column comments in `messy.yaml`); ASA classes paraphrase the ASA Physical Status Classification System (American Society of Anesthesiologists, last amended 2020-12-13) |
| the 19 literal codes (`OR_IN`, `WEIGHT`, `ICU_ADMISSION`, `MEDS_BIRTH`, …) | constant `_self` blocks on the events that emit them                                           | per event, in a `# Source:` comment: the PhysioNet INSPIRE 1.4.2 documentation section or the column comment                                                                                                                                                                      |

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
