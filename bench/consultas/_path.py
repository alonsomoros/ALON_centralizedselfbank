"""Pone la raíz del repositorio en sys.path para poder hacer `from selfbank import ...`."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
