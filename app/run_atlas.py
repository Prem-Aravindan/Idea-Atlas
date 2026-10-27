"""PyInstaller entry point (also: `python app/run_atlas.py` to run from source)."""
import sys

from atlas.main import main

if __name__ == "__main__":
    sys.exit(main())
