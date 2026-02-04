from typing import Optional
from rich.console import Console

import typer

# Add sub-apps
from .cli_config import config_app

# Try to import package version metadata (Modern Pythonic way)
try:
    from importlib.metadata import version as get_package_version, PackageNotFoundError
except ImportError:
    # Fallback for older environments or odd setups
    get_package_version = None
    PackageNotFoundError = Exception

# Import core functionality
try:
    from . import _core  # noqa: F401
except ImportError:
    print("Error: Rust core missing. Run 'maturin develop'!")
    exit(1)

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


app.add_typer(config_app, name="config")
