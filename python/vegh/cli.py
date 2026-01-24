from .cli_main import app
# noqa: F401 to expose app at package level
from . import cli_commands  

if __name__ == "__main__":
    app()
