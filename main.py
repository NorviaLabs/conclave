"""Entry point kept so `python main.py` and `uv run main.py` keep working.

The library lives in `conclave`; the investigation app lives in
`investigation`.
"""

from investigation.cli import run

if __name__ == "__main__":
    run()
