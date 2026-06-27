"""
Root pytest configuration.

Adds the project root to sys.path so all MondayOS packages are importable
without installation when running `pytest` from the project root.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
