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
# --- LANGUAGE DEFINITIONS (UPDATED) ---
LANG_MAP = {
    # Systems & Low Level
    ".rs": ("Rust", "red"),
    ".c": ("C", "white"),
    ".h": ("C/C++", "white"),
    ".cpp": ("C++", "blue"),
    ".hpp": ("C++", "blue"),
    ".cc": ("C++", "blue"),
    ".cxx": ("C++", "blue"), 
    ".ino": ("Arduino", "blue"), # Actually it's C++
    ".go": ("Go", "cyan"),
    ".asm": ("Assembly", "white"),
    ".s": ("Assembly", "white"), 
    ".zig": ("Zig", "yellow"),
    ".f90": ("Fortran", "magenta"),
    ".f95": ("Fortran", "magenta"),
    ".f03": ("Fortran", "magenta"),
    ".f08": ("Fortran", "magenta"),
    ".f": ("Fortran", "magenta"),
    ".hs": ("Haskell", "magenta"),
    ".ml": ("OCaml", "yellow"),
    ".mli": ("OCaml", "yellow"),
    ".nim": ("Nim", "cyan"),
    ".v": ("V", "green"),
    ".ada": ("Ada", "red"),
    ".adb": ("Ada", "red"),
    ".ads": ("Ada", "red"),
    ".fs": ("F#", "blue"),
    ".fsi": ("F#", "blue"),
    ".fsx": ("F#", "blue"),
    ".sv": ("SystemVerilog", "cyan"),
    ".svh": ("SystemVerilog", "cyan"),
    # ".v": ("Verilog", "cyan"), # Conflict with VLang
    ".pas": ("Pascal", "yellow"),
    ".pp": ("Pascal", "cyan"),

    # Enterprise & Mobile
    ".java": ("Java", "red"),
    ".jav": ("Java", "red"),
    ".scala": ("Scala", "red"),
    ".groovy": ("Groovy", "white"),
    ".clj": ("Clojure", "green"),
    ".cljs": ("ClojureScript", "green"),
    ".cljc": ("Clojure (Common)", "green"),
    ".kt": ("Kotlin", "magenta"),
    ".kts": ("Kotlin", "magenta"),
    ".cs": ("C#", "green"),
    ".swift": ("Swift", "bright_red"), 
    ".m": ("Objective-C", "blue"), # Could be Matlab too
    ".dart": ("Dart", "cyan"),
    ".ex": ("Elixir", "purple"),
    ".exs": ("Elixir", "purple"),
    ".erl": ("Erlang", "red"),
    ".gleam": ("Gleam", "pink1"),
    ".kmp": ("Kotlin Multiplatform", "magenta"),

    # Web & Scripting
    ".py": ("Python", "blue"),
    ".pyi": ("Python", "blue"),
    ".ipynb": ("Jupyter", "yellow"),
    ".js": ("JavaScript", "yellow"),
    ".jsx": ("JavaScript (React)", "yellow"),
    ".mjs": ("JavaScript (ESM)", "yellow"),
    ".cjs": ("JavaScript (CJS)", "yellow"), 
    ".coffee": ("CoffeeScript", "brown"),
    ".ts": ("TypeScript", "cyan"),
    ".tsx": ("TypeScript (React)", "cyan"),
    ".vue": ("Vue", "green"),
    ".astro": ("Astro", "orange3"),
    ".html": ("HTML", "magenta"),
    ".htm": ("HTML", "magenta"),
    ".css": ("CSS", "blue_violet"),
    ".scss": ("SCSS", "magenta"),
    ".sass": ("Sass", "magenta"),
    ".hcl": ("HCL", "purple"),
    ".tex": ("LaTeX", "blue"),
    ".less": ("LESS", "blue"),
    ".styl": ("Stylus", "green"),
    ".twig": ("Twig", "green"),
    ".svelte": ("Svelte", "orange_red1"),
    ".heex": ("Phoenix HEEx", "purple"),
    ".leex": ("Phoenix LEEx", "purple"),
    ".mjml": ("MJML (Email)", "cyan"),
    ".liquid": ("Liquid", "blue"),
    ".php": ("PHP", "magenta"),
    ".rb": ("Ruby", "red"),
    ".rake": ("Ruby", "red"),
    ".lua": ("Lua", "blue"),
    ".cr": ("Crystal", "white"),
    ".pl": ("Perl", "blue"), # Could be Prolog too
    ".pm": ("Perl", "blue"),
    ".sh": ("Shell", "green"),
    ".bash": ("Shell", "green"),
    ".zsh": ("Shell", "green"),
    ".ps1": ("PowerShell", "blue"),
    ".psm1": ("PowerShell", "blue"),
    ".bat": ("Batch", "yellow"),
    ".cmd": ("Batch", "yellow"),

    # Game Development
    ".gd": ("GDScript", "white"),
    ".glsl": ("GLSL", "green"),
    ".hlsl": ("HLSL", "green"),
    ".wgsl": ("WGSL", "green"),

    # Data & Config
    ".json": ("JSON", "yellow"),
    ".toml": ("TOML", "yellow"),
    ".yaml": ("YAML", "yellow"),
    ".yml": ("YAML", "yellow"),
    ".xml": ("XML", "magenta"),
    ".sql": ("SQL", "yellow"),
    ".md": ("Markdown", "white"),
    ".txt": ("Text", "white"),
    ".ini": ("INI", "white"), 
    ".conf": ("Config", "white"),
    ".csv": ("CSV", "green"),
    ".tsv": ("TSV", "green"),
    
    # AI & TeaserLang
    ".mojo": ("Mojo", "red"),
    ".🔥": ("Mojo", "red"),
    ".fdon": ("FDON", "bright_green"),
    ".fwon": ("FWON", "bright_green"),
    ".bxson": ("BXSON", "bright_green"),

    # Infrastructure & Others
    ".dockerfile": ("Dockerfile", "blue"),
    ".tf": ("Terraform", "magenta"),
    ".nix": ("Nix", "cyan"),
    ".sol": ("Solidity", "bright_cyan"),
    ".r": ("R", "blue"),
    ".hack": ("Hack", "cyan"),
    ".jl": ("Julia", "purple"),
    ".wat": ("WebAssembly Text", "white"),
    ".proto": ("Protobuf", "cyan"),
    ".log": ("Log", "dim white"),
    ".prisma": ("Prisma", "white"),
    ".graphql": ("GraphQL", "magenta"),
    ".gql": ("GraphQL", "magenta"),
    ".vto": ("Vento", "bright_green"),
    ".env": ("Env Config", "red"),
    ".lock": ("Lock File", "dim white"),
}

