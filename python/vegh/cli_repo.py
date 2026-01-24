import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from .cli_helpers import REPO_CACHE_DIR, load_config, console


def ensure_repo(
    url: str, branch: Optional[str] = None, offline_flag: bool = False
) -> Tuple[Path, str]:
    """
    Ensures a git repo is cached and up-to-date.
    Returns (Path to cached repo, Friendly Name).
    """
    if not shutil.which("git"):
        console.print("[bold red]Error:[/bold red] Git is not installed.")
        import typer

        raise typer.Exit(1)

    # 1. Prepare Cache Directory
    if not REPO_CACHE_DIR.exists():
        REPO_CACHE_DIR.mkdir(parents=True)

    # 2. Check Global Config for "Always Offline" preference
    cfg = load_config()
    always_offline = cfg.get("repo_offline", False)
    is_offline = offline_flag or always_offline

    # 3. Identify Repo (Hash URL to avoid filesystem issues)
    repo_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
    repo_path = REPO_CACHE_DIR / repo_hash
    friendly_name = url.split("/")[-1].replace(".git", "")

    # 4. Smart Sync
    if is_offline and repo_path.exists():
        reason = "CLI Flag" if offline_flag else "Global Config"
        console.print(
            f"[bold yellow]⚡ Using cached {friendly_name} (Offline Mode: {reason})[/bold yellow]"
        )
        return repo_path, friendly_name

    action = "Cloning" if not repo_path.exists() else "Updating"
    console.print(f"[bold blue]{action} {friendly_name}...[/bold blue]")

    try:
        if not repo_path.exists():
            cmd = ["git", "clone", "--depth", "1"]
            if branch:
                cmd.extend(["-b", branch])
            cmd.extend([url, str(repo_path)])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, cmd, result.stdout, result.stderr
                )
        else:
            # Update existing repo
            os.chdir(repo_path)
            cmd = ["git", "pull", "--depth", "1"]
            if branch:
                cmd.extend(["origin", branch])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, cmd, result.stdout, result.stderr
                )

    except subprocess.TimeoutExpired:
        console.print(
            "[bold red]⏳ Timeout![/bold red] Repository operation took too long."
        )
        import typer

        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else str(e)
        console.print(f"[bold red]✘ Git Error:[/bold red] {err}")
        if repo_path.exists():
            console.print("[dim]Removing failed repo cache...[/dim]")
            shutil.rmtree(repo_path)
        import typer

        raise typer.Exit(1)

    console.print(f"[green]✓ {friendly_name} ready.[/green]")
    return repo_path, friendly_name
