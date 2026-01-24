import json
from .jsonc import parse
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from rich.console import Console

console = Console()

# Load built-in config
CONFIG_FILE_PATH = Path(__file__).parent / "config.jsonc"
with open(CONFIG_FILE_PATH, "r") as f:
    CONFIG = parse(f.read())

# Constants from config
CHUNK_THRESHOLD = CONFIG["CHUNK_THRESHOLD"]
CHUNK_SIZE = CONFIG["CHUNK_SIZE"]
CONCURRENT_WORKERS = CONFIG["CONCURRENT_WORKERS"]
SENSITIVE_PATTERNS = CONFIG["SENSITIVE_PATTERNS"]
NOISE_PATTERNS = CONFIG["NOISE_PATTERNS"]

# --- PATH CONSTANTS ---
VEGH_ROOT = Path.home() / ".vegh"
CONFIG_FILE = VEGH_ROOT / "config.json"
CACHE_ROOT = VEGH_ROOT / "cache"
REPO_CACHE_DIR = CACHE_ROOT / "repos"
HOOKS_FILE = ".veghhooks.json"


def load_config() -> Dict:
    """Load user configuration from ~/.vegh/config.json"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}


def save_config(config: Dict):
    """Save user configuration to ~/.vegh/config.json"""
    if not VEGH_ROOT.exists():
        VEGH_ROOT.mkdir(parents=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0: "", 1: "K", 2: "M", 3: "G", 4: "T"}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"


def get_dir_size(path: Path) -> int:
    """Calculate total size of a directory."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except:
        pass
    return total


def build_tree(
    path_list: List[str], root_name: str
) -> str:  # Changed to return str for simplicity, but actually it's Tree from rich
    # This function uses rich.Tree, so need to import here or return the tree
    from rich.tree import Tree

    tree = Tree(f"[bold cyan][ROOT] {root_name}[/bold cyan]")
    folder_map = {"": tree}

    for path in sorted(path_list):
        parts = Path(path).parts
        parent_path = ""
        for i, part in enumerate(parts):
            current_path = str(Path(*parts[: i + 1]))
            if current_path not in folder_map:
                folder_map[current_path] = folder_map[parent_path].add(
                    f"[blue]{part}[/blue]"
                    if i < len(parts) - 1
                    else f"[green]{part}[/green]"
                )
            parent_path = current_path
    return tree


# --- NATIVE CLIPBOARD HELPER ---
def _copy_to_clipboard_native(text: str) -> bool:
    """
    Copies text to clipboard using system tools (Zero-Dependency).
    Supports macOS (pbcopy), Windows (clip), Linux (xclip/wl-copy).
    """
    platform = sys.platform
    try:
        if platform == "darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
        elif platform == "win32":
            subprocess.run(["clip"], input=text, text=True, check=True)
        elif platform.startswith("linux"):
            # Try wl-copy first (Wayland), fallback to xclip (X11)
            try:
                subprocess.run(["wl-copy"], input=text, text=True, check=True)
            except FileNotFoundError:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text,
                    text=True,
                    check=True,
                )
        else:
            return False
    except Exception:
        return False
    return True
