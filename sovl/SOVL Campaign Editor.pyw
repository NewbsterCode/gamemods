"""Double-click to launch the SOVL Campaign Editor (no console window)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sovl_editor.app import main  # noqa: E402

main()
