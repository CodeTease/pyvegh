# PyVegh - The Snapshot Tool (Python Binding)
# Copyright (c) 2026 CodeTease

from ._core import (
    create_snap,
    dry_run_snap,
    restore_snap,
    check_integrity,
    get_metadata,
    count_locs,
    scan_locs_dir,
    read_snapshot_text,
)

__version__ = "0.9.1"
__all__ = [
    "create_snap",
    "dry_run_snap",
    "restore_snap",
    "check_integrity",
    "get_metadata",
    "count_locs",
    "scan_locs_dir",
    "read_snapshot_text",
    "__version__",
]
