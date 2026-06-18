"""L2 transport retry: complete_with_retry must retry transient LLM transport
failures (timeout / connection / 5xx) with backoff, fail fast on fatal errors,
and never hang — the deepseek-v4-flash stall that froze the e2e runs."""
import httpx
import pytest
from openai import APIConnectionError, APITimeoutError

import src.envstate.llm_response as L
from src.envstate.llm_response import complete_with_retry


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Usage:
    prompt_tokens = 7
    completion_tokens = 3
    total_tokens = 10


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


class _Client:
    """chat.completions.create replays a script: a BaseException is raised, a str
    is returned wrapped as a completion response."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        outer = self

        class _Completions:
            def create(self, **kw):
                item = outer._script[outer.calls]
                outer.calls += 1
                if isinstance(item, BaseException):
                    raise item
                return _Resp(item)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _timeout():
    return APITimeoutError(request=httpx.Request("POST", "http://x"))


def _conn():
    return APIConnectionError(request=httpx.Request("POST", "http://x"))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(L, "_sleep_backoff", lambda *a, **k: None)


def _call(client, **kw):
    return complete_with_retry(client, "m", [{"role": "user", "content": "hi"}], **kw)


def test_retries_on_timeout_then_succeeds():
    c = _Client([_timeout(), '{"ok": true}'])
    text, _, _ = _call(c)
    assert text == '{"ok": true}'
    assert c.calls == 2


def test_recovers_after_multiple_transient():
    c = _Client([_timeout(), _conn(), "good"])
    text, _, _ = _call(c)
    assert text == "good"
    assert c.calls == 3


def test_transport_exhaustion_returns_empty_not_raise():
    c = _Client([_timeout()] * 3)
    text, _, resp = _call(c, max_attempts=1, max_transport_attempts=3)
    assert text == ""           # empty result, not an exception
    assert resp is None
    assert c.calls == 3


def test_fatal_exception_propagates():
    c = _Client([ValueError("boom")])
    with pytest.raises(ValueError):
        _call(c)
    assert c.calls == 1         # no retry on a fatal/unknown error


def test_success_first_try_no_retry():
    c = _Client(["good"])
    text, _, _ = _call(c)
    assert text == "good"
    assert c.calls == 1


def test_content_nudge_retry_still_works():
    # whitespace-only first reply is not "good" → existing content-nudge retry path
    c = _Client(["   ", "real"])
    text, _, _ = _call(c, max_attempts=2)
    assert text == "real"
    assert c.calls == 2