# --- FILENAME MAP ---
FILENAME_MAP = {
    "dockerfile": ("Dockerfile", "blue"),
    "docker-compose.yml": ("Docker Compose", "blue"),
    "docker-compose.yaml": ("Docker Compose", "blue"),
    "makefile": ("Makefile", "white"),
    "justfile": ("Justfile", "yellow"),
    "procfile": ("Heroku Procfile", "red"),
    "rakefile": ("Ruby", "red"),
    "gemfile": ("Ruby Config", "red"),
    "cargo.toml": ("Cargo", "red"),
    "pyproject.toml": ("Python Config", "blue"),
    "package.json": ("NPM Config", "yellow"),
    "tsconfig.json": ("TS Config", "cyan"),
    "webpack.config.js": ("Webpack Config", "yellow"),
    "tailwind.config.js": ("Tailwind Config", "cyan"),
    "tailwind.config.ts": ("Tailwind Config", "cyan"),
    "vite.config.js": ("Vite Config", "purple"),
    "vite.config.ts": ("Vite Config", "purple"),
    "postcss.config.js": ("PostCSS", "red"),
    "go.mod": ("Go Module", "cyan"),
    "go.sum": ("Go Sum", "cyan"),
    ".gitignore": ("Git Config", "white"),
    ".dockerignore": ("Docker Ignore", "blue"), 
    ".npmignore": ("NPM Ignore", "yellow"),
    ".veghignore": ("Vegh Ignore", "bright_green"),
    ".editorconfig": ("Editor Config", "white"),
    ".env.example": ("Env Template", "dim white"),
    ".env.production": ("Env Production", "red"),
    ".env.local": ("Env Development", "green"),
    "build.gradle": ("Gradle", "green"),
    "build.gradle.kts": ("Gradle Kotlin", "green"),
    "settings.gradle": ("Gradle Settings", "green"),
    "settings.gradle.kts": ("Gradle Settings Kotlin", "green"),
    "pom.xml": ("Maven", "red"),
    "pubspec.yaml": ("Flutter Config", "blue"),
    "vagrantfile": ("Vagrant", "blue"),
    "jenkinsfile": ("Groovy", "white"),
    "wrangler.toml": ("Cloudflare", "orange3"), 
    "vercel.json": ("Vercel", "white"),
    "netlify.toml": ("Netlify", "cyan"),
    "next.config.js": ("Next.js Config", "white"),
    "nuxt.config.js": ("Nuxt.js Config", "green"),
    "gatsby-config.js": ("Gatsby Config", "purple"),
    "package-lock.json": ("NPM Lock", "yellow"),
    "pnpm-lock.yaml": ("PNPM Lock", "yellow"),
    "pnpm-workspace.yaml": ("PNPM Workspace", "yellow"),
    "firebase.json": ("Firebase Config", "yellow"),
    "deno.json": ("Deno Config", "cyan"),
    "deno.jsonc": ("Deno Config", "cyan"),
    ".prettierrc": ("Prettier Config", "white"),
    ".eslintrc": ("ESLint Config", "white"),
    ".eslintrc.json": ("ESLint Config", "white"),
    ".eslintrc.js": ("ESLint Config", "white"),
    "pyrightconfig.json": ("Pyright Config", "blue"),
    "tslint.json": ("TSLint Config", "cyan"),
    "composer.json": ("Composer (PHP)", "magenta"),
    "composer.lock": ("Composer Lock", "magenta"),
    "setup.py": ("Python Setup", "blue"),
    "requirements.txt": ("Python Requirements", "blue"),
    "mix.exs": ("Elixir Mix", "purple"),
    "rebar.config": ("Erlang Rebar", "red"),
    "import_map.json": ("Deno Import Map", "cyan"),
    ".babelrc": ("Babel Config", "yellow"),
    "babel.config.js": ("Babel Config", "yellow"),
    ".nycrc": ("Istanbul Config", "yellow"),
    ".swcrc": ("SWC Config", "cyan"),
    # CI/CD & DevOps
    ".gitlab-ci.yml": ("GitLab CI", "orange_red1"),
    "cloudformation.yaml": ("CloudFormation", "orange3"),
    "cloudformation.yml": ("CloudFormation", "orange3"),
    "fly.toml": ("Fly.io Config", "purple"),
    # CodeTease Specific
    ".veghhooks.json": ("Vegh Hooks", "bright_green"),
    "sensify.yaml": ("Sensify Config", "bright_green"),
    "carade.conf": ("Carade Config", "bright_green"),
    # Special Cases
    "license": ("License", "white"),
    "readme": ("Readme", "white"),
    "license.md": ("License", "white"),
    "readme.md": ("Readme", "white"),
    "license.txt": ("License", "white"),
    "readme.txt": ("Readme", "white"),
    "contributing.md": ("Contributing", "white"),
    "contributing.txt": ("Contributing", "white"),
    "changelog.md": ("Changelog", "white"),
    "changelog.txt": ("Changelog", "white"),
    "security.md": ("Security", "white"),
    "security.txt": ("Security", "white"),
    "code_of_conduct.md": ("Code of Conduct", "white"),
    "code_of_conduct.txt": ("Code of Conduct", "white"),
    "agents.md": ("Agents", "bright_green"), # For AI coding agents, rare but possible. easily find in big repositories
    "authors.md": ("Authors", "white"),
    "authors": ("Authors", "white"),
    "version.txt": ("Version", "white"),
    "version.md": ("Version", "white"),
    "version": ("Version", "white"),
    "codeowners": ("Code Owners", "white"),
    "funding.yml": ("Funding", "white"),
    "todo.md": ("Todo", "white"),
    "todo": ("Todo", "white"),

}

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

