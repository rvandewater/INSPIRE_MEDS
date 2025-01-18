#!/usr/bin/env python
"""Performs pre-MEDS data wrangling for MIMIC-IV."""
from functools import partial

import hydra
from MEDS_transforms.utils import hydra_loguru_init
from omegaconf import DictConfig
from pathlib import Path
from loguru import logger
from . import PRE_MEDS_CFG
import hydra
import polars as pl
from loguru import logger
from MEDS_transforms.extract.utils import get_supported_fp
from MEDS_transforms.utils import get_shard_prefix, hydra_loguru_init, write_lazyframe
from omegaconf import DictConfig
FUNCTIONS = {}
ICD_DFS_TO_FIX = []
@hydra.main(version_base=None, config_path=str(PRE_MEDS_CFG.parent), config_name=PRE_MEDS_CFG.stem)
def main(cfg: DictConfig):
    """Performs pre-MEDS data wrangling for INSERT DATASET NAME HERE."""

    #raise NotImplementedError("Please implement the pre_MEDS.py script for your dataset.")
    hydra_loguru_init()

    input_dir = Path(cfg.input_dir)
    MEDS_input_dir = Path(cfg.output_dir)

    done_fp = MEDS_input_dir / ".done"
    if done_fp.is_file() and not cfg.do_overwrite:
        logger.info(
            f"Pre-MEDS transformation already complete as {done_fp} exists and "
            f"do_overwrite={cfg.do_overwrite}. Returning."
        )
        exit(0)

    all_fps = list(input_dir.rglob("*/*.*"))
    all_fps += list(input_dir.rglob("*.*"))

    dfs_to_load = {}
    seen_fps = {}

    for in_fp in all_fps:
        pfx = get_shard_prefix(input_dir, in_fp)

        try:
            fp, read_fn = get_supported_fp(input_dir, pfx)
        except FileNotFoundError:
            logger.info(f"Skipping {pfx} @ {str(in_fp.resolve())} as no compatible dataframe file was found.")
            continue

        if fp.suffix in [".csv", ".csv.gz"]:
            read_fn = partial(read_fn, infer_schema_length=100000)

        if str(fp.resolve()) in seen_fps:
            continue
        else:
            seen_fps[str(fp.resolve())] = read_fn

        out_fp = MEDS_input_dir / fp.relative_to(input_dir)

        if out_fp.is_file():
            print(f"Done with {pfx}. Continuing")
            continue

        out_fp.parent.mkdir(parents=True, exist_ok=True)

        if pfx not in FUNCTIONS and pfx not in [p for p, _ in ICD_DFS_TO_FIX]:
            logger.info(
                f"No function needed for {pfx}: " f"Symlinking {str(fp.resolve())} to {str(out_fp.resolve())}"
            )
            out_fp.symlink_to(fp)
            continue
        elif pfx in FUNCTIONS:
            out_fp = MEDS_input_dir / f"{pfx}.parquet"
            if out_fp.is_file():
                print(f"Done with {pfx}. Continuing")
                continue

            fn, need_df = FUNCTIONS[pfx]
            if not need_df:
                st = datetime.now()
                logger.info(f"Processing {pfx}...")
                df = read_fn(fp)
                logger.info(f"  Loaded raw {fp} in {datetime.now() - st}")
                processed_df = fn(df)
                write_lazyframe(processed_df, out_fp)
                logger.info(f"  Processed and wrote to {str(out_fp.resolve())} in {datetime.now() - st}")
            else:
                needed_pfx, needed_cols = need_df
                if needed_pfx not in dfs_to_load:
                    dfs_to_load[needed_pfx] = {"fps": set(), "cols": set()}

                dfs_to_load[needed_pfx]["fps"].add(fp)
                dfs_to_load[needed_pfx]["cols"].update(needed_cols)
if __name__ == "__main__":
    main()
