"""Unpack the PhysioNet zip archive staged by `meds-extract-download`.

INSPIRE ships as a single zip, and MEDS-Extract's download layer has no post-fetch archive
unpack yet (mmcdermott/MEDS_extract#92), so this small step stands between
`meds-extract-download` and the pre-MEDS transform. It preserves the behaviour the old
`download.py` had inline: extract members by basename into the input directory (the pre-MEDS
step expects flat `<table>.csv` names, not the archive's nested release directory), skipping
`__MACOSX/` cruft.

Once MEDS_extract#92 lands, this module can be deleted and the `sources:` block can declare the
unpack directly.
"""

import logging
import shutil
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


def unpack_archives(input_dir: Path, do_overwrite: bool = False) -> list[Path]:
    """Extract every ``*.zip`` in ``input_dir``, flattening members to their basenames.

    Args:
        input_dir: The directory holding the downloaded archive(s).
        do_overwrite: If False (the default), an archive whose members are all already present is
            skipped and left in place. If True, members are re-extracted unconditionally.

    Returns:
        The archives that were extracted, in sorted order.

    Raises:
        FileNotFoundError: If ``input_dir`` does not exist.

    Examples:
        >>> import tempfile, zipfile
        >>> tmp = Path(tempfile.mkdtemp())
        >>> with zipfile.ZipFile(tmp / "inspire.zip", "w") as zf:
        ...     zf.writestr("inspire/1.4.2/operations.csv", "subject_id\\n1\\n")
        ...     zf.writestr("inspire/1.4.2/labs.csv", "subject_id\\n2\\n")
        ...     zf.writestr("__MACOSX/._operations.csv", "junk")
        >>> [p.name for p in unpack_archives(tmp)]
        ['inspire.zip']

        Members land flat, and the `__MACOSX` entry is dropped:

        >>> sorted(p.name for p in tmp.glob("*.csv"))
        ['labs.csv', 'operations.csv']
        >>> (tmp / "operations.csv").read_text()
        'subject_id\\n1\\n'

        The archive is removed once extracted, so a second run is a no-op:

        >>> (tmp / "inspire.zip").exists()
        False
        >>> unpack_archives(tmp)
        []

        A missing directory is an error, not a silent no-op:

        >>> unpack_archives(tmp / "nope")
        Traceback (most recent call last):
            ...
        FileNotFoundError: No such input directory: ...nope
    """
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"No such input directory: {input_dir}")

    extracted = []
    for archive in sorted(input_dir.glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            members = [
                m
                for m in zf.infolist()
                if not m.is_dir()
                and not m.filename.startswith("__MACOSX/")
                and Path(m.filename).name
            ]
            already_present = all((input_dir / Path(m.filename).name).exists() for m in members)
            if members and already_present and not do_overwrite:
                logger.info(
                    "Skipping %s: all %d members already extracted (pass do_overwrite=True to "
                    "force).",
                    archive.name,
                    len(members),
                )
                continue

            logger.info("Extracting %s (%d members) to %s", archive.name, len(members), input_dir)
            for member in members:
                dest = input_dir / Path(member.filename).name
                with zf.open(member) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)

        archive.unlink()
        extracted.append(archive)

    return extracted
