# 🥬 PyVegh

**PyVegh** is the official Python binding for the Vegh snapshot engine, developed by CodeTease.

It delivers the raw performance of Rust (Zstd compression, Tar archiving, SHA256 hashing) wrapped in a modern, flexible Python interface.

> "Tight packing, swift unpacking, no nonsense."

## Features

* **Blazing Fast:** Core logic is implemented in Rust using PyO3, ensuring near-native speed.
* **Modern CLI:** Built with `Typer` and `Rich` for a clean, professional command-line experience.
* **Integrity:** Native SHA256 checksum verification for all snapshots.
* **Smart Filtering:** Automatically respects `.veghignore` and `.gitignore` rules.
* **Network Ready:** Built-in `send` command to push snapshots to remote endpoints.

## Installation

Install directly from PyPI:
```bash
pip install pyvegh
```

## CLI Usage

PyVegh provides a command-line interface via the `vegh` command.

**Create a snapshot:**
```bash
vegh snap ./my-project --output backup.snap
```

**Restore a snapshot:**
```bash
vegh restore backup.snap ./restored-folder
```

**Verify integrity:**
```bash
vegh check backup.snap
```

**Send to a server:**
```bash
vegh send backup.snap https://api.teaserverse.online/test --auth YOUR_TOKEN
```

## Library Usage

You can also use PyVegh as a library in your own Python scripts:
```python
from vegh import create_snap, restore_snap, check_integrity

# Create a snapshot
# Returns the number of files compressed
count = create_snap("src_folder", "backup.snap")
print(f"Compressed {count} files.")

# Check integrity
# Returns the SHA256 hash string
checksum = check_integrity("backup.snap")
print(f"SHA256: {checksum}")

# Restore
restore_snap("backup.snap", "dest_folder")
```

## License

This project is under the **MIT License**.