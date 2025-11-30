import typer
import time
import json
import requests
import math
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.tree import Tree
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TransferSpeedColumn, TimeElapsedColumn
from rich.prompt import Prompt

# Import core functionality
try:
    from ._core import create_snap, dry_run_snap, restore_snap, check_integrity, list_files, get_metadata, count_locs
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
    help="🥬 Vegh (Python Edition) - The CodeTease Snapshot Tool",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich"
)
console = Console()

# Configuration Path
CONFIG_FILE = Path.home() / ".vegh_config.json"

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
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except:
            return {}
    return {}

def save_config(config: Dict):
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def build_tree(path_list: List[str], root_name: str) -> Tree:
    tree = Tree(f"[bold cyan]📦 {root_name}[/bold cyan]")
    folder_map = {"": tree}

    for path in sorted(path_list):
        parts = Path(path).parts
        parent_path = ""
        
        for i, part in enumerate(parts):
            current_path = os.path.join(parent_path, part)
            is_file = (i == len(parts) - 1)
            
            if parent_path not in folder_map:
                parent_node = tree 
            else:
                parent_node = folder_map[parent_path]

            if current_path not in folder_map:
                if is_file:
                    if part == ".vegh.json":
                        parent_node.add(f"[dim]{part} (Meta)[/dim]")
                    else:
                        parent_node.add(f"[green]{part}[/green]")
                else:
                    new_branch = parent_node.add(f"[bold blue]📂 {part}[/bold blue]")
                    folder_map[current_path] = new_branch
            
            parent_path = current_path
            
    return tree

def check_sensitive(path: str) -> bool:
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, path, re.IGNORECASE):
            return True
    return False

# --- Commands ---

@app.command()
def config(
    url: Optional[str] = typer.Option(None, help="Set the default upload URL."),
    auth: Optional[str] = typer.Option(None, help="Set the default authentication token."),
):
    """
    ⚙️  Configure default settings.

    Run without arguments to start [bold]Interactive Mode[/bold].
    Settings are saved to [dim]~/.vegh_config.json[/dim].
    """
    cfg = load_config()
    
    if not url and not auth:
        console.print("[bold]Interactive Configuration[/bold]")
        cfg['url'] = Prompt.ask("Default Server URL", default=cfg.get('url', ''))
        cfg['auth'] = Prompt.ask("Default Auth Token", default=cfg.get('auth', ''), password=True)
    else:
        if url: cfg['url'] = url
        if auth: cfg['auth'] = auth
    
    save_config(cfg)
    console.print(f"[green]✔ Configuration saved to {CONFIG_FILE}[/green]")

