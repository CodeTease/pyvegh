# PyVegh - The CodeTease Snapshot Tool (Python Binding)
# Copyright (c) 2025 CodeTease

from ._core import (
    create_snap, 
    dry_run_snap, 
    restore_snap, 
    check_integrity, 
    get_metadata, 
    count_locs,
    scan_locs_dir
)

__version__ = "0.3.3"
__all__ = [
    "create_snap", 
    "dry_run_snap", 
    "restore_snap", 
    "check_integrity", 
    "get_metadata", 
    "count_locs", 
    "scan_locs_dir",
    "__version__"
]