# 🥬 PyVegh

**PyVegh** is the official Python binding for the Vegh snapshot engine, developed by **CodeTease**.

It delivers the raw performance of Rust (Zstd multithreaded compression, Tar archiving, Blake3 hashing) wrapped in a modern, flexible Python interface.

> "Tight packing, swift unpacking, no nonsense."

## Features

* **Blazing Fast:** Core logic is implemented in Rust using PyO3, utilizing **Zstd Multithreading** and the next-gen **Blake3** hashing algorithm.
* **AI-Ready Context:** Generate clean, token-optimized XML prompts for ChatGPT/Claude in milliseconds.
* **Analytics Dashboard:** Instantly visualize your project's Lines of Code (LOC) with a beautiful terminal dashboard, no extraction required.
* **Dry-Run Mode:** Simulate snapshot creation to check file sizes and detect sensitive data risks before packing.
* **Integrity v2:** Verify data integrity at lightning speed with **Blake3** and inspect metadata (author, timestamp, tool version) without unpacking.
* **Smart Upload:** Built-in `send` command supporting concurrent **Chunked Uploads** for large files.
* **Smart Filtering:** Automatically respects `.veghignore` and `.gitignore` rules.
* **Vegh Hooks:** Allow you to custom automation shell command while snapping.
* **Deep Inspection:** Peek into files (`cat`) and compare snapshots (`diff`) without unpacking.

## Installation

Install directly from PyPI:
```shell
pip install pyvegh

# Or via uv
uv pip install pyvegh
```

Or build from source (requires Rust):

```shell
maturin develop --release
```

## CLI Usage

PyVegh provides a powerful command-line interface via the `vegh` (or `pyvegh`) command.

### 1\. Configuration 

Set up your default server URL and Auth Token so you don't have to type them every time.

```shell
vegh config
# Or one-liner:
vegh config send --url https://api.teaserverse.online/test --auth YOUR_TOKEN

# List current configuration
vegh config list

# Reset configuration to defaults
vegh config reset
```

### 2\. Create Snapshot

Pack a directory into a highly compressed snapshot.

```shell
# Basic snapshot
vegh snap ./my-project --output backup.vegh

# Dry-Run (Simulation) - Check for large/sensitive files
vegh snap ./my-project --dry-run
```

### 3\. LOC

View the Analytics Dashboard to break down your project by language and lines of code.

```shell
vegh loc backup.vegh

# Show Source Lines of Code (SLOC) instead of total LOC
# Excludes blank lines and comments
vegh loc backup.vegh --sloc
```

### 4\. Prompt

Generate a structured XML context of your codebase to feed directly into ChatGPT, Claude, or Gemini.
```shell
# Generate XML context to stdout
vegh prompt .

# Clean Mode (Recommended):
# Removes lock files (package-lock.json, Cargo.lock), logs, secrets and other unnecessary files.
vegh prompt . --clean

# Copy to Clipboard (One-shot):
vegh prompt . --clean --copy

# Save to file
vegh prompt . --clean --output context.xml
```

### 5\. Prune

Clean up old snapshots to free disk space.

```shell
# Keep only the 5 most recent snapshots in the current directory
vegh prune --keep 5

# Force clean without confirmation (useful for CI/CD)
vegh prune --keep 1 --force
```

### 6\. Check

Check file integrity (Blake3) and view embedded metadata.

```shell
vegh check backup.vegh
```

### 7\. Restore

Restore the snapshot to a target directory. Supports **Partial Restore**.

```shell
# Full restore
vegh restore backup.vegh ./restored-folder

# Partial restore (Specific files or folders)
vegh restore backup.vegh ./restored-folder --path src/main.rs --path config/

# Flatten directory structure (Extract files directly to output dir)
vegh restore backup.vegh ./restored-folder --flatten
```

### 8\. Cat & Diff

Inspect content without extracting.

```shell
# View a file's content inside the snapshot
vegh cat backup.vegh src/main.rs

# View raw content (Useful for piping binary files)
vegh cat backup.vegh image.png --raw > extracted_image.png

# Compare snapshot with a directory
vegh diff backup.vegh ./current-project
```

### 9\. Send

Send the snapshot to a remote server. Supports **Chunked Uploads** for reliability.

```shell
# Auto-detects if chunking is needed, or force it:
vegh send backup.vegh --force-chunk
```

### 10\. Doctor

Check your environment and installation health.

```shell
vegh doctor
```

### 11\. Hooks example

Create a `.veghhooks.json` in your workspace.

```json
{
  "pre": ["echo 'Checking...'", "ruff check -e"],
  "post": ["echo 'Clean up...'"]
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
