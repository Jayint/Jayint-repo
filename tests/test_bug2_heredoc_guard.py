"""Regression tests for Bug 2 (no-op recipe): single-line heredoc-opens must NOT
enter the recipe Dockerfile.

Run: python3 -m pytest tests/test_bug2_heredoc_guard.py -v
"""
from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.synthesis import (
    _is_source_file_edit,
    _is_unterminated_heredoc,
    build_commands_from_ledger,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(step, cmd, rc, mutation_class=None):
    return ActionEvent(
        step=step,
        task_id=None,
        cmd=cmd,
        rc=rc,
        stdout="",
        stdout_path=None,
        stderr_path=None,
        env_revision_before=0,
        env_revision_after=0,
        mutation_class=mutation_class,
        container_id="c1",
        summary="",
    )


# ---------------------------------------------------------------------------
# _is_source_file_edit unit tests
# ---------------------------------------------------------------------------

def test_single_line_tmp_heredoc_open_returns_false():
    """cat > /tmp/scratch.py << 'EOF' (no body, no terminator) must be dropped.

    This is the Mechanism A case: _extract_worker_action plain/fenced branch
    applied .splitlines()[0] and discarded the heredoc body and EOF terminator.
    Keeping this command would emit an unterminated RUN cat > /tmp/... << 'EOF'
    in the Dockerfile, triggering the eval adapter's greedy heredoc-accumulation.
    """
    cmd = "cat > /tmp/prepend.py << 'EOF'"
    assert not _is_source_file_edit(cmd), (
        f"Single-line /tmp/ heredoc-open must return False; cmd={repr(cmd)}"
    )


def test_single_line_app_heredoc_open_returns_false():
    """cat << 'EOF' > /app/pyproject.toml (single-line, no body) must be dropped.

    Same recording truncation as above but writing to a persistent repo path.
    The heredoc body is gone so the command is unreplayable regardless of path.
    """
    cmd = "cat << 'EOF' > /app/pyproject.toml"
    assert not _is_source_file_edit(cmd), (
        f"Single-line /app/ heredoc-open must return False; cmd={repr(cmd)}"
    )


def test_single_line_testbed_heredoc_open_returns_false():
    """cat << 'EOF' > /testbed/pyproject.toml (single-line) must be dropped."""
    cmd = "cat << 'EOF' > /testbed/pyproject.toml"
    assert not _is_source_file_edit(cmd), (
        f"Single-line /testbed/ heredoc-open must return False; cmd={repr(cmd)}"
    )


def test_fully_terminated_heredoc_is_kept():
    """A multi-line heredoc with a body and terminator IS a real file write and must be kept."""
    cmd = "cat > /testbed/pyproject.toml << 'EOF'\n[project]\nname = foo\nEOF"
    assert _is_source_file_edit(cmd), (
        f"Fully-terminated heredoc must return True; cmd={repr(cmd)}"
    )


def test_printf_redirect_without_heredoc_is_kept():
    """printf '%s\n' ... > file — normal inline redirect, no heredoc, must be kept."""
    cmd = "printf '%s\n' 'import sys' 'path = \"/app/x.py\"' > /app/patch.py"
    assert _is_source_file_edit(cmd), (
        f"printf redirect (no heredoc) must return True; cmd={repr(cmd)}"
    )


def test_sed_i_unaffected_by_heredoc_guard():
    """sed -i has no <<; must still be kept."""
    cmd = "sed -i 's/old/new/' /app/setup.py"
    assert _is_source_file_edit(cmd), (
        f"sed -i must return True; cmd={repr(cmd)}"
    )


# ---------------------------------------------------------------------------
# build_commands_from_ledger integration tests
# ---------------------------------------------------------------------------

def test_single_line_heredoc_open_not_emitted_in_recipe():
    """The proxy_pool recipe: 4 truncated heredoc-opens must be dropped; pip installs kept.

    Without the fix, cmd[0] ("cat > /tmp/prepend.py << 'EOF'") was kept by
    _is_source_file_edit (matched _RE_WRITE_PREFIX+_RE_FILE_WRITE) and emitted as
    a single-line "RUN cat > /tmp/prepend.py << 'EOF'" in the Dockerfile.  The
    eval adapter then entered heredoc-accumulation mode and consumed ALL subsequent
    RUN instructions into one broken blob.
    """
    ledger = ActionLedger()
    # The 13 build_commands from the 084304 artifact (mutation_class=None for all,
    # because the v1 ledger appender doesn't call classify_mutation):
    ledger.append(_event(1, "cat > /tmp/prepend.py << 'EOF'", 0, None))       # DROPPED
    ledger.append(_event(2, "printf '%s\n' 'import sys' > /app/x.py", 0, None))  # kept
    ledger.append(_event(3, "pip install -r requirements.txt", 0, "language_package_install"))  # kept
    ledger.append(_event(4, "cat << 'EOF' > /app/pyproject.toml", 0, None))   # DROPPED
    ledger.append(_event(5, "pip install -e . 2>&1", 0, "language_package_install"))   # kept
    ledger.append(_event(6, "cat << 'EOF' > /app/pyproject.toml", 0, None))   # DROPPED
    ledger.append(_event(7, "pip install -e . 2>&1", 0, "language_package_install"))   # kept
    ledger.append(_event(8, "cat << 'EOF' > /app/pyproject.toml", 0, None))   # DROPPED
    ledger.append(_event(9, "pip install -e . 2>&1", 0, "language_package_install"))   # kept

    cmds = build_commands_from_ledger(ledger)

    # No instruction in the recipe may contain a bare '<<' (unterminated heredoc-open)
    heredoc_openers = [c for c in cmds if "<<" in c and "\n" not in c]
    assert heredoc_openers == [], (
        f"No single-line heredoc-opens should reach the recipe; got: {heredoc_openers}"
    )

    # The pip installs must still be present
    assert any("pip install -r requirements.txt" in c for c in cmds), \
        f"pip install must be in recipe; cmds={cmds}"

    # The printf redirect (no heredoc) must be kept
    assert any("printf" in c and "/app/x.py" in c for c in cmds), \
        f"printf redirect must be in recipe; cmds={cmds}"

    # The /tmp/ heredoc-open must not appear
    assert not any("/tmp/prepend.py" in c for c in cmds), \
        f"/tmp/prepend.py heredoc-open must be dropped; cmds={cmds}"


def test_no_single_run_contains_another_run_directive():
    """After the fix, no single recipe command should contain a 'RUN ' prefix.

    Before the fix: the eval adapter's greedy accumulation produced one giant
    instruction whose body contained lines prefixed 'RUN <actual-command>'.
    This test checks the recipe-level invariant from the agent's side.
    """
    ledger = ActionLedger()
    ledger.append(_event(1, "cat > /tmp/prepend.py << 'EOF'", 0, None))  # triggers eval-adapter bug
    ledger.append(_event(2, "pip install -e .", 0, "language_package_install"))
    ledger.append(_event(3, "pip install requests", 0, "language_package_install"))

    cmds = build_commands_from_ledger(ledger)

    for c in cmds:
        assert "\nRUN " not in c, (
            f"No recipe command body should contain 'RUN ' on a subsequent line; "
            f"cmd={repr(c[:120])}"
        )


def test_heredoc_open_dropped_from_recipe_even_when_mutation_class_set():
    """A single-line heredoc-open must be dropped EVEN when classify_mutation tagged it.

    classify_mutation matches the ' > ' in `cat > f << 'EOF'` and returns
    'file_or_env_change'. The `if not event.mutation_class and not _is_source_file_edit`
    guard is then short-circuited (mutation_class is truthy), so _is_source_file_edit is
    never consulted — the broken opener would survive into the recipe. The dedicated
    _is_unterminated_heredoc check in build_commands_from_ledger runs BEFORE that branch
    and drops it regardless. Without that earlier guard, this test fails.
    """
    ledger = ActionLedger()
    ledger.append(_event(1, "cat > /tmp/prepend.py << 'EOF'", 0, "file_or_env_change"))  # DROPPED
    ledger.append(_event(2, "pip install -e .", 0, "language_package_install"))           # kept

    cmds = build_commands_from_ledger(ledger)

    assert not any("<<" in c and "\n" not in c for c in cmds), (
        f"single-line heredoc-open must be dropped even with mutation_class set; cmds={cmds}"
    )
    assert "pip install -e ." in cmds, f"real install must survive; cmds={cmds}"


def test_bit_shift_redirect_is_not_treated_as_heredoc():
    """`<<` as an arithmetic bit-shift (delimiter token is a digit) is NOT a heredoc.

    Distinguishes the precise heredoc-operator regex from a naive `'<<' in cmd`
    substring check, which would wrongly drop this legitimate single-line file write.
    """
    cmd = "echo $((1 << 4)) > /app/n.txt"
    assert not _is_unterminated_heredoc(cmd), (
        f"bit-shift must not be detected as a heredoc; cmd={repr(cmd)}"
    )
    assert _is_source_file_edit(cmd), (
        f"a real single-line redirect with a bit-shift must still be kept; cmd={repr(cmd)}"
    )


def test_terminated_heredoc_body_survives_into_recipe():
    """A1 proxy_pool repro: a terminated heredoc extracted by the build agent must
    flow through to the recipe intact — one command, body + terminator preserved.

    Before A1, _extract_worker_action applied .splitlines()[0] and recorded only the
    opener (`cat > /app/pyproject.toml << 'EOF'`), which build_commands_from_ledger
    then DROPS as an unterminated heredoc — bucket-C loss. With A1 the full heredoc is
    recorded and kept verbatim.
    """
    from src.envstate.build_agent import _extract_worker_action

    content = (
        "Thought: write the project file\n"
        "Action: ```bash\n"
        "cat > /app/pyproject.toml << 'EOF'\n"
        "[project]\n"
        'name = "proxy_pool"\n'
        "EOF\n"
        "```"
    )
    action = _extract_worker_action(content)
    # Extraction must preserve the full heredoc, not the truncated opener.
    assert "[project]" in action and "EOF" in action, (
        f"extraction lost the heredoc body/terminator; action={repr(action)}"
    )

    ledger = ActionLedger()
    # Same mutation_class the file's other file-write tests use.
    ledger.append(_event(1, action, 0, "file_or_env_change"))

    cmds = build_commands_from_ledger(ledger)

    assert len(cmds) == 1, f"expected exactly one command (no greedy accumulation); cmds={cmds}"
    assert "[project]" in cmds[0], f"heredoc body lost on the way to the recipe; cmds={cmds}"
    assert "EOF" in cmds[0], f"heredoc terminator lost on the way to the recipe; cmds={cmds}"


def test_missing_terminator_heredoc_dropped_from_recipe():
    """A1 review repro: when the model never closes a plain heredoc (it ran on into
    Thought/Action text), _extract_worker_action must yield the bare single-line
    opener, which build_commands_from_ledger then DROPS — proving the Bug-2 "no-op
    recipe" regression is closed.

    Without the A1-review fix, _reconstruct_plain_heredoc swallowed every trailing
    line into a multi-line body; that body HAS a newline, so _is_unterminated_heredoc
    returned False and the unterminated heredoc survived into the recipe as a RUN whose
    eval-time parser greedily consumes the following RUNs.
    """
    from src.envstate.build_agent import _extract_worker_action

    content = (
        "Action: cat > /app/p.toml << 'EOF'\n"
        "[project]\n"
        'name = "x"\n'
        "Thought: done writing\n"
        "Action: ls -la\n"
    )
    action = _extract_worker_action(content)
    # The opener alone, single line — nothing swallowed.
    assert action == "cat > /app/p.toml << 'EOF'", f"unexpected action={action!r}"

    ledger = ActionLedger()
    # rc==0; use the same mutation_class classify_mutation would tag a `cat > f <<` with.
    ledger.append(_event(1, action, 0, "file_or_env_change"))
    ledger.append(_event(2, "pip install -e .", 0, "language_package_install"))

    cmds = build_commands_from_ledger(ledger)

    # The unterminated heredoc-open must be DROPPED from the recipe.
    assert action not in cmds, f"unterminated heredoc-open must be dropped; cmds={cmds}"
    assert not any("<<" in c for c in cmds), (
        f"no heredoc operator may reach the recipe; cmds={cmds}"
    )
    # The real install must survive.
    assert "pip install -e ." in cmds, f"real install must survive; cmds={cmds}"


def test_is_unterminated_heredoc_helper_matrix():
    """Direct coverage of the helper's contract."""
    # Broken single-line openers -> True (unterminated)
    assert _is_unterminated_heredoc("cat > /tmp/x.py << 'EOF'")
    assert _is_unterminated_heredoc("cat <<-PYEOF > /app/p.toml")
    assert _is_unterminated_heredoc('tee f << "DELIM"')
    # Terminated multi-line heredoc (has body + terminator) -> False (keep it)
    assert not _is_unterminated_heredoc("cat > f << 'EOF'\nbody\nEOF")
    # No heredoc operator at all -> False
    assert not _is_unterminated_heredoc("printf 'x' > /app/a.py")
    assert not _is_unterminated_heredoc("pip install -e .")
    assert not _is_unterminated_heredoc("")
