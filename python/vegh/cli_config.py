import typer
from typing import Optional
from rich.prompt import Prompt, Confirm

from .cli_helpers import load_config, save_config, console, CONFIG_FILE

# Sub-app for configuration commands
config_app = typer.Typer(
    help="Manage configuration settings (Server, Repo behavior, etc.)",
    context_settings={"help_option_names": ["-h", "--help"]},  # Enable -h flag
)


@config_app.command("send")
def config_send(
    url: Optional[str] = typer.Option(None, help="Set default upload URL."),
    auth: Optional[str] = typer.Option(None, help="Set default auth token."),
):
    """Configure Server/Upload settings."""
    cfg = load_config()

    console.print("[bold cyan]📡 Server Configuration[/bold cyan]")
    if not url and not auth:
        cfg["url"] = Prompt.ask("Default Server URL", default=cfg.get("url", ""))
        cfg["auth"] = Prompt.ask(
            "Default Auth Token", default=cfg.get("auth", ""), password=True
        )
    else:
        if url:
            cfg["url"] = url
        if auth:
            cfg["auth"] = auth

    save_config(cfg)
    console.print(f"[green][OK] Settings saved to {CONFIG_FILE}[/green]")


@config_app.command("repo")
def config_repo(
    offline: Optional[bool] = typer.Option(
        None, "--offline/--online", help="Set default offline mode."
    ),
):
    """Configure Git Repository behavior."""
    cfg = load_config()
    console.print("[bold cyan]📦 Repository Cache Configuration[/bold cyan]")

    if offline is None:
        current_setting = cfg.get("repo_offline", False)
        offline = Confirm.ask(
            "Always run in Offline Mode if cache exists? (Saves bandwidth)",
            default=current_setting,
        )

    cfg["repo_offline"] = offline
    save_config(cfg)

    status = "OFFLINE (Fast)" if offline else "ONLINE (Fresh)"
    console.print(
        f"[green][OK] Repo default mode set to: [bold]{status}[/bold][/green]"
    )


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
