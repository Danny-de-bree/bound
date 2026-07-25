"""Entry point for ``python -m bound``.

Delegates to :func:`bound.cli.main`.
"""

from __future__ import annotations

import sys

from bound.cli import main

if __name__ == "__main__":
    sys.exit(main())
