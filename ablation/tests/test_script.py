from __future__ import annotations

from ablation.models import FlatBlock, FlatPlan
from ablation.script import locate_failed_block, render_plan


def sample_plan() -> FlatPlan:
    return FlatPlan(
        (
            FlatBlock("b01", ("echo prepare",), (), ("host.base_image",)),
            FlatBlock(
                "b02",
                ("echo same", "false"),
                (),
                ("file:pyproject.toml",),
            ),
        )
    )


def test_renderer_is_deterministic_and_contains_no_graph_metadata():
    first = render_plan(sample_plan())
    second = render_plan(sample_plan())
    assert first == second
    assert first.text.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail\n")
    assert first.text.count("#@block id=") == 2
    assert "target_node" not in first.text
    assert "provider" not in first.text
    assert "wave=" not in first.text
    assert first.commands[-1].command == "false"


def test_localizer_prefers_line_number_and_disambiguates_duplicate_command():
    plan = FlatPlan(
        (
            FlatBlock("b01", ("echo same",), (), ("host.base_image",)),
            FlatBlock("b02", ("echo same",), (), ("host.base_image",)),
        )
    )
    rendered = render_plan(plan)
    second_line = rendered.commands[1].line
    assert locate_failed_block(
        rendered,
        output="",
        failing_command="echo same",
        lineno=second_line,
    ) == "b02"


def test_localizer_uses_marker_then_unique_command_and_never_guesses():
    rendered = render_plan(sample_plan())
    assert locate_failed_block(
        rendered,
        output="__ABLATION_BLOCK__:b02\nboom",
        failing_command=None,
        lineno=None,
    ) == "b02"
    assert locate_failed_block(
        rendered,
        output="boom",
        failing_command="false",
        lineno=None,
    ) == "b02"
    assert locate_failed_block(
        rendered,
        output="boom",
        failing_command="unknown",
        lineno=99_999,
    ) is None
