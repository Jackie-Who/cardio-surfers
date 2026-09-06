"""Entry point for the frozen build.

PyInstaller runs its entry script as a top-level module, not as part of a
package, so `src/cardio_surfers/__main__.py` cannot be used directly -- its
`from .main import main` raises "attempted relative import with no known parent
package". This file does the same job with an absolute import.

Running from source still works either way:

    python -m cardio_surfers
    python launcher.py
"""

from __future__ import annotations

import os
import sys

# A source checkout has the package under src/; a frozen build already has it
# importable, and this is then a no-op.
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from cardio_surfers.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
