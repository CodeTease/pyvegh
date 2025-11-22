import typer
import time
import requests
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

# Import compiled Rust functions
try:
    from ._core import create_snap, restore_snap, check_integrity
except ImportError:
    # Fallback for dev environment if not built yet
    # We use a simple print here to avoid crashing if rich isn't ready
    print("Error: Could not import Rust core. Did you run 'maturin develop'?")
    exit(1)

# Configure Typer to be "calm"
# no_args_is_help=True: Shows help if no command is provided instead of an error
app = typer.Typer(
    name="vegh",
    help="🥬 Vegh (Python Edition) - Tight packing, swift unpacking, no nonsense.",
    add_completion=False,
    no_args_is_help=True 
)
console = Console()

@app.command()
def snap(
    path: Path = typer.Argument(..., help="Path to the source directory"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file name"),
):
    """Create a snapshot (.snap) from a directory"""
    if not path.exists():
        console.print(f"[bold red]Error:[/bold red] Path '{path}' does not exist.")
        raise typer.Exit(code=1)

    folder_name = path.name or "backup"
    output_path = output or Path(f"{folder_name}.snap")

    console.print(f"[cyan]Packing[/cyan] [bold]{path}[/bold] [cyan]into[/cyan] [bold]{output_path}[/bold]...")

    start_time = time.time()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(description="Compressing files...", total=None)
        try:
            count = create_snap(str(path), str(output_path))
        except Exception as e:
            console.print(f"[bold red]Core Error:[/bold red] {e}")
            raise typer.Exit(code=1)

    duration = time.time() - start_time
    console.print(f"[bold green]Done![/bold green] Packed [bold]{count}[/bold] files in [bold]{duration:.2f}s[/bold]")


@app.command()
def restore(
    file: Path = typer.Argument(..., help="The .snap file to restore"),
    out_dir: Path = typer.Argument(Path("."), help="Destination directory"),
):
    """Restore a snapshot (.snap)"""
    if not file.exists():
        console.print(f"[bold red]Error:[/bold red] File '{file}' not found.")
        raise typer.Exit(code=1)

    console.print(f"[cyan]Restoring[/cyan] [bold]{file}[/bold] [cyan]to[/cyan] [bold]{out_dir}[/bold]...")
    
    start_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(description="Unpacking archive...", total=None)
        try:
            restore_snap(str(file), str(out_dir))
        except Exception as e:
            console.print(f"[bold red]Core Error:[/bold red] {e}")
            raise typer.Exit(code=1)

    duration = time.time() - start_time
    console.print(f"[bold green]Done![/bold green] Took [bold]{duration:.2f}s[/bold]")


@app.command()
def check(file: Path = typer.Argument(..., help="The .snap file to verify")):
    """Verify the integrity of a .snap file"""
    if not file.exists():
        console.print(f"[bold red]Error:[/bold red] File '{file}' not found.")
        raise typer.Exit(code=1)

    console.print(f"[yellow]Inspecting[/yellow] [bold]{file}[/bold]...")

    try:
        checksum = check_integrity(str(file))
        console.print(f"[bold green]Integrity Verified![/bold green]")
        console.print(f"SHA256: [dim]{checksum}[/dim]")
    except Exception as e:
        console.print(f"[bold red]Check Error:[/bold red] {e}")
        raise typer.Exit(code=1)


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