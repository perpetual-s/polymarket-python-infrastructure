"""Pytest configuration for polymarket tests."""

import sys
from pathlib import Path

# Add repository root to PYTHONPATH so local `polymarket` imports resolve.
repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(repo_root))
