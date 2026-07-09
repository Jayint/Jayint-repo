"""Tests for complete_with_tools in src/envstate/llm_response.py — the native tool-calling call
path used by the react arm. Fakes use SimpleNamespace, mirroring test_complete_with_retry.py."""
from types import SimpleNamespace


def _tool_response(name, arguments, content="", pt=10, ct=5, tt=15):
    fn = SimpleNamespace(name=name, arguments=arguments)
    tc = SimpleNamespace(function=fn, id="call_1", type="function")
    msg = SimpleNamespace(content=content, tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt))


def _notool_response(content="just prose", pt=10, ct=5, tt=15):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt))


def _client(responses):
    call_log = []
    it = iter(responses)

    def _create(*, model, messages, **kwargs):
        call_log.append({"messages": list(messages), "kwargs": kwargs})
        return next(it)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    return client, call_log


def test_returns_tool_call_on_first_attempt():
    from src.envstate.llm_response import complete_with_tools
    client, log = _client([_tool_response("edit", '{"verb":"insert","start":8,"content":"x"}')])
    calls, content, usage, resp = complete_with_tools(client, "m", [{"role": "user", "content": "go"}], tools=[{"t": 1}])
    assert calls == [("edit", '{"verb":"insert","start":8,"content":"x"}')]
    assert len(log) == 1


def test_forwards_tools_and_forces_tool_choice_required():
    from src.envstate.llm_response import complete_with_tools
    client, log = _client([_tool_response("explore", '{"command":"ls"}')])
    TOOLS = [{"type": "function", "function": {"name": "explore"}}]
    complete_with_tools(client, "m", [{"role": "user", "content": "go"}], tools=TOOLS)
    assert log[0]["kwargs"]["tools"] == TOOLS
    assert log[0]["kwargs"]["tool_choice"] == "required"


def test_retries_with_nudge_when_no_tool_call():
    from src.envstate.llm_response import complete_with_tools
    client, log = _client([_notool_response("I'll investigate…"),
                           _tool_response("explore", '{"command":"ls"}')])
    calls, content, usage, resp = complete_with_tools(client, "m", [{"role": "user", "content": "go"}], tools=[{"t": 1}])
    assert calls == [("explore", '{"command":"ls"}')]
    assert len(log) == 2                                   # retried once
    assert log[1]["messages"][-1]["role"] == "user"        # a corrective nudge was appended


def test_accumulates_usage_across_attempts():
    from src.envstate.llm_response import complete_with_tools
    client, _ = _client([_notool_response("prose", 10, 5, 15),
                         _tool_response("explore", '{"command":"ls"}', pt=20, ct=8, tt=28)])
    _, _, usage, _ = complete_with_tools(client, "m", [{"role": "user", "content": "go"}], tools=[{"t": 1}])
    assert usage["input_tokens"] == 30 and usage["total_tokens"] == 43


def test_captures_content_alongside_tool_call():
    from src.envstate.llm_response import complete_with_tools
    client, _ = _client([_tool_response("explore", '{"command":"ls"}', content="<think>look</think>")])
    _, content, _, _ = complete_with_tools(client, "m", [{"role": "user", "content": "go"}], tools=[{"t": 1}])
    assert content == "<think>look</think>"
