from __future__ import annotations

import re

_FROM = re.compile(r"^\s*FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE)


def parse_from(dockerfile: str) -> str | None:
    """Return the tag of the first `FROM <tag>` line, or None."""
    m = _FROM.search(dockerfile or "")
    return m.group(1) if m else None


def link_testbed(dockerfile: str, src: str = "/repo") -> str:
    """Append `RUN ln -sfn {src} /testbed` so the repo lives at /testbed. Idempotent."""
    link = f"RUN ln -sfn {src} /testbed"
    if link in dockerfile:
        return dockerfile
    return dockerfile.rstrip("\n") + "\n" + link + "\n"


def clone_lines(repo_url: str, dest: str = "/repo") -> str:
    """git-install + shallow-clone block (no trailing newline; caller joins/joins-in)."""
    return (
        "RUN apt-get update && apt-get install -y --no-install-recommends git "
        "&& rm -rf /var/lib/apt/lists/*\n"
        f"RUN git clone --depth=1 {repo_url} {dest}"
    )