@app.command()
def snap(
    path: Path = typer.Argument(..., help="Source directory"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    level: int = typer.Option(3, "--level", "-l", help="Compression level (1-21)"),
    comment: Optional[str] = typer.Option(None, "--comment", "-c", help="Add metadata comment"),
    include: Optional[List[str]] = typer.Option(None, "--include", "-i", help="Force include files"),
    exclude: Optional[List[str]] = typer.Option(None, "--exclude", "-e", help="Exclude files"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without creating file"),
):
    """📸 Create a snapshot (.snap)"""
    if not path.exists():
        console.print(f"[red]Path '{path}' not found.[/red]")
        raise typer.Exit(1)

    # --- DRY RUN LOGIC ---
    if dry_run:
        console.print(f"[yellow]🚧 Dry-Run Mode:[/yellow] Simulating snapshot for [b]{path}[/b]...")
        try:
            results: List[Tuple[str, int]] = dry_run_snap(str(path), include, exclude)
        except Exception as e:
             console.print(f"[red]Simulation failed:[/red] {e}")
             raise typer.Exit(1)
        
        total_files = len(results)
        total_size = sum(size for _, size in results)
        warnings = []

        tree = Tree(f"[bold yellow]🔍 Simulation: {path.name}[/bold yellow]")
        folder_map = {"": tree}

        # Show max 50 items to avoid flooding console in dry-run
        sorted_items = sorted(results, key=lambda x: x[0])
        for i, (f_path, f_size) in enumerate(sorted_items):
            is_sensitive = check_sensitive(f_path)
            is_large = f_size > LARGE_FILE_THRESHOLD
            
            if is_sensitive: warnings.append(f"[red]SENSITIVE FILE DETECTED:[/red] {f_path}")
            if is_large: warnings.append(f"[yellow]LARGE FILE ({format_bytes(f_size)}):[/yellow] {f_path}")

            if i < 50 or is_sensitive or is_large:
                parts = Path(f_path).parts
                parent_path = ""
                for idx, part in enumerate(parts):
                    current_path = os.path.join(parent_path, part)
                    is_file_node = (idx == len(parts) - 1)
                    if parent_path not in folder_map: parent_node = tree 
                    else: parent_node = folder_map[parent_path]
                    if current_path not in folder_map:
                        if is_file_node:
                            label = f"{part} [dim]({format_bytes(f_size)})[/dim]"
                            if is_sensitive: label = f"[bold red]⛔ {label}[/bold red]"
                            elif is_large: label = f"[bold yellow]⚠️ {label}[/bold yellow]"
                            else: label = f"[green]{label}[/green]"
                            parent_node.add(label)
                        else:
                            new_branch = parent_node.add(f"[bold blue]📂 {part}[/bold blue]")
                            folder_map[current_path] = new_branch
                    parent_path = current_path
        
        if total_files > 50: tree.add(f"[dim]... and {total_files - 50} more files[/dim]")

        console.print(tree)
        console.print()

        grid = Table.grid(padding=1)
        grid.add_column(justify="right", style="cyan")
        grid.add_column(style="white")
        grid.add_row("Total Files:", f"[bold]{total_files:,}[/bold]")
        grid.add_row("Total Uncompressed:", format_bytes(total_size))
        
        console.print(Panel(grid, title="[bold blue]Dry-Run Summary[/bold blue]", border_style="blue", expand=False))

        if warnings: console.print(Panel("\n".join(warnings), title="[bold red]Risk Assessment[/bold red]", border_style="red"))
        else: console.print("[bold green]✔ No obvious issues detected.[/bold green]")
        return 
    
    # --- REAL SNAP LOGIC ---
    folder_name = path.name or "backup"
    output_path = output or Path(f"{folder_name}.snap")
    console.print(f"[cyan]Packing[/cyan] [b]{path}[/b] -> [b]{output_path}[/b]")
    start = time.time()
    
    # Use simple Spinner (no counting overhead) for maximum speed
    with console.status("[bold cyan]Compressing...[/bold cyan]", spinner="dots"):
        try:
            # Note: No callback passed anymore
            count = create_snap(str(path), str(output_path), level, comment, include, exclude)
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

@app.command()
def restore(
    file: Path = typer.Argument(..., help=".snap file"),
    out_dir: Path = typer.Argument(Path("."), help="Dest dir"),
):
    """📦 Restore a snapshot."""
    if not file.exists():
        console.print("[red]File not found.[/red]")
        raise typer.Exit(1)
    # Using Status/Spinner instead of Progress bar here too
    with console.status("[bold cyan]Restoring...[/bold cyan]", spinner="dots"):
        try: restore_snap(str(file), str(out_dir))
        except Exception as e:
            console.print(f"[red]Restore failed:[/red] {e}")
            raise typer.Exit(1)
    console.print(f"[green]✔ Successfully restored to[/green] [bold]{out_dir}[/bold]")

@app.command("list")
def list_cmd(
    file: Path = typer.Argument(..., help=".snap file"),
    tree_view: bool = typer.Option(True, "--tree/--flat", help="Show as tree or flat list"),
):
    """📜 List contents (supports Tree view)."""
    try:
        files = list_files(str(file))
        if not files:
            console.print("[yellow]Empty snapshot.[/yellow]")
            return
        if tree_view:
            tree = build_tree(files, file.name)
            console.print(tree)
        else:
            table = Table(title=f"Contents of {file.name}")
            table.add_column("File Path", style="cyan")
            for f in sorted(files): table.add_row(f)
            console.print(table)
    except Exception as e:
        console.print(f"[red]List failed:[/red] {e}")

@app.command()
def check(file: Path = typer.Argument(..., help=".snap file")):
    """✅ Verify integrity & metadata."""
    if not file.exists():
        console.print(f"[red]File '{file}' not found.[/red]")
        raise typer.Exit(1)
    with console.status("[bold cyan]Verifying Integrity...[/bold cyan]", spinner="dots"):
        try:
            h = check_integrity(str(file))
            raw_meta = get_metadata(str(file))
            meta = json.loads(raw_meta)
            ts = meta.get("timestamp", 0)
            date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            
            grid = Table.grid(padding=1)
            grid.add_column(style="bold cyan", justify="right")
            grid.add_column(style="white")
            
            # Updated to match Blake3 and Format V2
            grid.add_row("Blake3:", f"[dim]{h}[/dim]")
            grid.add_row("Author:", meta.get("author", "Unknown"))
            grid.add_row("Created:", date_str)
            
            # Separate Tool Version vs File Format Version
            grid.add_row("Vegh Ver:", meta.get("tool_version", "Unknown"))
            grid.add_row("Format:", f"[bold]v{meta.get('format_version', '1')}[/bold]")
            
            if meta.get("comment"): grid.add_row("Comment:", f"[italic]{meta['comment']}[/italic]")
            console.print(Panel(grid, title=f"[bold green]✔ Valid Snapshot ({file.name})[/bold green]", border_style="green"))
        except Exception as e:
            console.print(f"[bold red]❌ Verification Failed:[/bold red] {e}")
            raise typer.Exit(1)

@app.command()
def loc(
    file: Path = typer.Argument(..., help=".snap file"),
    raw: bool = typer.Option(False, "--raw", help="Show raw list instead of dashboard")
):
    """📊 Visualize Lines of Code (Analytics)."""
    if not file.exists():
        console.print(f"[red]File '{file}' not found.[/red]")
        raise typer.Exit(1)

    with console.status("[cyan]Crunching numbers...[/cyan]", spinner="dots"):
        try:
            results = count_locs(str(file))
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
            
    # Try using Dashboard first
    if render_dashboard and not raw:
        render_dashboard(console, file.name, results)
    else:
        # --- IMPROVED FALLBACK / RAW VIEW ---
        total_loc = sum(count for _, count in results)
        
        # TABLE FIX: Ensure Numbers are NEVER truncated
        table = Table(title=f"LOC Analysis: {file.name}", show_footer=True, expand=True)
        
        # Column 1: LOC - Protected!
        table.add_column(
            "LOC", 
            style="bold green", 
            justify="right", 
            footer=f"[bold green]{total_loc:,}[/bold green]",
            no_wrap=True,      # Absolutely no wrapping
            min_width=10       # Reserve space for millions
        )
        
        # Column 2: Path - Sacrificial!
        table.add_column(
            "File Path", 
            style="cyan", 
            overflow="ellipsis", 
            no_wrap=True,
            ratio=1            # Take whatever space is left
        )
        
        sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
        for path_str, loc_count in sorted_results:
            if loc_count == 0: 
                table.add_row("[dim]0[/dim]", f"[dim]{path_str}[/dim]")
            else: 
                table.add_row(f"{loc_count:,}", path_str)
        
        console.print(table)
        
        if not raw and not render_dashboard:
            console.print("[dim italic]Note: Dashboard module (vegh.analytics) not found. Showing list view.[/dim italic]")

def _upload_chunk(url: str, file_path: Path, start: int, chunk_size: int, index: int, total_chunks: int, filename: str, headers: dict):
    try:
        with open(file_path, 'rb') as f:
            f.seek(start)
            data = f.read(chunk_size)
        
        chunk_headers = headers.copy()
        chunk_headers.update({
            "X-File-Name": filename,
            "X-Chunk-Index": str(index),
            "X-Total-Chunks": str(total_chunks)
        })
        
        resp = requests.post(url, data=data, headers=chunk_headers)
        if not (200 <= resp.status_code < 300):
            raise Exception(f"Status {resp.status_code}")
        return True
    except Exception as e:
        raise Exception(f"Chunk {index} error: {e}")

@app.command()
def send(
    file: Path = typer.Argument(..., help="The file to send"),
    url: Optional[str] = typer.Option(None, help="Target URL (overrides config)"),
    force_chunk: bool = typer.Option(False, "--force-chunk", help="Force chunked upload"),
    auth: Optional[str] = typer.Option(None, "--auth", help="Bearer token (overrides config)"),
):
    """🚀 Send snapshot to server."""
    if not file.exists():
        console.print(f"[bold red]Error:[/bold red] File '{file}' not found.")
        raise typer.Exit(1)

    cfg = load_config()
    target_url = url or cfg.get('url')
    auth_token = auth or cfg.get('auth')

    if not target_url:
        console.print("[red]No URL specified.[/red] Use [bold]--url[/bold] or run [bold]vegh config[/bold].")
        raise typer.Exit(1)

    file_size = file.stat().st_size
    filename = file.name

    console.print(f"[cyan]Target:[/cyan] {target_url}")
    console.print(f"[cyan]File:[/cyan]   {filename} ([bold]{format_bytes(file_size)}[/bold])")
    
    base_headers = {}
    if auth_token:
        base_headers["Authorization"] = f"Bearer {auth_token}"
        console.print(f"[green]Auth:[/green]   Enabled")

    if file_size < CHUNK_THRESHOLD and not force_chunk:
        console.print("[yellow]Mode:[/yellow]   Direct Upload")
        _send_direct(file, target_url, base_headers)
    else:
        console.print("[yellow]Mode:[/yellow]   Concurrent Chunked Upload")
        _send_chunked(file, target_url, file_size, filename, base_headers)

def _send_direct(file: Path, url: str, headers: dict):
    try:
        with open(file, 'rb') as f:
            # Replaced Progress with Status Spinner
            with console.status("[bold cyan]Uploading...[/bold cyan]", spinner="dots"):
                response = requests.post(url, data=f, headers=headers)
                
        if response.status_code in range(200, 300):
            console.print("[bold green]✔ Upload complete![/bold green]")
            if response.text:
                console.print(Panel(response.text, title="Server Response", border_style="blue"))
        else:
            console.print(f"[bold red]Upload failed:[/bold red] Status {response.status_code}")
    except Exception as e:
         console.print(f"[bold red]Network Error:[/bold red] {e}")

def _send_chunked(file: Path, url: str, file_size: int, filename: str, headers: dict):
    total_chunks = math.ceil(file_size / CHUNK_SIZE)
    
    # Progress Bar is still somewhat useful for uploads (Network IO is slow anyway)
    # But since we want to delete it, I'll switch to a Spinner that says "Uploading X Chunks..."
    # Actually, for network, knowing percentage IS useful, but let's stick to the spirit of "Turbo Mode".
    # I'll keep the progress bar ONLY for Upload (since it's network bound, not CPU bound like Rust), 
    # OR replace it if you want consistency.
    
    with console.status(f"[bold cyan]Uploading {total_chunks} chunks...[/bold cyan]", spinner="dots") as status:
        completed = 0
        with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
            futures = []
            for i in range(total_chunks):
                start = i * CHUNK_SIZE
                current_size = min(CHUNK_SIZE, file_size - start)
                futures.append(executor.submit(_upload_chunk, url, file, start, current_size, i, total_chunks, filename, headers))
            
            for future in as_completed(futures):
                try:
                    future.result()
                    completed += 1
                    status.update(f"[bold cyan]Uploading... ({completed}/{total_chunks})[/bold cyan]")
                except Exception as e:
                    console.print(f"[red]Upload Aborted:[/red] {e}")
                    raise typer.Exit(1)

    console.print("[bold green]✔ All chunks sent successfully![/bold green]")

if __name__ == "__main__":
    app()