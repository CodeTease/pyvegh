import typer
import time
import json
import requests
import math
import re
import os
import sys 
import subprocess 
import shutil
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.tree import Tree
from rich.prompt import Prompt, Confirm

# Import core functionality
try:
    from ._core import create_snap, dry_run_snap, restore_snap, check_integrity, list_files, get_metadata, count_locs, scan_locs_dir, cat_file, list_files_details
except ImportError:
    print("Error: Rust core missing. Run 'maturin develop'!")
    exit(1)

# Import new Analytics module
try:
    from .analytics import render_dashboard
except ImportError:
    render_dashboard = None

app = typer.Typer(
    name="vegh",
    help="Vegh (Python Edition) - The Snapshot Tool",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich"
)

# Sub-app for configuration commands
config_app = typer.Typer(help="Manage configuration settings (Server, Repo behavior, etc.)")
app.add_typer(config_app, name="config")

console = Console()

# --- PATH CONSTANTS (The "Feng Shui" Update) ---
VEGH_ROOT = Path.home() / ".vegh"
CONFIG_FILE = VEGH_ROOT / "config.json"
CACHE_ROOT = VEGH_ROOT / "cache"
REPO_CACHE_DIR = CACHE_ROOT / "repos" 
HOOKS_FILE = ".veghhooks.json"

# Constants
CHUNK_THRESHOLD = 100 * 1024 * 1024  # 100MB
CHUNK_SIZE = 10 * 1024 * 1024        # 10MB
CONCURRENT_WORKERS = 4
LARGE_FILE_THRESHOLD = 50 * 1024 * 1024 # 50MB warn for dry-run
SENSITIVE_PATTERNS = [
    r"\.env(\..+)?$", 
    r".*id_rsa.*", 
    r".*\.pem$", 
    r".*\.key$", 
    r"credentials\.json",
    r"secrets\..*"
]

# --- Helper Functions ---

