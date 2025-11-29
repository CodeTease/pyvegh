import os
from pathlib import Path
from typing import List, Tuple, Dict
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.layout import Layout
from rich.align import Align

# --- LANGUAGE DEFINITIONS ---
# Map extension -> (Language Name, Color)
LANG_MAP = {
    ".rs": ("Rust", "red"),
    ".py": ("Python", "blue"),
    ".pyi": ("Python", "blue"),
    ".js": ("JavaScript", "yellow"),
    ".jsx": ("JavaScript", "yellow"),
    ".mjs": ("JavaScript", "yellow"),
    ".ts": ("TypeScript", "cyan"),
    ".tsx": ("TypeScript", "cyan"),
    ".html": ("HTML", "magenta"),
    ".css": ("CSS", "blue_violet"),
    ".scss": ("SCSS", "magenta"),
    ".c": ("C", "white"),
    ".h": ("C/C++", "white"),
    ".cpp": ("C++", "blue"),
    ".hpp": ("C++", "blue"),
    ".cc": ("C++", "blue"),
    ".go": ("Go", "cyan"),
    ".java": ("Java", "red"),
    ".rb": ("Ruby", "red"),
    ".php": ("PHP", "magenta"),
    ".sh": ("Shell", "green"),
    ".bash": ("Shell", "green"),
    ".zsh": ("Shell", "green"),
    ".json": ("JSON", "yellow"),
    ".toml": ("TOML", "yellow"),
    ".yaml": ("YAML", "yellow"),
    ".yml": ("YAML", "yellow"),
    ".md": ("Markdown", "white"),
    ".txt": ("Text", "white"),
    ".sql": ("SQL", "yellow"),
    ".dockerfile": ("Dockerfile", "blue"),
}

# Filenames that imply a language without extension
FILENAME_MAP = {
    "dockerfile": ("Dockerfile", "blue"),
    "makefile": ("Makefile", "white"),
    "cargo.toml": ("Cargo", "red"),
    "pyproject.toml": ("Python Config", "blue"),
    "package.json": ("NPM Config", "yellow"),
}

class ProjectStats:
    def __init__(self):
        self.total_files = 0
        self.total_loc = 0
        self.lang_stats: Dict[str, Dict] = {} # {"Rust": {"files": 0, "loc": 0, "color": "red"}}

    def add_file(self, path_str: str, loc: int):
        self.total_files += 1
        self.total_loc += loc
        
        path = Path(path_str)
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
    # Logic: Calculate filled blocks based on width
    filled_len = int((percent / 100.0) * width)
    unfilled_len = width - filled_len
    
    # Use solid block for filled, shade for empty
    bar_str = ("█" * filled_len) + ("░" * unfilled_len)
    
    text = Text()
    text.append(f"{label:<12}", style=f"bold {color}")
    text.append(f"{bar_str} ", style=color)
    text.append(f"{percent:>5.1f}%", style="bold white")
    return text

def render_dashboard(console: Console, file_name: str, raw_results: List[Tuple[str, int]]):
    """Draws the beautiful CodeTease Analytics Dashboard."""
    
    # 1. Process Data
    stats = ProjectStats()
    for path, loc in raw_results:
        if loc > 0:
            stats.add_file(path, loc)
    
    if stats.total_loc == 0:
        console.print("[yellow]No code detected (or binary only).[/yellow]")
        return

    # Sort languages by LOC desc
    sorted_langs = sorted(
        stats.lang_stats.items(), 
        key=lambda item: item[1]['loc'], 
        reverse=True
    )

    # 2. Build Layout
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3)
    )
    
    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1)
    )

    # --- Header ---
    title_text = Text(f"📊 CodeTease Analytics: {file_name}", style="bold white on blue", justify="center")
    layout["header"].update(Panel(title_text, box=box.HEAVY))

    # --- Left: Detailed Table ---
    table = Table(box=box.SIMPLE_HEAD, expand=True)
    table.add_column("Lang", style="bold")
    table.add_column("Files", justify="right")
    table.add_column("LOC", justify="right", style="green")
    table.add_column("%", justify="right")

    for lang, data in sorted_langs:
        percent = (data['loc'] / stats.total_loc) * 100
        table.add_row(
            f"[{data['color']}]{lang}[/{data['color']}]",
            str(data['files']),
            f"{data['loc']:,}",
            f"{percent:.1f}%"
        )
    
    layout["left"].update(Panel(
        table, 
        title="[bold]Breakdown[/bold]", 
        border_style="cyan"
    ))

    # --- Right: Custom Manual Bar Chart ---
    # We build the chart manually to avoid dependency on rich.bar_chart
    chart_content = Text()
    
    # Take Top 10 languages
    for i, (lang, data) in enumerate(sorted_langs[:10]):
        percent = (data['loc'] / stats.total_loc) * 100
        bar = _make_bar(lang, percent, data['color'])
        chart_content.append(bar)
        chart_content.append("\n")
    
    if len(sorted_langs) > 10:
        chart_content.append(f"\n... and {len(sorted_langs) - 10} others", style="dim italic")

    layout["right"].update(Panel(
        Align.center(chart_content, vertical="middle"), 
        title="[bold]Distribution[/bold]", 
        border_style="green"
    ))

    # --- Footer: Summary & Fun Comment ---
    if sorted_langs:
        top_lang = sorted_langs[0][0]
    else:
        top_lang = "Other"

    comment = "Code Hard, Play Hard! 🚀"
    
    if top_lang == "Rust": comment = "Blazingly Fast! 🦀"
    elif top_lang == "Python": comment = "Snake Charmer! 🐍"
    elif top_lang == "JavaScript" or top_lang == "TypeScript": comment = "Web Scale! 🌐"
    elif top_lang == "C" or top_lang == "C++": comment = "Low Level Wizardry! 🧙‍♂️"
    elif top_lang == "HTML": comment = "How To Meet Ladies? 😉" 

    summary = f"[bold]Total LOC:[/bold] [green]{stats.total_loc:,}[/green] | [bold]Analyzed Files:[/bold] {stats.total_files} | [italic]{comment}[/italic]"
    
    layout["footer"].update(Panel(
        Text.from_markup(summary, justify="center"),
        border_style="blue"
    ))

    console.print(layout)