import sys
from pathlib import Path

# Pytest puts this folder on sys.path, not tests/, where the shared builders and scale helpers live.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
