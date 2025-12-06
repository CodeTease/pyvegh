import pytest
import os
from vegh import create_snap, check_integrity, get_metadata

# This is a Smoke Test to ensure Rust bindings load correctly into Python.
# Actual compression/decompression logic is heavily tested in the Shell script.

def test_binding_imports():
    """Ensure import doesn't raise 'ModuleNotFoundError' or 'ImportError'."""
    assert callable(create_snap)
    assert callable(check_integrity)

def test_basic_flow(tmp_path):
    """Quick test of basic flow via Python API."""
    # 1. Setup
    source = tmp_path / "src"
    source.mkdir()
    (source / "test.txt").write_text("Hello Teaserverse")
    
    snap_file = tmp_path / "test.vegh"
    
    # 2. Create Snap
    count = create_snap(str(source), str(snap_file), comment="Pytest")
    assert count > 0
    assert snap_file.exists()
    
    # 3. Check Metadata
    raw_meta = get_metadata(str(snap_file))
    assert "Pytest" in raw_meta
    
    # 4. Check Integrity
    checksum = check_integrity(str(snap_file))
    assert len(checksum) == 64 # SHA256 length