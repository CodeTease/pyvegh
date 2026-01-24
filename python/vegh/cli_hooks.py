import json
import os
from pathlib import Path
from typing import Dict, List

from .cli_helpers import HOOKS_FILE, console


def load_hooks(project_path: Path) -> Dict[str, List[str]]:
    hook_path = project_path / HOOKS_FILE
    if hook_path.exists():
        try:
            with open(hook_path, "r") as f:
                return json.load(f)
        except Exception as e:
            console.print(f"[yellow]Warning: Failed to load hooks: {e}[/yellow]")
    return {}


def execute_hooks(commands: List[str], hook_name: str) -> bool:
    if not commands:
        return True

    # Just a friendly warning for the unsuspecting user
    console.print(f"[bold yellow]⚠ Running {hook_name} hooks from project config...[/bold yellow]")


    console.print(f"[bold magenta]>>> HOOK: {hook_name}[/bold magenta]")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    for cmd in commands:
        console.print(f"  [dim]$ {cmd}[/dim]")
        # Windows encoding fix
        final_cmd = f"chcp 65001 >NUL && {cmd}" if os.name == "nt" else cmd
        try:
            import subprocess

            result = subprocess.run(
                final_cmd, shell=True, env=env, capture_output=True, text=True
            )
            if result.returncode != 0:
                console.print(f"[red]Hook failed: {result.stderr}[/red]")
                return False
        except Exception as e:
            console.print(f"[red]Hook error: {e}[/red]")
            return False
    console.print(f"[green][OK] {hook_name} passed.[/green]")
    return True
