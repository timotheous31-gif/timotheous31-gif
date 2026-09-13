"""``python -m app.cli`` — the entry point a deployment can rely on.

The installed ``osint`` script needs the package on ``PATH``; inside a container,
or straight after a ``pip install -e .``, ``python -m app.cli`` always works. The
bootstrap documentation uses this form for exactly that reason.
"""

from __future__ import annotations

from app.cli.main import run

if __name__ == "__main__":
    run()
