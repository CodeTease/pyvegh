import time
import typer
import json
import requests
import math
import re
import sys
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, List, Tuple
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from .cli_main import app, create_snap, dry_run_snap
from .cli_helpers import (
    console,
    format_bytes,
    load_config,
    get_dir_size,
    build_tree,
    _copy_to_clipboard_native,
    CONFIG_FILE,
    REPO_CACHE_DIR,
    CHUNK_THRESHOLD,
    CHUNK_SIZE,
    CONCURRENT_WORKERS,
    SENSITIVE_PATTERNS,
    NOISE_PATTERNS,
)
from .cli_repo import ensure_repo
from .cli_hooks import load_hooks, execute_hooks
from ._core import (
    restore_snap,
    check_integrity,
    list_files,
    get_metadata,
    count_locs,
    scan_locs_dir,
    cat_file,
    list_files_details,
    get_context_xml,
    search_snap,
    read_snapshot_text,
)
from .analytics import render_dashboard, scan_sloc, calculate_sloc, count_sloc_from_text


@app.command()
def prune(
    target_dir: Path = typer.Argument(
        Path("."), help="Directory to scan for snapshots"
    ),
    keep: int = typer.Option(
        5, "--keep", "-k", help="Number of recent snapshots to keep"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clean up old snapshots, keeping only the most recent ones."""
    if not target_dir.exists():
        console.print(f"[red]Directory '{target_dir}' not found.[/red]")
        raise typer.Exit(1)

    snapshots = sorted(
        target_dir.glob("*.vegh"), key=lambda f: f.stat().st_mtime, reverse=True
    )

    if len(snapshots) <= keep:
        console.print(
            f"[green]No cleanup needed. Found {len(snapshots)} snapshots (Keep: {keep}).[/green]"
        )
        return

    keep_list = snapshots[:keep]
    delete_list = snapshots[keep:]

    console.print(
        f"[bold cyan]Found {len(snapshots)} snapshots. Keeping {len(keep_list)} most recent.[/bold cyan]"
    )

    table = Table(title="Snapshots to Delete")
    table.add_column("File", style="red")
    table.add_column("Size", style="yellow")
    table.add_column("Modified", style="dim")

    total_size = 0
    for s in delete_list:
        size = s.stat().st_size
        total_size += size
        mtime = datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        table.add_row(s.name, format_bytes(size), mtime)

    console.print(table)
    console.print(
        f"[bold]Total space to free:[/bold] [green]{format_bytes(total_size)}[/green]"
    )

    if not force:
        if not Confirm.ask(
            f"Are you sure you want to delete {len(delete_list)} snapshots?"
        ):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Abort()

    with console.status("[red]Pruning...[/red]", spinner="bouncingBall"):
        deleted_count = 0
        for s in delete_list:
            try:
                s.unlink()
                console.print(f"[dim]Deleted: {s.name}[/dim]")
                deleted_count += 1
            except Exception as e:
                console.print(f"[red]Failed to delete {s.name}: {e}[/red]")

    console.print(
        f"[green]Pruned {deleted_count} snapshots. Freed {format_bytes(total_size)}.[/green]"
    )


@app.command()
def snap(
    path: Optional[Path] = typer.Argument(
        None, help="Source directory (Required unless --repo used)"
    ),
    repo: Optional[str] = typer.Option(
        None, "--repo", help="Snapshot a remote Git repo"
    ),
    branch: Optional[str] = typer.Option(
        None, "--branch", "-b", help="Branch for remote repo"
    ),
    offline: bool = typer.Option(
        False, "--offline", help="Force offline mode (overrides config)"
    ),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    level: int = typer.Option(3, "--level", "-l", help="Compression level (1-21)"),
    comment: Optional[str] = typer.Option(
        None, "--comment", "-c", help="Metadata comment"
    ),
    include: Optional[List[str]] = typer.Option(
        None, "--include", "-i", help="Include patterns"
    ),
    exclude: Optional[List[str]] = typer.Option(
        None, "--exclude", "-e", help="Exclude patterns"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate only"),
    skip_hooks: bool = typer.Option(False, "--skip-hooks", help="Bypass hooks"),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Use minimal output (hides progress bar)"
    ),
):
    """Create a snapshot (.vegh) from local folder OR remote repo."""

    # 1. Resolve Source
    if repo:
        source_path, friendly_name = ensure_repo(repo, branch, offline)
    else:
        if not path:
            console.print("[red]Missing argument 'PATH'. Or use --repo <url>.[/red]")
            raise typer.Exit(1)
        if not path.exists():
            console.print(f"[red]Path '{path}' not found.[/red]")
            raise typer.Exit(1)
        source_path = path
        friendly_name = path.name

    hooks = load_hooks(source_path)

    # --- DRY RUN ---
    if dry_run:
        console.print(
            f"[yellow][DRY-RUN] Simulating snapshot for [b]{friendly_name}[/b]...[/yellow]"
        )
        try:
            results: List[Tuple[str, int]] = dry_run_snap(
                str(source_path), include, exclude
            )
        except Exception as e:
            console.print(f"[red]Simulation failed:[/red] {e}")
            raise typer.Exit(1)

        total_files = len(results)
        total_size = sum(size for _, size in results)

        console.print(f"Files: [bold]{total_files:,}[/bold]")
        console.print(f"Size:  [bold]{format_bytes(total_size)}[/bold] (uncompressed)")
        console.print("[bold green][OK] Simulation complete.[/bold green]")
        return

    # --- REAL SNAP ---
    if not skip_hooks:
        if not execute_hooks(hooks.get("pre"), "pre"):
            console.print("[bold red][ABORT] Pre-snap hooks failed.[/bold red]")
            raise typer.Exit(1)

    folder_name = friendly_name or "backup"
    output_path = output or Path(f"{folder_name}.vegh")

    console.print(
        f"[cyan]Packing[/cyan] [b]{friendly_name}[/b] -> [b]{output_path}[/b]"
    )
    start = time.time()

    # LOGIC UPDATE: Switch between Rust UI (default) and Python Spinner (quiet)
    try:
        if not quiet:
            # Default: Let Rust handle the UI with indicatif (No Python spinner to interfere)
            count = create_snap(
                str(source_path),
                str(output_path),
                level,
                comment,
                include,
                exclude,
                no_cache=offline,
                verbose=True,  # Enable Rust Progress Bar
            )
        else:
            # Quiet mode: Use Python spinner (Rust verbose=False)
            with console.status("[cyan]Packing...[/cyan]", spinner="dots"):
                count = create_snap(
                    str(source_path),
                    str(output_path),
                    level,
                    comment,
                    include,
                    exclude,
                    no_cache=offline,
                    verbose=False,  # Disable Rust Progress Bar
                )
    except Exception as e:
        console.print(f"[red]Snapshot failed:[/red] {e}")
        raise typer.Exit(1)

    elapsed = time.time() - start
    size = output_path.stat().st_size

    console.print(
        f"[bold green]✓ Created {output_path} ({format_bytes(size)}) in {elapsed:.1f}s[/bold green]"
    )
    console.print(f"[dim]Files: {count:,}[/dim]")

    if not skip_hooks:
        execute_hooks(hooks.get("post"), "post")


@app.command()
def restore(
    file: Path = typer.Argument(..., help=".vegh file"),
    out_dir: Path = typer.Argument(Path("."), help="Dest dir"),
    path: Optional[List[str]] = typer.Option(
        None, "--path", "-p", help="Partial restore"
    ),
    flatten: bool = typer.Option(
        False, "--flatten", help="Flatten directory structure"
    ),
):
    """Restore a snapshot."""
    if not file.exists():
        console.print("[red]File not found.[/red]")
        raise typer.Exit(1)
    with console.status("[bold cyan]Restoring...[/bold cyan]", spinner="dots"):
        try:
            restore_snap(str(file), str(out_dir), path, flatten)
        except Exception as e:
            console.print(f"[red]Restore failed:[/red] {e}")
            raise typer.Exit(1)
    console.print(f"[green][OK] Restored to[/green] [bold]{out_dir}[/bold]")


@app.command()
def cat(
    file: Path = typer.Argument(..., help=".vegh file"),
    target: str = typer.Argument(..., help="Path inside snapshot"),
    raw: bool = typer.Option(False, "--raw", help="Print raw content to stdout"),
):
    """View content of a file in the snapshot."""
    if not file.exists():
        console.print(f"[red]File '{file}' not found.[/red]")
        raise typer.Exit(1)
    try:
        content_bytes = cat_file(str(file), target)
        if raw:
            sys.stdout.buffer.write(bytes(content_bytes))
            sys.stdout.buffer.flush()
            return

        try:
            content_str = bytes(content_bytes).decode("utf-8")
            from rich.syntax import Syntax

            ext = Path(target).suffix.lstrip(".") or "txt"
            console.print(Syntax(content_str, ext, theme="monokai", line_numbers=True))
        except UnicodeDecodeError:
            console.print(
                f"[yellow]Binary content detected ({len(content_bytes)} bytes).[/yellow]"
            )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def diff(
    file: Optional[Path] = typer.Argument(
        None, help=".vegh file (Optional if using --repo)"
    ),
    target: Path = typer.Argument(
        Path("."), help="Local directory OR .vegh file to compare against"
    ),
    repo: Optional[str] = typer.Option(
        None, "--repo", help="Use remote repo as Source instead of .vegh file"
    ),
    branch: Optional[str] = typer.Option(
        None, "--branch", "-b", help="Branch for remote repo"
    ),
    offline: bool = typer.Option(
        False, "--offline", help="Force offline mode (overrides config)"
    ),
):
    """Compare snapshot OR remote repo with a local directory OR another snapshot."""
    if not target.exists():
        console.print(f"[red]Target '{target}' not found.[/red]")
        raise typer.Exit(1)

    snap_map = {}
    source_name = "Unknown"
    target_is_snap = target.suffix == ".vegh"

    with console.status(
        "[bold cyan]Preparing Comparison...[/bold cyan]", spinner="dots"
    ):
        try:
            if repo:
                repo_path, source_name = ensure_repo(repo, branch, offline)
                source_name = f"Repo: {source_name}"
                snap_list = dry_run_snap(str(repo_path))
                snap_map = {Path(p).as_posix(): s for p, s in snap_list}
            elif file:
                if not file.exists():
                    console.print(f"[red]File '{file}' not found.[/red]")
                    raise typer.Exit(1)
                source_name = f"Snap: {file.name}"
                snap_files = list_files_details(str(file))
                snap_map = {
                    Path(p).as_posix(): s for p, s in snap_files if p != ".vegh.json"
                }
            else:
                console.print(
                    "[red]Must specify either a .vegh file OR --repo <url>.[/red]"
                )
                raise typer.Exit(1)

            if target_is_snap:
                target_files = list_files_details(str(target))
                local_files = {
                    Path(p).as_posix(): s for p, s in target_files if p != ".vegh.json"
                }
            else:
                local_list = dry_run_snap(str(target))
                local_files = {Path(p).as_posix(): s for p, s in local_list}
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    all_paths = set(snap_map.keys()) | set(local_files.keys())
    table = Table(title=f"Diff: {source_name} vs {target}")
    table.add_column("File Path", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    changes = False
    for path in sorted(all_paths):
        in_src = path in snap_map
        in_loc = path in local_files

        if in_src and in_loc:
            if snap_map[path] != local_files[path]:
                table.add_row(
                    path,
                    "[yellow]MODIFIED[/yellow]",
                    f"Size: {format_bytes(snap_map[path])} -> {format_bytes(local_files[path])}",
                )
                changes = True
        elif in_src and not in_loc:
            msg = (
                "In Source, missing in Target"
                if target_is_snap
                else "In Source, missing locally"
            )
            table.add_row(path, "[red]DELETED[/red]", msg)
            changes = True
        elif not in_src and in_loc:
            msg = (
                "In Target, missing in Source"
                if target_is_snap
                else "On Disk, missing in source"
            )
            table.add_row(path, "[green]NEW[/green]", msg)
            changes = True

    if changes:
        console.print(table)
    else:
        console.print("[bold green]No changes detected (Sync).[/bold green]")


@app.command()
def audit(
    file: Path = typer.Argument(..., help=".vegh file to audit"),
):
    """Scan snapshot for sensitive data and security risks."""
    if not file.exists():
        console.print(f"[red]File '{file}' not found.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]Auditing {file.name}...[/bold cyan]")

    risks = []

    try:
        files = list_files(str(file))

        # 1. Filename Scan
        for path in files:
            for pattern in SENSITIVE_PATTERNS:
                if re.search(pattern, path, re.IGNORECASE):
                    risks.append((path, "Filename Match", f"Pattern: {pattern}"))

        # 2. Content Scan (Config files only)
        # Scan for common secrets inside textual config files
        config_exts = {
            ".env",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".conf",
            ".ini",
            ".xml",
        }
        secret_keywords = [
            "PASSWORD",
            "SECRET_KEY",
            "TOKEN",
            "API_KEY",
            "ACCESS_KEY",
            "PRIVATE_KEY",
        ]

        for path in files:
            p = Path(path)
            if p.suffix in config_exts:
                try:
                    # Limit content read size if needed.
                    content_bytes = cat_file(str(file), path)
                    try:
                        content = bytes(content_bytes).decode("utf-8")
                        for keyword in secret_keywords:
                            if keyword in content:
                                risks.append(
                                    (path, "Content Match", f"Found keyword: {keyword}")
                                )
                                break  # Report once per file
                    except UnicodeDecodeError:
                        pass  # Skip binary files
                except Exception:
                    pass

        if not risks:
            console.print("[bold green]No security risks found.[/bold green]")
        else:
            table = Table(title=f"Security Audit: {file.name}")
            table.add_column("File Path", style="red")
            table.add_column("Type", style="yellow")
            table.add_column("Detail", style="dim")

            for path, type_, detail in risks:
                table.add_row(path, type_, detail)

            console.print(table)
            console.print(f"\n[bold red]Found {len(risks)} potential risks.[/bold red]")

    except Exception as e:
        console.print(f"[red]Audit failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def doctor(
    file: Optional[Path] = typer.Argument(None, help="Optional: .vegh file to check"),
):
    """Check environment health and cache status."""
    console.print("[bold cyan]Vegh Doctor[/bold cyan]")

    py_ver = sys.version.split()[0]
    console.print(f"Python Version: [green]{py_ver}[/green]")

    # Check new config file
    if CONFIG_FILE.exists():
        console.print(f"Config: [green]Found[/green] ({CONFIG_FILE})")
    else:
        console.print("Config: [dim]Not configured[/dim]")

    try:
        from . import _core

        console.print("Rust Core: [green]Loaded[/green]")
    except ImportError:
        console.print("Rust Core: [red]MISSING[/red]")

    # Updated Cache Check
    if REPO_CACHE_DIR.exists():
        repo_count = len([x for x in REPO_CACHE_DIR.iterdir() if x.is_dir()])
        total_size = get_dir_size(REPO_CACHE_DIR)
        size_str = format_bytes(total_size)
        color = "green" if total_size < 5 * 1024 * 1024 * 1024 else "yellow"

        console.print(
            f"Repo Cache: [bold]{repo_count}[/bold] repos ([{color}]{size_str}[/{color}])"
        )
        console.print(f"Cache Location: [dim]{REPO_CACHE_DIR}[/dim]")
        if total_size > 5 * 1024 * 1024 * 1024:
            console.print(
                "[yellow]WARN: Cache is large. Run 'vegh clean' to free space.[/yellow]"
            )
    else:
        console.print("Repo Cache: [dim]Empty[/dim]")

    if file:
        console.print(f"\n[bold cyan]Checking Snapshot: {file.name}[/bold cyan]")
        if file.exists():
            try:
                check_integrity(str(file))
                console.print("Integrity: [green]OK[/green]")
            except Exception as e:
                console.print(f"Integrity: [bold red]CORRUPT ({e})[/bold red]")
        else:
            console.print("[red]File not found![/red]")

    console.print("\n[bold green]System seems healthy![/bold green]")


@app.command()
def explore(file: Path = typer.Argument(..., help=".vegh file to explore")):
    """Interactive Explorer for .vegh files."""
    if not file.exists():
        console.print(f"[red]File '{file}' not found.[/red]")
        raise typer.Exit(1)

    console.print(
        f"[bold cyan]Exploring {file.name}. Type 'help' for commands.[/bold cyan]"
    )

    try:
        # Load file structure once
        raw_files = list_files(str(file))
        # Ensure paths are consistently posix
        all_files = sorted([Path(p).as_posix() for p in raw_files])
    except Exception as e:
        console.print(f"[red]Failed to load snapshot:[/red] {e}")
        raise typer.Exit(1)

    current_path = "/"

    while True:
        try:
            cmd_input = Prompt.ask(f"[bold green]vegh:{current_path}>[/bold green]")
            parts = cmd_input.split()
            if not parts:
                continue

            cmd = parts[0]
            args = parts[1:]

            if cmd in ("exit", "quit"):
                break
            elif cmd == "clear":
                console.clear()
            elif cmd == "help":
                console.print("""
[bold]Available Commands:[/bold]
  ls [dir]    List files
  cd <dir>    Change directory
  cat <file>  View file content
  pwd         Show current path
  clear       Clear screen
  grep <text> [-i]   Search text in files (-i for case-insensitive)
  exit        Exit explorer
""")
            elif cmd == "pwd":
                console.print(current_path)

            elif cmd == "grep":
                if not args:
                    console.print("[red]Usage: grep <text> [-i][/red]")
                    continue

                # Simple argument parsing for -i flag
                case_sensitive = True
                search_text = args[0]

                # Handle cases like: grep -i "foo" OR grep "foo" -i
                if len(args) > 1:
                    if args[0] == "-i":
                        case_sensitive = False
                        search_text = args[1]
                    elif args[1] == "-i":
                        case_sensitive = False
                        search_text = args[0]

                # Determine search scope based on current directory in explore
                # If root, search everything. If subdir, filter by prefix.
                search_prefix = current_path if current_path != "/" else ""

                # Remove leading slash to match tar paths (e.g., "src/main.rs" not "/src/main.rs")
                if search_prefix.startswith("/"):
                    search_prefix = search_prefix[1:]

                with console.status(f"[cyan]Searching '{search_text}'...[/cyan]"):
                    try:
                        # Call Rust Core (Zero extra dep!)
                        matches = search_snap(
                            str(file), search_text, search_prefix, case_sensitive
                        )

                        if not matches:
                            console.print("[yellow]No matches found.[/yellow]")
                        else:
                            # Render results nicely
                            table = Table(box=None, show_header=False)
                            table.add_column("Location", style="cyan")
                            table.add_column("Content", style="white")

                            current_file_group = ""
                            for fpath, line_num, content in matches:
                                # Group output by file for cleaner look
                                if fpath != current_file_group:
                                    table.add_row(
                                        f"\n[bold green]{fpath}[/bold green]", ""
                                    )
                                    current_file_group = fpath

                                # Strip whitespace for cleaner display
                                table.add_row(
                                    f"  :{line_num}", f"[dim]{content.strip()}[/dim]"
                                )

                            console.print(table)
                            console.print(
                                f"\n[bold]Found {len(matches)} matches.[/bold]"
                            )

                    except Exception as e:
                        console.print(f"[red]Grep error:[/red] {e}")

            elif cmd == "ls":
                target_path = current_path
                if args:
                    # simplistic path resolution
                    arg_path = args[0]
                    if arg_path.startswith("/"):
                        target_path = arg_path
                    else:
                        target_path = (Path(current_path) / arg_path).as_posix()

                    # Normalize: /src/ -> /src
                    if target_path != "/" and target_path.endswith("/"):
                        target_path = target_path.rstrip("/")

                # Filter items in this directory
                items = set()
                prefix = target_path if target_path == "/" else target_path + "/"

                found_any = False
                for p in all_files:
                    # p is like "src/main.rs"
                    # if target is "/", we want "src"
                    # if target is "/src", we want "main.rs"

                    p_abs = (
                        "/" + p
                    )  # Treat stored paths as relative to root, map to absolute

                    if p_abs.startswith(prefix):
                        found_any = True
                        rel = p_abs[len(prefix) :]
                        if "/" in rel:
                            items.add(rel.split("/")[0] + "/")  # Directory
                        else:
                            items.add(rel)  # File

                if not found_any and target_path != "/":
                    pass

                # Sort: Dirs first
                sorted_items = sorted(
                    list(items), key=lambda x: (not x.endswith("/"), x)
                )

                grid = Table.grid(padding=1)
                for item in sorted_items:
                    if item.endswith("/"):
                        grid.add_row(f"[bold blue]{item}[/bold blue]")
                    else:
                        grid.add_row(f"[green]{item}[/green]")
                console.print(grid)

            elif cmd == "cd":
                if not args:
                    continue
                new_dir = args[0]

                if new_dir == "..":
                    current_path = str(Path(current_path).parent.as_posix())
                    if current_path == ".":
                        current_path = "/"
                elif new_dir == "/":
                    current_path = "/"
                else:
                    # Construct target
                    if new_dir.startswith("/"):
                        target = new_dir
                    else:
                        target = (Path(current_path) / new_dir).as_posix()

                    if target != "/" and target.endswith("/"):
                        target = target.rstrip("/")

                    # Validate existence (is it a directory prefix?)
                    prefix = target + "/"
                    is_valid = any(("/" + f).startswith(prefix) for f in all_files)

                    if is_valid or target == "/":
                        current_path = target
                    else:
                        console.print(f"[red]Directory not found: {new_dir}[/red]")

            elif cmd == "cat":
                if not args:
                    console.print("[red]Usage: cat <file>[/red]")
                    continue

                fname = args[0]
                # Resolve path
                if fname.startswith("/"):
                    full_path = fname.lstrip("/")
                else:
                    if current_path == "/":
                        full_path = fname
                    else:
                        full_path = (Path(current_path) / fname).as_posix().lstrip("/")

                if full_path in all_files:
                    # Call existing cat logic
                    try:
                        content_bytes = cat_file(str(file), full_path)
                        try:
                            content_str = bytes(content_bytes).decode("utf-8")
                            console.print(content_str)
                        except UnicodeDecodeError:
                            console.print(
                                f"[yellow]Binary content ({len(content_bytes)} bytes)[/yellow]"
                            )
                    except Exception as e:
                        console.print(f"[red]Error:[/red] {e}")
                else:
                    console.print(f"[red]File not found: {fname}[/red]")

            else:
                console.print(f"[red]Unknown command: {cmd}[/red]")

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")


@app.command()
def clean():
    """Clean up the repository cache."""
    if not REPO_CACHE_DIR.exists():
        console.print("[yellow]Cache is already empty.[/yellow]")
        return

    confirm = typer.confirm(f"Delete all cached repos in {REPO_CACHE_DIR}?")
    if not confirm:
        raise typer.Abort()

    with console.status("[red]Cleaning cache...[/red]", spinner="bouncingBall"):
        try:
            shutil.rmtree(REPO_CACHE_DIR)
            console.print(
                f"[green]Successfully cleared cache at {REPO_CACHE_DIR}[/green]"
            )
        except Exception as e:
            console.print(f"[red]Failed to clean cache:[/red] {e}")


@app.command("list")
def list_cmd(
    file: Path = typer.Argument(..., help=".vegh file"),
    tree_view: bool = typer.Option(True, "--tree/--flat", help="View format"),
):
    """List snapshot contents."""
    try:
        files = list_files(str(file))
        if not files:
            console.print("[yellow]Empty snapshot.[/yellow]")
            return
        if tree_view:
            console.print(build_tree(files, file.name))
        else:
            table = Table(title=f"Contents of {file.name}")
            table.add_column("File Path", style="cyan")
            for f in sorted(files):
                table.add_row(f)
            console.print(table)
    except Exception as e:
        console.print(f"[red]List failed:[/red] {e}")


@app.command()
def check(file: Path = typer.Argument(..., help=".vegh file")):
    """Verify integrity & metadata."""
    if not file.exists():
        console.print("[red]File not found.[/red]")
        raise typer.Exit(1)
    with console.status("[bold cyan]Verifying...[/bold cyan]", spinner="dots"):
        try:
            h = check_integrity(str(file))
            raw_meta = get_metadata(str(file))
            meta = json.loads(raw_meta)

            grid = Table.grid(padding=1)
            grid.add_column(style="bold cyan", justify="right")
            grid.add_column(style="white")
            grid.add_row("Blake3:", f"[dim]{h}[/dim]")
            grid.add_row("Author:", meta.get("author", "Unknown"))
            grid.add_row("Ver:", meta.get("tool_version", "Unknown"))
            console.print(
                Panel(
                    grid,
                    title=f"[bold green][OK] Valid ({file.name})[/bold green]",
                    border_style="green",
                )
            )
        except Exception as e:
            console.print(f"[bold red]Verification Failed:[/bold red] {e}")
            raise typer.Exit(1)


@app.command()
def loc(
    target: Optional[str] = typer.Argument(None, help="File, Dir, or Git URL"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Git Repo URL"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="Branch/Tag"),
    offline: bool = typer.Option(
        False, "--offline", help="Force offline mode (overrides config)"
    ),
    raw: bool = typer.Option(False, "--raw", help="Raw list view"),
    sloc: bool = typer.Option(
        False, "--sloc", help="Count SLOC (Source Lines of Code) instead of LOC"
    ),
):
    """Visualize Lines of Code (Analytics)."""
    input_target = repo or target
    if not input_target:
        console.print("[red]Provide file/dir or use --repo.[/red]")
        raise typer.Exit(1)

    is_remote = (
        input_target.startswith(("http://", "https://", "git@")) or repo is not None
    )
    scan_path: Path = None
    display_name: str = "Unknown"

    try:
        if is_remote:
            scan_path, display_name = ensure_repo(input_target, branch, offline)
        else:
            scan_path = Path(input_target)
            display_name = scan_path.name
            if not scan_path.exists():
                console.print(f"[red]Path '{scan_path}' not found.[/red]")
                raise typer.Exit(1)

        metric_name = "SLOC" if sloc else "LOC"

        with console.status(
            f"[cyan]Analyzing {display_name} ({metric_name})...[/cyan]", spinner="dots"
        ):
            if sloc:
                if scan_path.is_dir():
                    if scan_sloc is None:
                        console.print(
                            "[red]SLOC analysis not available (Import Error).[/red]"
                        )
                        raise typer.Exit(1)
                    results = scan_sloc(str(scan_path))
                    # --- FIX START: Handle SLOC for .vegh snapshot ---
                elif scan_path.is_file() and scan_path.suffix == ".vegh":
                    if count_sloc_from_text is None:
                        console.print("[red]SLOC analysis not available.[/red]")
                        raise typer.Exit(1)

                    # Call the new Rust function to get filename + content
                    try:
                        files_content = read_snapshot_text(str(scan_path))
                        results = []
                        for fname, content in files_content:
                            ext = Path(fname).suffix.lower()
                            cnt = count_sloc_from_text(content, ext)
                            results.append((fname, cnt))
                    except Exception as e:
                        console.print(
                            f"[red]Failed to read snapshot for SLOC: {e}[/red]"
                        )
                        raise typer.Exit(1)
                # --- FIX END ---
                else:
                    if calculate_sloc is None:
                        console.print(
                            "[red]SLOC analysis not available (Import Error).[/red]"
                        )
                        raise typer.Exit(1)
                    # For single file, results should be list of (path, count)
                    cnt = calculate_sloc(str(scan_path))
                    results = [(scan_path.name, cnt)]
            else:
                if scan_path.is_file():
                    # Rust's count_locs expects a .vegh snapshot
                    if scan_path.suffix == ".vegh":
                        results = count_locs(str(scan_path))
                    else:
                        # Plain text file counting in Python
                        try:
                            with open(
                                scan_path, "r", encoding="utf-8", errors="ignore"
                            ) as f:
                                cnt = sum(1 for _ in f)
                            results = [(scan_path.name, cnt)]
                        except Exception:
                            results = [(scan_path.name, 0)]
                elif scan_path.is_dir():
                    results = scan_locs_dir(str(scan_path))
                else:
                    results = []  # Should not happen

        if render_dashboard and not raw:
            render_dashboard(console, display_name, results, metric_name=metric_name)
        else:
            total = sum(c for _, c in results)
            table = Table(title=f"{metric_name}: {display_name}")
            table.add_column(metric_name, style="green", footer=f"{total:,}")
            table.add_column("Path", style="cyan")
            for p, c in sorted(results, key=lambda x: x[1], reverse=True):
                if c > 0:
                    table.add_row(f"{c:,}", p)
            console.print(table)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _upload_chunk(url, file_path, start, chunk_size, index, total, filename, headers):
    try:
        with open(file_path, "rb") as f:
            f.seek(start)
            data = f.read(chunk_size)
        h = headers.copy()
        h.update(
            {
                "X-File-Name": filename,
                "X-Chunk-Index": str(index),
                "X-Total-Chunks": str(total),
            }
        )
        resp = requests.post(url, data=data, headers=h)
        if not (200 <= resp.status_code < 300):
            raise Exception(f"Status {resp.status_code}")
        return True
    except Exception as e:
        raise Exception(f"Chunk {index}: {e}")


@app.command()
def send(
    file: Path = typer.Argument(..., help="File to send"),
    url: Optional[str] = typer.Option(None, help="Target URL"),
    force_chunk: bool = typer.Option(False, "--force-chunk"),
    auth: Optional[str] = typer.Option(None, "--auth"),
):
    """Send snapshot to server."""
    if not file.exists():
        console.print("[red]File not found.[/red]")
        raise typer.Exit(1)
    cfg = load_config()
    target = url or cfg.get("url")
    token = auth or cfg.get("auth")
    if not target:
        console.print("[red]No URL configured.[/red]")
        raise typer.Exit(1)

    size = file.stat().st_size
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    console.print(f"Target: {target} | Size: {format_bytes(size)}")

    if size < CHUNK_THRESHOLD and not force_chunk:
        try:
            with open(file, "rb") as f:
                with console.status("Uploading...", spinner="dots"):
                    r = requests.post(target, data=f, headers=headers)
            if 200 <= r.status_code < 300:
                console.print("[green]Success![/green]")
            else:
                console.print(f"[red]Failed: {r.status_code}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
    else:
        chunks = math.ceil(size / CHUNK_SIZE)
        with console.status(f"Sending {chunks} chunks...", spinner="dots") as s:
            done = 0
            with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as ex:
                fs = []
                for i in range(chunks):
                    start = i * CHUNK_SIZE
                    curr = min(CHUNK_SIZE, size - start)
                    fs.append(
                        ex.submit(
                            _upload_chunk,
                            target,
                            file,
                            start,
                            curr,
                            i,
                            chunks,
                            file.name,
                            headers,
                        )
                    )
                for f in as_completed(fs):
                    try:
                        f.result()
                        done += 1
                        s.update(f"Sending... ({done}/{chunks})")
                    except Exception as e:
                        console.print(f"[red]Aborted: {e}[/red]")
                        raise typer.Exit(1)
        console.print("[green]Success![/green]")


# --- VEGH PROMPT COMMAND ---


@app.command()
def prompt(
    target: Path = typer.Argument(Path("."), help="Target codebase"),
    clean: bool = typer.Option(
        False,
        "--clean",
        help="Remove lock files, binaries, and secrets to save tokens.",
    ),
    exclude: Optional[List[str]] = typer.Option(
        None, "--exclude", "-e", help="Custom patterns to exclude"
    ),
    copy: bool = typer.Option(
        False, "--copy", "-c", help="Copy output to clipboard (No extra deps required)"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save XML to file"
    ),
):
    """
    Generate XML context for LLM.
    """
    if not target.exists():
        console.print(f"[red]Path '{target}' not found.[/red]")
        raise typer.Exit(1)

    # 1. Prepare Exclude Patterns
    final_exclude = []
    if exclude:
        final_exclude.extend(exclude)

    if clean:
        final_exclude.extend(NOISE_PATTERNS)
        console.print(
            "[dim]Clean mode enabled: Ignoring lock files, binaries & secrets.[/dim]"
        )

    # 2. Call Rust Core
    with console.status("[bold cyan]Gathering context...[/bold cyan]"):
        try:
            # Calls the new Rust function
            xml_content = get_context_xml(str(target), exclude=final_exclude)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    # 3. Handle Output
    if output:
        output.write_text(xml_content, encoding="utf-8")
        console.print(f"[green]Saved prompt to {output}[/green]")
    elif copy:
        # Use our Native helper instead of Pyperclip
        success = _copy_to_clipboard_native(xml_content)
        if success:
            console.print(
                f"[green]Copied {len(xml_content)} chars to clipboard![/green]"
            )
        else:
            console.print(
                "[yellow]Clipboard tool not found (pbcopy, clip, xclip/wl-copy missing). Printing to stdout:[/yellow]"
            )
            print(xml_content)
    else:
        # Default: Print to stdout
        print(xml_content)
