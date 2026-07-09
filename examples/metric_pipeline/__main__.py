"""``python -m metric_pipeline`` entry point (run from the examples/ dir). The repo-root-friendly
wrapper is ``examples/validate_metrics.py``, which works from anywhere."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
