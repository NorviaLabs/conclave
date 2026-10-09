"""Entry point kept so `python main.py` and `uv run main.py` keep working.

Applications are definitions, not a package: this is the same command as
`conclave`, and `conclave.cli` is where it lives.
"""

from conclave.cli import run

if __name__ == "__main__":
    run()
