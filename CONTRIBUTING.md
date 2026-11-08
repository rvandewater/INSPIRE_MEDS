# Contributing

This repository is a **MEDS-Extract 0.8 ETL**. Almost all of it is one declarative file; there is
very little Python to change.

```bash
pip install INSPIRE_MEDS
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD

meds-extract-run spec=INSPIRE output_dir=$MEDS_OUTPUT_DIR download_dest_dir=$RAW_INPUT_DIR
```

## What the 0.7 layout looks like

Under MEDS-Transforms 0.2.x an ETL was a Python package: `pre_MEDS.py` reshaped the raw data,
`dataset.yaml` described the download, `ETL.yaml` listed the pipeline stages, and
`event_configs.yaml` described the events. **0.7 replaces all four with a single MESSY file**
(MEDS-Extract Specification Syntax YAML):

| Used to be                     | Now                                                                       |
| ------------------------------ | ------------------------------------------------------------------------- |
| `dataset.yaml` + `download.py` | the `sources:` block                                                      |
| `configs/ETL.yaml`             | nothing — the stage sequence is canonical and not configurable            |
| `configs/event_configs.yaml`   | the event tables, written in [dftly](https://github.com/mmcdermott/dftly) |
| `pre_MEDS.py`                  | mostly `_table.joins` / `_table.cols` (see the exception below)           |
| a custom runner script         | `etl.pre_stages` / `etl.post_stages` (MEDS-Extract 0.8.1)                 |

The file lives at [`src/INSPIRE_MEDS/messy.yaml`](src/INSPIRE_MEDS/messy.yaml) and is registered
under the `MEDS_extract.pipelines` entry-point group in `pyproject.toml`:

```toml
[project.entry-points."MEDS_extract.pipelines"]
INSPIRE = "INSPIRE_MEDS:messy.yaml"
```

That value is **parsed, not imported** — it names a package and a data file inside it, which is
why it does not look like a normal `module:attribute` target.

## Editing the config

Three things bite newcomers, in rough order of how often:

1. **`$` is load-bearing.** `$value` reads the `value` column; a bare `value` is the string
    literal `"value"`. This fails silently — wrong data, no error.
2. **A null code component drops the row.** `f"LAB//{$item}"` discards every row with a null
    `item`. Coalesce with `?? 'UNK'` when the row must be kept. 0.6.x rendered nulls as `UNK`
    automatically; 0.7 does not.
3. **There is no null test.** dftly has no `is_null`. The working idiom is `X if $c == $c`, which
    is true for any present value (including `0`, `inf` and `NaN`) and null for a missing one.
    A bare `null` parses as the *string* `"null"`. Tracked in
    [dftly#113](https://github.com/mmcdermott/dftly/issues/113).

Also worth knowing: `_table.joins` may name several targets, but each only once and independent
of the others (no join may key on a column another join delivers), `_table.cols` entries are
evaluated in order so later ones may reference earlier ones, and adding a Duration to a Datetime
must be written as `anchor - (-$offset)::minutes` because `+` does not lower correctly.

## The pieces of Python

`MEDS_BIRTH` and `MEDS_DEATH` are per-**subject** facts, but INSPIRE records what they derive from
on `operations`, which is one row per **operation**. MESSY cannot reduce a table over its own rows
— self-joins are rejected and dftly is strictly row-wise — so
[`src/INSPIRE_MEDS/pre_MEDS.py`](src/INSPIRE_MEDS/pre_MEDS.py) reduces `operations` to one row per
subject, and the config joins that side table back. It is a MEDS-Extract **pre-stage**: registered
as `inspire_subject_table` under the `MEDS_transforms.stages` entry-point group and named in the
spec's `etl.pre_stages`, so `meds-extract-run` runs it before `convert_to_parquet`. It writes
`subjects.parquet` into its own output directory, which MEDS-Extract searches before the raw input,
so the checksum-verified download is never modified.

Everything else is in the spec. Literal codes are described by constant `_self` blocks. Vocabulary
parents and titles come from suMEDS's `sumeds_enrich` post-stage (`etl.post_stages`) with
[`src/INSPIRE_MEDS/sumeds_rules.yaml`](src/INSPIRE_MEDS/sumeds_rules.yaml), registered under
`sumeds.rules` as `INSPIRE`, so there is no suMEDS code in this package. See the README section
"Code metadata". `MEDS_extract-INSPIRE` ([`src/INSPIRE_MEDS/__main__.py`](src/INSPIRE_MEDS/__main__.py))
is a deprecated alias of `meds-extract-run spec=INSPIRE`, kept for one release.

If you are porting another dataset and it needs no such reduction, drop the Python entirely: users
call `meds-extract-run spec=<NAME>` either way.

## Testing

```bash
uv sync --extra tests
uv run pytest tests/
uv run pre-commit run --all-files
```

There is no demo release for INSPIRE, and the credentialed download cannot run in CI. Instead,
`tests/test_run_with_enrichment.py` runs `meds-extract-run spec=INSPIRE` end to end over a tiny,
entirely synthetic release in INSPIRE's column layout (`tests/conftest.py`), offline against a tiny
synthetic suMEDS ontology pack; `tests/test_runner_equivalence.py` checks that the build still has
the codes, descriptions and event counts the 1.0.0 runner built from that release; and the Synthetic
ETL workflow runs the same command over data that MESSY-synth generates from the config. Changes that affect the output should still be validated by running the
ETL over the real release and comparing cohort statistics before and after (see the README section
"Summary statistics").

## Changing what the ETL emits

Any change to codes, timestamps or which rows are emitted is a change to everybody's downstream
cohort. State the measured effect in the PR — row counts, subject counts, code-vocabulary size
before and after — rather than only describing the intent. Several defects in this repository were
found precisely because those numbers were compared.

# Development

## Environment

The project uses [uv](https://docs.astral.sh/uv/) for its environment, lockfile,
build, and commands.

```bash
uv sync --extra tests
uv run pre-commit install
uv run pre-commit run --all-files
uv run pytest
uv run mkdocs build --strict
```

A plain `pre-commit run` checks only staged files, so hooks legitimately skip
when nothing matching is staged. Use `--all-files` for a full repository check.

Build the distribution with:

```bash
uv build
```

## Release

Bump and commit the project version before creating the matching tag:

```bash
uv version --bump patch
uv run pytest
uv build --clear
git add pyproject.toml uv.lock
git commit -m "Release $(uv version --short)"
VERSION=$(uv version --short)
git tag -a "$VERSION" -m "Release $VERSION"
git push origin main "$VERSION"
```

The tag workflow rejects mismatched versions, then publishes the distributions
to PyPI and creates the GitHub release.

```bash
uv sync --extra tests
uv version --bump patch   # 0.0.12 → 0.0.13
uv version --bump minor   # 0.0.12 → 0.1.0
uv version --bump major   # 0.0.12 → 1.0.0
```
