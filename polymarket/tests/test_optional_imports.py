"""Regression tests for optional top-level dependencies."""

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_base_package_import_does_not_require_web3():
    script = """
import builtins

original_import = builtins.__import__


def block_web3(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "web3" or name.startswith("web3."):
        raise ModuleNotFoundError("blocked optional web3 dependency")
    return original_import(name, globals, locals, fromlist, level)


builtins.__import__ = block_web3
import polymarket

assert polymarket.PolymarketClient is not None
assert "ConversionCalculator" in dir(polymarket)
"""

    subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