def load_config() -> Dict:
    """Load configuration from ~/.vegh/config.json"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except:
            return {}
    return {}

def save_config(config: Dict):
    """Save configuration to ~/.vegh/config.json"""
    if not VEGH_ROOT.exists():
        VEGH_ROOT.mkdir(parents=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def get_dir_size(path: Path) -> int:
    """Calculate total size of a directory."""
    total = 0
    try:
        for entry in path.rglob('*'):
            if entry.is_file():
                total += entry.stat().st_size
    except:
        pass
    return total

def build_tree(path_list: List[str], root_name: str) -> Tree:
    tree = Tree(f"[bold cyan][ROOT] {root_name}[/bold cyan]")
    folder_map = {"": tree}

    for path in sorted(path_list):
        parts = Path(path).parts
        parent_path = ""
        for i, part in enumerate(parts):
            current_path = os.path.join(parent_path, part)
            is_file = (i == len(parts) - 1)
            
            if parent_path not in folder_map: parent_node = tree 
            else: parent_node = folder_map[parent_path]

            if current_path not in folder_map:
                if is_file:
                    if part == ".vegh.json": parent_node.add(f"[dim]{part} (Meta)[/dim]")
                    else: parent_node.add(f"[green]{part}[/green]")
                else:
                    new_branch = parent_node.add(f"[bold blue]+ {part}[/bold blue]")
                    folder_map[current_path] = new_branch
            parent_path = current_path
    return tree

def check_sensitive(path: str) -> bool:
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, path, re.IGNORECASE):
            return True
    return False

# --- REPO MANAGEMENT (The Vault Logic) ---

def ensure_repo(url: str, branch: Optional[str] = None, offline_flag: bool = False) -> Tuple[Path, str]:
    """
    Ensures a git repo is cached and up-to-date.
    Returns (Path to cached repo, Friendly Name).
    """
    if not shutil.which("git"):
        console.print("[bold red]Error:[/bold red] Git is not installed.")
        raise typer.Exit(1)

    # 1. Prepare Cache Directory
    if not REPO_CACHE_DIR.exists():
        REPO_CACHE_DIR.mkdir(parents=True)

    # 2. Check Global Config for "Always Offline" preference
    cfg = load_config()
    always_offline = cfg.get("repo_offline", False)
    
    # Effective offline mode: either CLI flag OR global config says so
    is_offline = offline_flag or always_offline

    # 3. Identify Repo (Hash URL to avoid filesystem issues)
    repo_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
    repo_path = REPO_CACHE_DIR / repo_hash
    friendly_name = url.split("/")[-1].replace(".git", "")
    
    # 4. Smart Sync
    # If offline mode active AND cache exists -> Skip network
    if is_offline and repo_path.exists():
        reason = "CLI Flag" if offline_flag else "Global Config"
        console.print(f"[bold yellow]⚡ Using cached {friendly_name} (Offline Mode: {reason})[/bold yellow]")
        return repo_path, friendly_name

    action = "Cloning" if not repo_path.exists() else "Updating"
    
    try:
        if not repo_path.exists():
            # A. First Clone (Shallow) - Network is mandatory here
            if is_offline:
                 console.print("[dim]Cache miss. Connecting to network to clone...[/dim]")

            console.print(f"[bold cyan]🚀 {action} {friendly_name} (fresh cache)...[/bold cyan]")
            cmd = ["git", "clone", "--depth", "1", "--single-branch"]
            if branch:
                cmd.extend(["--branch", branch])
            cmd.extend([url, str(repo_path)])
            
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
            
        else:
            # B. Update Existing (Fetch + Reset)
            console.print(f"[bold cyan]🔄 {action} {friendly_name} (checking remote)...[/bold cyan]")
            
            # Safety: Ensure remote URL matches
            subprocess.run(["git", "remote", "set-url", "origin", url], cwd=repo_path, check=True, stderr=subprocess.PIPE)
            
            # Fetch latest delta
            fetch_cmd = ["git", "fetch", "--depth", "1", "origin"]
            if branch:
                fetch_cmd.append(branch)
            subprocess.run(fetch_cmd, cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
            
            # Reset to match remote
            target_ref = f"origin/{branch}" if branch else "origin/HEAD"
            
            if branch:
                subprocess.run(["git", "reset", "--hard", target_ref], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            else:
                subprocess.run(["git", "pull", "--rebase"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            # Cleanup artifacts
            subprocess.run(["git", "clean", "-fdx"], cwd=repo_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    except subprocess.TimeoutExpired:
        console.print("[bold red]⏳ Timeout![/bold red] Repository operation took too long.")
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode().strip() if e.stderr else str(e)
        console.print(f"[bold red]✘ Git Error:[/bold red] {err}")
        if repo_path.exists():
            console.print("[yellow]Tip: Run 'vegh clean' if the cache is corrupted.[/yellow]")
        raise typer.Exit(1)

    return repo_path, friendly_name

# --- HOOKS SYSTEM ---

def load_hooks(project_path: Path) -> Dict[str, List[str]]:
    hook_path = project_path / HOOKS_FILE
    if hook_path.exists():
        try:
            data = json.loads(hook_path.read_text(encoding='utf-8'))
            return data.get("hooks", {})
        except Exception as e:
            console.print(f"[yellow][WARN] Failed to parse {HOOKS_FILE}: {e}[/yellow]")
    return {}

def execute_hooks(commands: List[str], hook_name: str) -> bool:
    if not commands: return True
    console.print(f"[bold magenta]>>> HOOK: {hook_name}[/bold magenta]")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    for cmd in commands:
        console.print(f"  [dim]$ {cmd}[/dim]")
        final_cmd = f"chcp 65001 >NUL && {cmd}" if os.name == 'nt' else cmd
        try:
            sys.stdout.flush()
            result = subprocess.run(final_cmd, shell=True, capture_output=False, env=env)
            if result.returncode != 0:
                console.print(f"\n[bold red][ERR] Failed code {result.returncode}[/bold red]")
                return False
        except Exception as e:
            console.print(f"\n[bold red][ERR] Error:[/bold red] {e}")
            return False
    console.print(f"[green][OK] {hook_name} passed.[/green]")
    return True

# --- CONFIG COMMANDS (New Structure) ---

@config_app.command("send")
def config_send(
    url: Optional[str] = typer.Option(None, help="Set default upload URL."),
    auth: Optional[str] = typer.Option(None, help="Set default auth token."),
):
    """Configure Server/Upload settings."""
    cfg = load_config()
    
    console.print("[bold cyan]📡 Server Configuration[/bold cyan]")
    if not url and not auth:
        cfg['url'] = Prompt.ask("Default Server URL", default=cfg.get('url', ''))
        cfg['auth'] = Prompt.ask("Default Auth Token", default=cfg.get('auth', ''), password=True)
    else:
        if url: cfg['url'] = url
        if auth: cfg['auth'] = auth
    
    save_config(cfg)
    console.print(f"[green][OK] Settings saved to {CONFIG_FILE}[/green]")

@config_app.command("repo")
def config_repo(
    offline: Optional[bool] = typer.Option(None, "--offline/--online", help="Set default offline mode."),
):
    """Configure Git Repository behavior."""
    cfg = load_config()
    
    console.print("[bold cyan]📦 Repository Cache Configuration[/bold cyan]")
    
    # If flag is not provided, ask interactively
    if offline is None:
        current_setting = cfg.get("repo_offline", False)
        # Fun prompt
        offline = Confirm.ask(
            "Always run in Offline Mode if cache exists? (Saves bandwidth)", 
            default=current_setting
        )
    
    cfg["repo_offline"] = offline
    save_config(cfg)
    
    status = "OFFLINE (Fast)" if offline else "ONLINE (Fresh)"
    console.print(f"[green][OK] Repo default mode set to: [bold]{status}[/bold][/green]")

@config_app.command("list")
def config_list():
    """List current configuration."""
    cfg = load_config()
    console.print_json(data=cfg)

@config_app.command("reset")
def config_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Reset configuration to defaults."""
    if not force:
        if not Confirm.ask("Are you sure you want to reset all configuration?"):
            raise typer.Abort()
    
    save_config({})
    console.print("[green]Configuration reset.[/green]")

