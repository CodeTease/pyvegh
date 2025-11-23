import typer
import time
import json
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

try:
    from ._core import create_snap, restore_snap, check_integrity, list_files, get_metadata
except ImportError:
    print("Error: Rust core missing. Run 'maturin develop'!")
    exit(1)

app = typer.Typer(
    name="vegh",
    help="🥬 Vegh (Python Edition) - Snapshot Tool",
    add_completion=False,
    no_args_is_help=True 
)
console = Console()

@app.command()
def snap(
    path: Path = typer.Argument(..., help="Source directory"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    level: int = typer.Option(3, "--level", "-l", help="Compression level (1-21)"),
    comment: Optional[str] = typer.Option(None, "--comment", "-c", help="Add metadata comment"),
    include: Optional[List[str]] = typer.Option(None, "--include", "-i", help="Force include files"),
    exclude: Optional[List[str]] = typer.Option(None, "--exclude", "-e", help="Exclude files"),
):
    """Create a snapshot with options."""
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
            # Pass include/exclude to Rust
            c = create_snap(str(path), str(output_path), level, comment, include, exclude)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    dur = time.time() - start
    console.print(f"[green]Done![/green] Packed [b]{c}[/b] files in [b]{dur:.2f}s[/b]")

@app.command()
def restore(
    file: Path = typer.Argument(..., help=".snap file"),
    out_dir: Path = typer.Argument(Path("."), help="Dest dir"),
):
    """Restore a snapshot."""
    if not file.exists():
        console.print("[red]File not found.[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Restoring[/cyan] [b]{file}[/b]...")
    try:
        restore_snap(str(file), str(out_dir))
        console.print("[green]Success![/green]")
    except Exception as e:
        console.print(f"[red]Restore failed:[/red] {e}")

@app.command("list")
def list_cmd(
    file: Path = typer.Argument(..., help=".snap file"),
):
    """List files inside a snapshot."""
    try:
        files = list_files(str(file))
        table = Table(title=f"Contents of {file.name}")
        table.add_column("File Path", style="cyan")
        
        for f in files:
            if f == ".vegh.json":
                table.add_row(f"[dim]{f} (Meta)[/dim]")
            else:
                table.add_row(f)
        
        console.print(table)
    except Exception as e:
        console.print(f"[red]List failed:[/red] {e}")

@app.command()
def check(file: Path = typer.Argument(..., help=".snap file")):
    """Verify integrity and show metadata."""
    if not file.exists():
        console.print(f"[red]File '{file}' not found.[/red]")
        raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("[bold cyan]Verifying...[/bold cyan]"), transient=True) as p:
        p.add_task("verifying", total=None)
        try:
            # 1. Check SHA256
            h = check_integrity(str(file))
            
            # 2. Get Metadata
            raw_meta = get_metadata(str(file))
            meta = json.loads(raw_meta)
            
            # 3. Display
            console.print(f"[green]✔ Integrity Verified![/green]")
            console.print(f"  SHA256: [dim]{h}[/dim]\n")
            
            # Format timestamp
            ts = meta.get("timestamp", 0)
            date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

            grid = Table.grid(padding=1)
            grid.add_column(style="bold cyan", justify="right")
            grid.add_column(style="white")

            grid.add_row("Author:", meta.get("author", "Unknown"))
            grid.add_row("Created:", date_str)
            grid.add_row("Tool:", meta.get("tool_version", "Unknown"))
            if meta.get("comment"):
                grid.add_row("Comment:", f"[italic]{meta['comment']}[/italic]")

            console.print(Panel(grid, title=f"[bold]📦 Snapshot Metadata ({file.name})[/bold]", border_style="cyan"))

        except Exception as e:
            console.print(f"[red]Check Failed:[/red] {e}")

@app.command()
def send(
    file: Path = typer.Argument(..., help="The file to send"),
    url: str = typer.Argument(..., help="The target URL"),
    auth: Optional[str] = typer.Option(None, "--auth", help="Bearer token"),
):
    """Send a snapshot to a remote server"""
    if not file.exists():
        console.print(f"[bold red]Error:[/bold red] File '{file}' not found.")
        raise typer.Exit(code=1)

    file_size = file.stat().st_size
    size_mb = file_size / (1024 * 1024)
    console.print(f"[cyan]Target:[/cyan] {url}")
    console.print(f"[cyan]File:[/cyan] {file.name} ([bold]{size_mb:.2f} MB[/bold])")
    
    headers = {}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
        console.print(f"[green]Authentication:[/green] Enabled")

    console.print("[yellow]Mode:[/yellow] Direct Upload")
    
    try:
        with open(file, 'rb') as f:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                progress.add_task(description="Uploading...", total=None)
                response = requests.post(url, data=f, headers=headers)
                
        if response.status_code in range(200, 300):
            console.print("[bold green]Transfer complete![/bold green]")
            if response.text:
                console.print(f"[blue]Server Response:[/blue]\n[dim]{response.text}[/dim]")
        else:
            console.print(f"[bold red]Upload failed:[/bold red] Status {response.status_code}")
    except Exception as e:
         console.print(f"[bold red]Network Error:[/bold red] {e}")

if __name__ == "__main__":
    app()