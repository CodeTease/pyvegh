# PyVegh - The CodeTease Snapshot Tool (Python Binding)
# Copyright (c) 2025 CodeTease

from ._core import create_snap, restore_snap, check_integrity, get_metadata, count_locs

__version__ = "0.2.3"
__all__ = ["create_snap", "restore_snap", "check_integrity", "get_metadata", "count_locs", "__version__"]