# --- MAIN COMMANDS ---

@app.command()
def snap(
    path: Optional[Path] = typer.Argument(None, help="Source directory (Required unless --repo used)"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Snapshot a remote Git repo"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="Branch for remote repo"),
    offline: bool = typer.Option(False, "--offline", help="Force offline mode (overrides config)"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    level: int = typer.Option(3, "--level", "-l", help="Compression level (1-21)"),
    comment: Optional[str] = typer.Option(None, "--comment", "-c", help="Metadata comment"),
    include: Optional[List[str]] = typer.Option(None, "--include", "-i", help="Include patterns"),
    exclude: Optional[List[str]] = typer.Option(None, "--exclude", "-e", help="Exclude patterns"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate only"),
    skip_hooks: bool = typer.Option(False, "--skip-hooks", help="Bypass hooks"),
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
        console.print(f"[yellow][DRY-RUN] Simulating snapshot for [b]{friendly_name}[/b]...[/yellow]")
        try:
            results: List[Tuple[str, int]] = dry_run_snap(str(source_path), include, exclude)
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
    
    console.print(f"[cyan]Packing[/cyan] [b]{friendly_name}[/b] -> [b]{output_path}[/b]")
    start = time.time()
    
    with console.status("[bold cyan]Compressing...[/bold cyan]", spinner="dots"):
        try:
            count = create_snap(str(source_path), str(output_path), level, comment, include, exclude)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    dur = time.time() - start
    size = output_path.stat().st_size
    grid = Table.grid(padding=1)
    grid.add_column(justify="right", style="cyan")
    grid.add_column(style="white")
    grid.add_row("Files:", f"[bold]{count:,}[/bold]")
    grid.add_row("Size:", format_bytes(size))
    grid.add_row("Time:", f"{dur:.2f}s")
    console.print(Panel(grid, title="[bold green]Snapshot Created[/bold green]", border_style="green", expand=False))

    if not skip_hooks:
        if not execute_hooks(hooks.get("post"), "post"):
            console.print("[yellow][WARN] Post-snap hooks error.[/yellow]")

@app.command()
def restore(
    file: Path = typer.Argument(..., help=".vegh file"),
    out_dir: Path = typer.Argument(Path("."), help="Dest dir"),
    path: Optional[List[str]] = typer.Option(None, "--path", "-p", help="Partial restore"),
    flatten: bool = typer.Option(False, "--flatten", help="Flatten directory structure"),
):
    """Restore a snapshot."""
    if not file.exists():
        console.print("[red]File not found.[/red]")
        raise typer.Exit(1)
    with console.status("[bold cyan]Restoring...[/bold cyan]", spinner="dots"):
        try: restore_snap(str(file), str(out_dir), path, flatten)
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
            content_str = bytes(content_bytes).decode('utf-8')
            from rich.syntax import Syntax
            ext = Path(target).suffix.lstrip(".") or "txt"
            console.print(Syntax(content_str, ext, theme="monokai", line_numbers=True))
        except UnicodeDecodeError:
            console.print(f"[yellow]Binary content detected ({len(content_bytes)} bytes).[/yellow]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

@app.command()
def diff(
    file: Optional[Path] = typer.Argument(None, help=".vegh file (Optional if using --repo)"),
    target: Path = typer.Argument(Path("."), help="Local directory OR .vegh file to compare against"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Use remote repo as Source instead of .vegh file"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="Branch for remote repo"),
    offline: bool = typer.Option(False, "--offline", help="Force offline mode (overrides config)"),
):
    """Compare snapshot OR remote repo with a local directory OR another snapshot."""
    if not target.exists():
        console.print(f"[red]Target '{target}' not found.[/red]")
        raise typer.Exit(1)

    snap_map = {}
    source_name = "Unknown"
    target_is_snap = target.suffix == ".vegh"

    with console.status("[bold cyan]Preparing Comparison...[/bold cyan]", spinner="dots"):
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
                snap_map = {Path(p).as_posix(): s for p, s in snap_files if p != ".vegh.json"}
            else:
                console.print("[red]Must specify either a .vegh file OR --repo <url>.[/red]")
                raise typer.Exit(1)

            if target_is_snap:
                target_files = list_files_details(str(target))
                local_files = {Path(p).as_posix(): s for p, s in target_files if p != ".vegh.json"}
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
                table.add_row(path, "[yellow]MODIFIED[/yellow]", f"Size: {format_bytes(snap_map[path])} -> {format_bytes(local_files[path])}")
                changes = True
        elif in_src and not in_loc:
            msg = "In Source, missing in Target" if target_is_snap else "In Source, missing locally"
            table.add_row(path, "[red]DELETED[/red]", msg)
            changes = True
        elif not in_src and in_loc:
            msg = "In Target, missing in Source" if target_is_snap else "On Disk, missing in source"
            table.add_row(path, "[green]NEW[/green]", msg)
            changes = True

    if changes: console.print(table)
    else: console.print("[bold green]No changes detected (Sync).[/bold green]")

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
        config_exts = {".env", ".json", ".yaml", ".yml", ".toml", ".conf", ".ini", ".xml"}
        secret_keywords = ["PASSWORD", "SECRET_KEY", "TOKEN", "API_KEY", "ACCESS_KEY", "PRIVATE_KEY"]
        
        for path in files:
            p = Path(path)
            if p.suffix in config_exts:
                try:
                    # Limit content read size if needed, but cat_file reads whole file.
                    # Assuming config files are small.
                    content_bytes = cat_file(str(file), path)
                    try:
                        content = content_bytes.decode('utf-8')
                        for keyword in secret_keywords:
                            if keyword in content:
                                risks.append((path, "Content Match", f"Found keyword: {keyword}"))
                                break # Report once per file
                    except UnicodeDecodeError:
                        pass # Skip binary files
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
        
        console.print(f"Repo Cache: [bold]{repo_count}[/bold] repos ([{color}]{size_str}[/{color}])")
        console.print(f"Cache Location: [dim]{REPO_CACHE_DIR}[/dim]")
        if total_size > 5 * 1024 * 1024 * 1024:
            console.print("[yellow]WARN: Cache is large. Run 'vegh clean' to free space.[/yellow]")
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
        
    console.print(f"[bold cyan]Exploring {file.name}. Type 'help' for commands.[/bold cyan]")
    
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
            if not parts: continue
            
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
  exit        Exit explorer
""")
            elif cmd == "pwd":
                console.print(current_path)
            
            elif cmd == "ls":
                target_path = current_path
                if args:
                    # simplistic path resolution
                    arg_path = args[0]
                    if arg_path.startswith("/"): target_path = arg_path
                    else: target_path = (Path(current_path) / arg_path).as_posix()
                    
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
                    
                    p_abs = "/" + p # Treat stored paths as relative to root, map to absolute
                    
                    if p_abs.startswith(prefix):
                        found_any = True
                        rel = p_abs[len(prefix):]
                        if "/" in rel:
                            items.add(rel.split("/")[0] + "/") # Directory
                        else:
                            items.add(rel) # File

                if not found_any and target_path != "/":
                     # Check if it is a file?
                     # Actually, if not found_any, maybe dir doesn't exist
                     pass

                # Sort: Dirs first
                sorted_items = sorted(list(items), key=lambda x: (not x.endswith("/"), x))
                
                grid = Table.grid(padding=1)
                for item in sorted_items:
                    if item.endswith("/"):
                         grid.add_row(f"[bold blue]{item}[/bold blue]")
                    else:
                         grid.add_row(f"[green]{item}[/green]")
                console.print(grid)

            elif cmd == "cd":
                if not args: continue
                new_dir = args[0]
                
                if new_dir == "..":
                    current_path = str(Path(current_path).parent.as_posix())
                    if current_path == ".": current_path = "/"
                elif new_dir == "/":
                    current_path = "/"
                else:
                    # Construct target
                    if new_dir.startswith("/"): target = new_dir
                    else: target = (Path(current_path) / new_dir).as_posix()
                    
                    if target != "/" and target.endswith("/"): target = target.rstrip("/")

                    # Validate existence (is it a directory prefix?)
                    prefix = target + "/"
                    is_valid = any(("/"+f).startswith(prefix) for f in all_files)
                    
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
                    if current_path == "/": full_path = fname
                    else: full_path = (Path(current_path) / fname).as_posix().lstrip("/")

                if full_path in all_files:
                     # Call existing cat logic
                     try:
                        content_bytes = cat_file(str(file), full_path)
                        try:
                            content_str = bytes(content_bytes).decode('utf-8')
                            console.print(content_str)
                        except UnicodeDecodeError:
                            console.print(f"[yellow]Binary content ({len(content_bytes)} bytes)[/yellow]")
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
            console.print(f"[green]Successfully cleared cache at {REPO_CACHE_DIR}[/green]")
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
            for f in sorted(files): table.add_row(f)
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
            console.print(Panel(grid, title=f"[bold green][OK] Valid ({file.name})[/bold green]", border_style="green"))
        except Exception as e:
            console.print(f"[bold red]Verification Failed:[/bold red] {e}")
            raise typer.Exit(1)

@app.command()
def loc(
    target: Optional[str] = typer.Argument(None, help="File, Dir, or Git URL"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Git Repo URL"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="Branch/Tag"),
    offline: bool = typer.Option(False, "--offline", help="Force offline mode (overrides config)"),
    raw: bool = typer.Option(False, "--raw", help="Raw list view")
):
    """Visualize Lines of Code (Analytics)."""
    input_target = repo or target
    if not input_target:
        console.print("[red]Provide file/dir or use --repo.[/red]")
        raise typer.Exit(1)

    is_remote = input_target.startswith(("http://", "https://", "git@")) or repo is not None
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

        with console.status(f"[cyan]Analyzing {display_name}...[/cyan]", spinner="dots"):
            if scan_path.is_dir():
                results = scan_locs_dir(str(scan_path))
            else:
                results = count_locs(str(scan_path))
                
        if render_dashboard and not raw:
            render_dashboard(console, display_name, results)
        else:
            total = sum(c for _, c in results)
            table = Table(title=f"LOC: {display_name}")
            table.add_column("LOC", style="green", footer=f"{total:,}")
            table.add_column("Path", style="cyan")
            for p, c in sorted(results, key=lambda x: x[1], reverse=True):
                if c > 0: table.add_row(f"{c:,}", p)
            console.print(table)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

def _upload_chunk(url, file_path, start, chunk_size, index, total, filename, headers):
    try:
        with open(file_path, 'rb') as f:
            f.seek(start)
            data = f.read(chunk_size)
        h = headers.copy()
        h.update({"X-File-Name": filename, "X-Chunk-Index": str(index), "X-Total-Chunks": str(total)})
        resp = requests.post(url, data=data, headers=h)
        if not (200 <= resp.status_code < 300): raise Exception(f"Status {resp.status_code}")
        return True
    except Exception as e: raise Exception(f"Chunk {index}: {e}")

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
    target = url or cfg.get('url')
    token = auth or cfg.get('auth')
    if not target:
        console.print("[red]No URL configured.[/red]")
        raise typer.Exit(1)

    size = file.stat().st_size
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    
    console.print(f"Target: {target} | Size: {format_bytes(size)}")

    if size < CHUNK_THRESHOLD and not force_chunk:
        try:
            with open(file, 'rb') as f:
                with console.status("Uploading...", spinner="dots"):
                    r = requests.post(target, data=f, headers=headers)
            if 200 <= r.status_code < 300: console.print("[green]Success![/green]")
            else: console.print(f"[red]Failed: {r.status_code}[/red]")
        except Exception as e: console.print(f"[red]Error: {e}[/red]")
    else:
        chunks = math.ceil(size / CHUNK_SIZE)
        with console.status(f"Sending {chunks} chunks...", spinner="dots") as s:
            done = 0
            with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as ex:
                fs = []
                for i in range(chunks):
                    start = i * CHUNK_SIZE
                    curr = min(CHUNK_SIZE, size - start)
                    fs.append(ex.submit(_upload_chunk, target, file, start, curr, i, chunks, file.name, headers))
                for f in as_completed(fs):
                    try:
                        f.result()
                        done += 1
                        s.update(f"Sending... ({done}/{chunks})")
                    except Exception as e:
                        console.print(f"[red]Aborted: {e}[/red]")
                        raise typer.Exit(1)
        console.print("[green]Success![/green]")

if __name__ == "__main__":
    app()