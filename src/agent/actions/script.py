"""De-graph a rendered build script for the react (script-primary) arm.

`render_build_script` is shared with the v3 (graph-primary) arm, so its output carries
a "COMPILED from the certified dependency graph. DO NOT EDIT. Edit the graph and
re-render; this file is an artifact, not a source." header plus inline `#@node`/`#@check`/
`#@need` annotations. In the react arm the agent edits this very file, so that framing is
self-contradictory (the prompt says "edit it"; the file says "DO NOT EDIT"). We strip it
here — only from the react loop's OWN seeded copy — leaving `render_build_script` (and the
v3 arm) untouched.

Safe to strip: the header and every `#@` line are inert. Execution is `bash`, so they are
comments; failure attribution comes from an ERR trap the sandbox injects at RUN time
(`_wrap_with_err_trap` → `_parse_install_failure` reads `$BASH_COMMAND`), never from these
markers. Executable lines are preserved verbatim.
"""
from __future__ import annotations

# Comment phrases that only make sense under the graph-primary model.
_GRAPH_PHRASES = (
    "COMPILED from the certified dependency graph",
    "Edit the graph and re-render",
    "artifact, not a source",
    "DO NOT EDIT",
)

_NEUTRAL_HEADER = (
    "#!/usr/bin/env bash",
    "#",
    "# setup.sh — the environment build script. Edit it directly to fix the build.",
    "#",
)


def _drop(line: str) -> bool:
    """True if this line should be removed: a `#@` annotation, a graph-phrase comment, or
    any COMMENT line that still mentions the graph. Executable lines are never dropped
    (only comments — a line whose stripped form starts with `#`, other than the shebang).

    EXCEPTION: `#@config-env` markers are KEPT. Unlike `#@node`/`#@check`/`#@need` (the
    graph-primary framing the react agent edits against), a `#@config-env VAR=value`
    marker is a cross-artifact hand-off — the eval adapter bakes it into a Dockerfile ENV
    by scanning the FINAL setup.sh (multi_docker_eval_adapter._CONFIG_ENV_RE). Stripping
    it here would silently drop the react arm's config channel (review IMPORTANT 2)."""
    s = line.strip()
    if s.startswith("#@config-env"):
        return False                                  # cross-artifact hand-off, not graph framing
    if s.startswith("#@"):
        return True
    is_comment = s.startswith("#") and not s.startswith("#!")
    if is_comment and ("graph" in line.lower() or any(p in line for p in _GRAPH_PHRASES)):
        return True
    return False


def strip_graph_framing(script: str) -> str:
    """Return *script* with all graph-primary framing removed: the header block (everything
    before the first ``set -...``, replaced with a neutral editable-artifact header), the
    inline `#@node`/`#@check`/`#@need` annotations, and any remaining COMMENT line that
    mentions the graph. Executable lines are kept verbatim."""
    had_trailing_nl = script.endswith("\n")
    lines = script.splitlines()
    anchor = next((i for i, ln in enumerate(lines) if ln.strip().startswith("set -")), None)

    if anchor is not None:
        out_lines = list(_NEUTRAL_HEADER) + [ln for ln in lines[anchor:] if not _drop(ln)]
    else:
        out_lines = [ln for ln in lines if not _drop(ln)]

    out = "\n".join(out_lines)
    return out + "\n" if had_trailing_nl else out
