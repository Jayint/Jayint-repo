"""The prompt-style lever, in one place because BOTH ends of the pipe need it.

`REACT_MSG_STYLE=agentic` is a bundle, not a render flag — and the ordering matters:

  write time (loop._obs_body)   dedup + pip-strip, THEN safety_compress
  read time  (message_view)     envelope + fence, re-render at the recency tier

Dedup must happen BEFORE compression, not after. safety_compress's selection pass keeps at most 12
error blocks; on a 50k pytest dump with 200 identical collection errors, those 12 slots fill with
copies of ONE cause and a genuinely different cause further down is dropped — permanently, because
the compressed text is what gets stored. Deduping first means the 12 slots hold 12 distinct CAUSES.
Rendering afterwards can only shrink what write time already threw away.

(Both transforms are idempotent, so the render layer re-applies them harmlessly — which keeps the
view correct for hand-built histories and for traces recorded before this landed.)

Read per-call, never cached at import: a VM run flips it via env, and the unit tests monkeypatch it.
"""
from __future__ import annotations

import os

_LEVER = "REACT_MSG_STYLE"


def agentic() -> bool:
    """True when the agentic prompt bundle is on. Default is `classic` — the shape that shipped, and
    the A/B's control arm."""
    return os.getenv(_LEVER, "classic").lower() == "agentic"
