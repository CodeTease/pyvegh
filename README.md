# 🥬 PyVegh

**PyVegh** is the official Python binding for the Vegh snapshot engine, developed by CodeTease.

It delivers the raw performance of Rust (Zstd compression, Tar archiving, SHA256 hashing) wrapped in a modern, flexible Python interface.

> "Tight packing, swift unpacking, no nonsense."

## Features

* **Blazing Fast:** Core logic is implemented in Rust using PyO3, ensuring near-native speed.
* **Modern CLI:** Built with `Typer` and `Rich` for a clean, professional command-line experience.
* **Integrity:** Native SHA256 checksum verification plus instant metadata inspection (author, timestamp, comments) without unpacking.
* **Smart Filtering:** Automatically respects `.veghignore` and `.gitignore` rules.
* **Code Analytics:** Instant **LOC (Lines of Code)** counting inside snapshots without extraction.
* **Network Ready:** Built-in `send` command to push snapshots to remote endpoints.

## Installation

Install directly from PyPI (or build locally using Maturin):
```bash
pip install pyvegh
```

## CLI Usage

PyVegh provides a command-line interface via the `vegh` command.

1. **Create a snapshot:**

Pack a directory into a highly compressed snapshot.
```bash
vegh snap ./my-project --output backup.snap
```

2. **Inspect & Verify**

Check file integrity and view embedded metadata (Author, Timestamp, Tool Version).
```bash
vegh check backup.snap
```

3. **List Contents**

View files inside the snapshot without extracting.
```bash
vegh list backup.snap
```

4. **Restore**

Restore the snapshot to a target directory.
```bash
vegh restore backup.snap ./restored-folder
```

5. **Send**

Send the snapshot to a remote server. (Now with chunking and --force-chunk option!)
```bash
vegh send backup.snap https://api.teaserverse.online/test --auth YOUR_TOKEN --force-chunk
```

6. **Analytics (LOC)**

Count Lines of Code instantly.
```bash
vegh loc backup.snap
```

## Library Usage

You can also use PyVegh as a library in your own Python scripts:
```python
import json
from vegh import create_snap, restore_snap, check_integrity, get_metadata

# 1. Create a snapshot
# Returns the number of files compressed
count = create_snap("src_folder", "backup.snap", comment="Automated backup")
print(f"Compressed {count} files.")

# 2. Check integrity (SHA256)
checksum = check_integrity("backup.snap")
print(f"SHA256: {checksum}")

# 3. Read Metadata (Fast, no unpacking)
# Returns a JSON string containing author, timestamp, etc.
raw_meta = get_metadata("backup.snap")
meta = json.loads(raw_meta)
print(f"Snapshot created by: {meta.get('author')}")

# 4. Restore
restore_snap("backup.snap", "dest_folder")
```

## License

This project is under the **MIT License**.