def render_dashboard(console: Console, file_name: str, raw_results: List[Tuple[str, int]]):
    """Draws the beautiful CodeTease Analytics Dashboard."""
    
    # 1. Process Data
    stats = ProjectStats()
    for path, loc in raw_results:
        if loc > 0:
            stats.add_file(path, loc)
    
    if stats.total_loc == 0:
        console.print("[yellow]No code detected (or binary only). Is this a ghost project?[/yellow]")
        return

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
    title_text = Text(f"📊 Vegh Analytics: {file_name}", style="bold white on blue", justify="center")
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
    chart_content = Text()
    
    # Take Top 12 languages
    for i, (lang, data) in enumerate(sorted_langs[:12]):
        percent = (data['loc'] / stats.total_loc) * 100
        bar = _make_bar(lang, percent, data['color'])
        chart_content.append(bar)
        chart_content.append("\n")
    
    if len(sorted_langs) > 12:
        chart_content.append(f"\n... and {len(sorted_langs) - 12} others", style="dim italic")

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
    
# Logic Fun Comment
    if top_lang == "Rust": comment = "Blazingly Fast! 🦀"
    elif top_lang == "Python": comment = "Snake Charmer! 🐍"
    elif top_lang == "Haskell": comment = "Purely Functional... and confusing! 😵‍💫"
    elif top_lang == "Mojo": comment = "AI Speedster! 🔥"
    elif top_lang == "Solidity": comment = "Wen Lambo? 🏎️"
    elif top_lang == "Elixir": comment = "Scalability God! 💜"
    elif top_lang == "Astro": comment = "To the stars! 🚀"
    elif top_lang == "CSS": comment = "Center a div? Good luck! 🎨"
    elif "React" in top_lang: comment = "Component Heaven! ⚛️"
    elif top_lang in ["JavaScript", "TypeScript", "Vue", "Svelte"]: comment = "Web Scale! 🌐"
    elif top_lang in ["Assembly", "C", "C++"]: comment = "Low Level Wizardry! 🧙‍♂️"
    elif top_lang in ["FDON", "FWON", "BXSON"]: comment = "Teasers! ⚡"
    elif top_lang == "HTML": comment = "How To Meet Ladies? 😉"
    elif top_lang == "Go": comment = "Gopher it! 🐹"
    elif top_lang == "Java": comment = "Enterprise Grade! ☕"
    elif top_lang == "C#": comment = "Microsoft Magic! 🪟"
    elif top_lang == "PHP": comment = "Elephant in the room! 🐘"
    elif top_lang == "Swift": comment = "Feeling Swift? 🍏"
    elif top_lang == "Dart": comment = "Fluttering away! 🐦"
    elif top_lang == "SQL": comment = "DROP TABLE production; 💀"
    elif top_lang == "Terraform": comment = "Infrastructure as Code! 🏗️"
    elif top_lang == "Dockerfile": comment = "Containerized! 🐳"

    summary = f"[bold]Total LOC:[/bold] [green]{stats.total_loc:,}[/green] | [bold]Analyzed Files:[/bold] {stats.total_files} | [italic]{comment}[/italic]"
    
    layout["footer"].update(Panel(
        Text.from_markup(summary, justify="center"),
        border_style="blue"
    ))

    console.print(layout)