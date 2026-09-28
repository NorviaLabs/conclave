"""Entry point kept so `python main.py` and `uv run main.py` keep working.

The implementation now lives in the `conclave` package.
"""

from conclave.cli import run

if __name__ == "__main__":
    run()
