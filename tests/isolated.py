"""Run a snippet in a fresh interpreter with a hard kill timeout. Test-only.

Used for size-limit checks whose failure mode is "computes for minutes": a subprocess can be
killed cleanly, a CPU-bound thread can't.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

PRELUDE = """\
import json, time
_started = time.perf_counter()
def _done(value):
    print(json.dumps({"elapsed": time.perf_counter() - _started, "value": value}))
"""


def run_isolated(body: str, hard_timeout: float = 15.0) -> dict:
    """Run `body` (which must call `_done(value)` once); return {"elapsed": s, "value": ...}.

    `elapsed` is measured inside the child, so interpreter start-up isn't counted.
    """
    code = PRELUDE + textwrap.dedent(body)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=hard_timeout,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(f"still computing after {hard_timeout:g} s (killed)") from None
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])
