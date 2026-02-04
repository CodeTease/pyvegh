from .cli_main import app

# noqa: F401 to expose app at package level
from . import cli_commands  # noqa: F401

if __name__ == "__main__":
    app()
