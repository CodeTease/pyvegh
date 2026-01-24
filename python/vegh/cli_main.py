from typing import Optional
from rich.console import Console

import typer

# Try to import package version metadata (Modern Pythonic way)
try:
    from importlib.metadata import version as get_package_version, PackageNotFoundError
except ImportError:
    # Fallback for older environments or odd setups
    get_package_version = None
    PackageNotFoundError = Exception

# Import core functionality
try:
    from ._core import (
        create_snap,
        dry_run_snap,
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
except ImportError:
    print("Error: Rust core missing. Run 'maturin develop'!")
    exit(1)

# Import Analytics module
try:
    from .analytics import (
        render_dashboard,
        scan_sloc,
        calculate_sloc,
        count_sloc_from_text,
    )
except ImportError:
    render_dashboard = None
    scan_sloc = None
    calculate_sloc = None

# Define context settings to enable '-h' alongside '--help'
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    name="vegh",
    help="Vegh (Python Edition) - The Snapshot Tool",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings=CONTEXT_SETTINGS,  # Enable -h flag
)

console = Console()


def version_callback(value: bool):
    """
    Callback function to handle version flags (-v, --version).
    It fetches the installed package version or falls back to 'dev'.
    """
    if value:
        try:
            v = get_package_version("vegh")
        except PackageNotFoundError:
            v = "dev"
        console.print(f"PyVegh CLI Version: [bold green]{v}[/bold green]")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,  # Process this before other commands
        help="Show the application version and exit.",
    ),
):
    """
    Vegh: The lightning-fast snapshot and analytics tool.
    """
    pass


# Add sub-apps
from .cli_config import config_app

app.add_typer(config_app, name="config")
