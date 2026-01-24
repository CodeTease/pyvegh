from pathlib import Path
from typing import List, Tuple, Dict
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.layout import Layout
from rich.align import Align

from .jsonc import parse

# Load configuration from JSON
config_path = Path(__file__).parent / "config.jsonc"
with open(config_path, "r", encoding="utf-8") as f:
    config = parse(f.read())

LANG_MAP = {k: tuple(v) for k, v in config["LANG_MAP"].items()}
SLOC_IGNORE_EXTS = set(config["SLOC_IGNORE_EXTS"])
COMMENT_MAP = config["COMMENT_MAP"]
FILENAME_MAP = {k: tuple(v) for k, v in config["FILENAME_MAP"].items()}


class ProjectStats:
    def __init__(self):
        self.total_files = 0
        self.total_loc = 0
        self.lang_stats: Dict[str, Dict] = {}

    def add_file(self, path_str: str, loc: int):
        self.total_files += 1
        self.total_loc += loc

        path = Path(path_str)
        # .lower() handles both .s and .S
        ext = path.suffix.lower()
        name = path.name.lower()

        # Identify Language
        lang, color = "Other", "white"

        if name in FILENAME_MAP:
            lang, color = FILENAME_MAP[name]
        elif ext in LANG_MAP:
            lang, color = LANG_MAP[ext]

        # Update Stats
        if lang not in self.lang_stats:
            self.lang_stats[lang] = {"files": 0, "loc": 0, "color": color}

        self.lang_stats[lang]["files"] += 1
        self.lang_stats[lang]["loc"] += loc


def _make_bar(label: str, percent: float, color: str, width: int = 30) -> Text:
    """Manually renders a progress bar using unicode blocks."""
    filled_len = int((percent / 100.0) * width)
    unfilled_len = width - filled_len

    bar_str = ("█" * filled_len) + ("░" * unfilled_len)

    text = Text()
    text.append(f"{label:<20}", style=f"bold {color}")
    text.append(f"{bar_str} ", style=color)
    text.append(f"{percent:>5.1f}%", style="bold white")
    return text


def scan_sloc(path: str) -> List[Tuple[str, int]]:
    """Scans a directory for SLOC, using core dry_run_snap for traversal."""
    # We need to import dry_run_snap here or pass it in.
    # To avoid circular imports, we'll try to import it inside the function
    from ._core import dry_run_snap

    results = []

    # Use dry_run_snap to get the file list (respecting .gitignore/veghignore)
    # dry_run_snap returns (path, size). We just want the path.
    files = dry_run_snap(path)

    base_path = Path(path)

    for relative_path, _ in files:
        full_path = base_path / relative_path
        sloc = calculate_sloc(str(full_path))
        results.append((relative_path, sloc))

    return results


def count_sloc_from_text(content: str, ext: str) -> int:
    """Core logic to count SLOC from a string content."""
    if ext in SLOC_IGNORE_EXTS:
        return 0

    comment_prefix = COMMENT_MAP.get(ext)
    count = 0

    # Process line by line
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if comment_prefix and line.startswith(comment_prefix):
            continue
        count += 1

    return count


def calculate_sloc(file_path: str) -> int:
    """Calculates Source Lines of Code (SLOC) from a file on disk."""
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext in SLOC_IGNORE_EXTS:
        return 0

    try:
        # Check if file is binary
        with open(file_path, "rb") as f:
            chunk = f.read(512)
            if b'\x00' in chunk:
                return 0
        
        # Read file with error handling
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            return count_sloc_from_text(content, ext)
    except Exception:
        return 0


