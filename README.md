# 🥬 PyVegh

**PyVegh** is the official Python binding for the Vegh snapshot engine, developed by **CodeTease**.

It delivers the raw performance of Rust (Zstd multithreaded compression, Tar archiving, Blake3 hashing) wrapped in a modern, flexible Python interface.

> "Tight packing, swift unpacking, no nonsense."

## Features

* **Blazing Fast:** Core logic is implemented in Rust using PyO3, utilizing **Zstd Multithreading** and the next-gen **Blake3** hashing algorithm.
* **Analytics Dashboard:** Instantly visualize your project's Lines of Code (LOC) with a beautiful terminal dashboard—no extraction required.
* **Dry-Run Mode:** Simulate snapshot creation to check file sizes and detect sensitive data risks before packing.
* **Integrity v2:** Verify data integrity at lightning speed with **Blake3** and inspect metadata (author, timestamp, tool version) without unpacking.
* **Smart Upload:** Built-in `send` command supporting concurrent **Chunked Uploads** for large files.
* **Smart Filtering:** Automatically respects `.veghignore` and `.gitignore` rules.
* **Vegh Hooks:** Allow you to custom automation shell command while snapping.
* **Deep Inspection:** Peek into files (`cat`) and compare snapshots (`diff`) without unpacking.

## Installation

Install directly from PyPI:
```bash
pip install pyvegh
````

Or build from source (requires Rust):

```bash
maturin develop --release
```

## CLI Usage

PyVegh provides a powerful command-line interface via the `vegh` (or `pyvegh`) command.

### 1\. Configuration 

Set up your default server URL and Auth Token so you don't have to type them every time.

```bash
vegh config
# Or one-liner:
vegh config --url https://api.teaserverse.online/test --auth YOUR_TOKEN
```

### 2\. Create Snapshot

Pack a directory into a highly compressed snapshot.

```bash
# Basic snapshot
vegh snap ./my-project --output backup.vegh

# Dry-Run (Simulation) - Check for large/sensitive files
vegh snap ./my-project --dry-run
```

### 3\. Analytics 

View the CodeTease Analytics Dashboard to break down your project by language and lines of code.

```bash
vegh loc backup.vegh
```

### 4\. Inspect & Verify

Check file integrity (Blake3) and view embedded metadata.

```bash
vegh check backup.vegh
```

### 5\. Restore

Restore the snapshot to a target directory. Supports **Partial Restore**.

```bash
# Full restore
vegh restore backup.vegh ./restored-folder

# Partial restore (Specific files or folders)
vegh restore backup.vegh ./restored-folder --path src/main.rs --path config/
```

### 6\. Peek & Diff

Inspect content without extracting.

```bash
# View a file's content inside the snapshot
vegh cat backup.vegh src/main.rs

# Compare snapshot with a directory
vegh diff backup.vegh ./current-project
```

### 7\. Send

Send the snapshot to a remote server. PyVegh now supports **Chunked Uploads** for reliability.

```bash
# Auto-detects if chunking is needed, or force it:
vegh send backup.vegh --force-chunk
```

### 8\. Doctor

Check your environment and installation health.

```bash
vegh doctor
```

### 9\. Hooks example

Create a `.veghhooks.json` in your workspace.

```json
{
  "hooks": {
    "pre": [
      "echo '🚀 Pre-snap hook started...'",
      "echo 'Preparing to backup...'",
      "echo 'Backing up database...'",
      "echo 'Backup completed.'"
    ],
    "post": [
      "echo '🎉 Snapshot completed!'",
      "echo 'Cleaning up...'"
    ]
  }
}
```

## Library Usage

You can also use PyVegh as a library in your own Python scripts:

```python
import json
from vegh import create_snap, restore_snap, check_integrity, get_metadata

# 1. Create a snapshot
# Returns the number of files compressed
count = create_snap("src_folder", "backup.vegh", comment="Automated backup")
print(f"Compressed {count} files.")

# 2. Check integrity (Now uses Blake3)
checksum = check_integrity("backup.vegh")
print(f"Blake3 Hash: {checksum}")

# 3. Read Metadata (Fast, no unpacking)
raw_meta = get_metadata("backup.vegh")
meta = json.loads(raw_meta)
print(f"Snapshot created by: {meta.get('author')}")

# 4. Restore
restore_snap("backup.vegh", "dest_folder")
```

## License

This project is under the **MIT License**.
