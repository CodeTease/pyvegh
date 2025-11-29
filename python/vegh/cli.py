import typer
import time
import json
import requests
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.tree import Tree
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TransferSpeedColumn
from rich.prompt import Prompt

# Import core functionality
try:
    from ._core import create_snap, restore_snap, check_integrity, list_files, get_metadata, count_locs
except ImportError:
    print("Error: Rust core missing. Run 'maturin develop'!")
    exit(1)

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
    """Converts a list of paths into a Rich Tree structure."""
    tree = Tree(f"[bold cyan]📦 {root_name}[/bold cyan]")
    
    # A simpler iterative approach for Tree building
    # Map: folder_path_str -> Tree_Branch
    folder_map = {"": tree}

    # Sort to ensure folders are created before files inside them
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
                    # It's a folder
                    new_branch = parent_node.add(f"[bold blue]📂 {part}[/bold blue]")
                    folder_map[current_path] = new_branch
            
            parent_path = current_path
            
    return tree

# --- Commands ---

@app.command()
def config(
    url: Optional[str] = typer.Option(None, help="Default server URL"),
    auth: Optional[str] = typer.Option(None, help="Default Auth Token"),
):
    """⚙️ Configure default settings."""
    cfg = load_config()
    
    if not url and not auth:
        # Interactive mode
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
):
    """📸 Create a snapshot (.snap)"""
    if not path.exists():
        console.print(f"[red]Path '{path}' not found.[/red]")
        raise typer.Exit(1)

    folder_name = path.name or "backup"
    output_path = output or Path(f"{folder_name}.snap")

    console.print(f"[cyan]Packing[/cyan] [b]{path}[/b] -> [b]{output_path}[/b]")

    start = time.time()
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as p:
        p.add_task("Compressing...", total=None)
        try:
            count = create_snap(str(path), str(output_path), level, comment, include, exclude)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    dur = time.time() - start
    size = output_path.stat().st_size
    
    # Summary Panel
    grid = Table.grid(padding=1)
    grid.add_column(justify="right", style="cyan")
    grid.add_column(style="white")
    grid.add_row("Files:", str(count))
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

    with Progress(SpinnerColumn(), TextColumn("[cyan]Restoring...[/cyan]"), transient=True) as p:
        p.add_task("unpack", total=None)
        try:
            restore_snap(str(file), str(out_dir))
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
            for f in sorted(files):
                table.add_row(f)
            console.print(table)
            
    except Exception as e:
        console.print(f"[red]List failed:[/red] {e}")

@app.command()
def check(file: Path = typer.Argument(..., help=".snap file")):
    """✅ Verify integrity & metadata."""
    if not file.exists():
        console.print(f"[red]File '{file}' not found.[/red]")
        raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("[bold cyan]Verifying...[/bold cyan]"), transient=True) as p:
        p.add_task("verifying", total=None)
        try:
            h = check_integrity(str(file))
            raw_meta = get_metadata(str(file))
            meta = json.loads(raw_meta)
            
            # Metadata Panel
            ts = meta.get("timestamp", 0)
            date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

            grid = Table.grid(padding=1)
            grid.add_column(style="bold cyan", justify="right")
            grid.add_column(style="white")

            grid.add_row("SHA256:", f"[dim]{h}[/dim]")
            grid.add_row("Author:", meta.get("author", "Unknown"))
            grid.add_row("Created:", date_str)
            grid.add_row("Format:", meta.get("tool_version", "Unknown"))
            if meta.get("comment"):
                grid.add_row("Comment:", f"[italic]{meta['comment']}[/italic]")

            console.print(Panel(grid, title=f"[bold green]✔ Valid Snapshot ({file.name})[/bold green]", border_style="green"))

        except Exception as e:
            console.print(f"[bold red]❌ Verification Failed:[/bold red] {e}")
            raise typer.Exit(1)

@app.command()
def loc(file: Path = typer.Argument(..., help=".snap file")):
    """🔢 Count Lines of Code (LOC) inside snapshot."""
    if not file.exists():
        console.print(f"[red]File '{file}' not found.[/red]")
        raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("[cyan]Counting LOC...[/cyan]"), transient=True) as p:
        p.add_task("counting", total=None)
        try:
            results = count_locs(str(file))
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
            
    total_loc = sum(count for _, count in results)
    
    table = Table(title=f"LOC Analysis: {file.name}", show_footer=True)
    table.add_column("File Path", style="cyan", no_wrap=True)
    table.add_column("LOC", style="green", justify="right", footer=f"[bold green]{total_loc:,}[/bold green]")
    
    # Sort by LOC descending to show biggest files first
    sorted_results = sorted(results, key=lambda x: x[1], reverse=True)

    for path_str, loc_count in sorted_results:
        if loc_count == 0:
             table.add_row(f"[dim]{path_str} (Binary/Empty)[/dim]", "[dim]0[/dim]")
        else:
            table.add_row(path_str, f"{loc_count:,}")

    console.print(table)
    
    # Fun summary panel
    console.print(Panel(
        f"[bold]Total LOC:[/bold] [green]{total_loc:,}[/green]\n[dim](Binary/Image files are ignored)[/dim]",
        title="[bold blue]CodeTease Analytics[/bold blue]",
        border_style="blue",
        expand=False
    ))

# Helper for upload
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

    # Load defaults
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
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TransferSpeedColumn(),
                transient=True,
            ) as progress:
                progress.add_task(description="Uploading...", total=None)
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
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total} chunks"),
        transient=False,
    ) as progress:
        task_id = progress.add_task("Uploading...", total=total_chunks)
        
        with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
            futures = []
            for i in range(total_chunks):
                start = i * CHUNK_SIZE
                current_size = min(CHUNK_SIZE, file_size - start)
                futures.append(executor.submit(_upload_chunk, url, file, start, current_size, i, total_chunks, filename, headers))
            
            for future in as_completed(futures):
                try:
                    future.result()
                    progress.advance(task_id, 1)
                except Exception as e:
                    console.print(f"[red]Upload Aborted:[/red] {e}")
                    raise typer.Exit(1)

    console.print("[bold green]✔ All chunks sent successfully![/bold green]")

if __name__ == "__main__":
    app()