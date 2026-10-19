"""Publication figures; keep the numbers.py entry point from shadowing stdlib numbers."""
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or '.').resolve() != _here]