def render_dashboard(
    console: Console,
    file_name: str,
    raw_results: List[Tuple[str, int]],
    metric_name: str = "LOC",
):
    """Draws the beautiful CodeTease Analytics Dashboard."""

    # 1. Process Data
    stats = ProjectStats()
    for path, loc in raw_results:
        if loc > 0:
            stats.add_file(path, loc)

    if stats.total_loc == 0:
        console.print(
            "[yellow]No code detected (or binary only). Is this a ghost project?[/yellow]"
        )
        return

    sorted_langs = sorted(
        stats.lang_stats.items(), key=lambda item: item[1]["loc"], reverse=True
    )

    # 2. Build Layout
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3),
    )

    layout["body"].split_row(
        Layout(name="left", ratio=1), Layout(name="right", ratio=1)
    )

    # --- Header ---
    title_text = Text(
        f"📊 Vegh Analytics ({metric_name}): {file_name}",
        style="bold white on blue",
        justify="center",
    )
    layout["header"].update(Panel(title_text, box=box.HEAVY))

    # --- Left: Detailed Table ---
    table = Table(box=box.SIMPLE_HEAD, expand=True)
    table.add_column("Lang", style="bold")
    table.add_column("Files", justify="right")
    table.add_column(metric_name, justify="right", style="green")
    table.add_column("%", justify="right")

    for lang, data in sorted_langs:
        percent = (data["loc"] / stats.total_loc) * 100
        table.add_row(
            f"[{data['color']}]{lang}[/{data['color']}]",
            str(data["files"]),
            f"{data['loc']:,}",
            f"{percent:.1f}%",
        )

    layout["left"].update(
        Panel(table, title="[bold]Breakdown[/bold]", border_style="cyan")
    )

    # --- Right: Custom Manual Bar Chart ---
    chart_content = Text()

    # Take Top 12 languages
    for i, (lang, data) in enumerate(sorted_langs[:12]):
        percent = (data["loc"] / stats.total_loc) * 100
        bar = _make_bar(lang, percent, data["color"])
        chart_content.append(bar)
        chart_content.append("\n")

    if len(sorted_langs) > 12:
        chart_content.append(
            f"\n... and {len(sorted_langs) - 12} others", style="dim italic"
        )

    layout["right"].update(
        Panel(
            Align.center(chart_content, vertical="middle"),
            title="[bold]Distribution[/bold]",
            border_style="green",
        )
    )

    # --- Footer: Summary & Fun Comment ---
    if sorted_langs:
        top_lang = sorted_langs[0][0]
    else:
        top_lang = "Other"

    comment = "Code Hard, Play Hard! 🚀"

    # Logic Fun Comment
    if top_lang == "Rust":
        comment = "Blazingly Fast! 🦀"
    elif top_lang == "Python":
        comment = "Snake Charmer! 🐍"
    elif top_lang == "Haskell":
        comment = "Purely Functional... and confusing! 😵‍💫"
    elif top_lang == "Mojo":
        comment = "AI Speedster! 🔥"
    elif top_lang == "Solidity":
        comment = "Wen Lambo? 🏎️"
    elif top_lang == "Elixir":
        comment = "Scalability God! 💜"
    elif top_lang == "Astro":
        comment = "To the stars! 🚀"
    elif top_lang == "CSS":
        comment = "Center a div? Good luck! 🎨"
    elif "React" in top_lang:
        comment = "Component Heaven! ⚛️"
    elif top_lang in ["JavaScript", "TypeScript", "Vue", "Svelte"]:
        comment = "Web Scale! 🌐"
    elif top_lang in ["Assembly", "C", "C++"]:
        comment = "Low Level Wizardry! 🧙‍♂️"
    elif top_lang in ["FDON", "FWON", "BXSON"]:
        comment = "Teasers! ⚡"
    elif top_lang == "HTML":
        comment = "How To Meet Ladies? 😉"
    elif top_lang == "Go":
        comment = "Gopher it! 🐹"
    elif top_lang == "Java":
        comment = "Enterprise Grade! ☕"
    elif top_lang == "C#":
        comment = "Microsoft Magic! 🪟"
    elif top_lang == "PHP":
        comment = "Elephant in the room! 🐘"
    elif top_lang == "Swift":
        comment = "Feeling Swift? 🍏"
    elif top_lang == "Dart":
        comment = "Fluttering away! 🐦"
    elif top_lang == "SQL":
        comment = "DROP TABLE production; 💀"
    elif top_lang == "Terraform":
        comment = "Infrastructure as Code! 🏗️"
    elif top_lang == "Dockerfile":
        comment = "Containerized! 🐳"

    summary = f"[bold]Total {metric_name}:[/bold] [green]{stats.total_loc:,}[/green] | [bold]Analyzed Files:[/bold] {stats.total_files} | [italic]{comment}[/italic]"

    layout["footer"].update(
        Panel(Text.from_markup(summary, justify="center"), border_style="blue")
    )

    console.print(layout)
