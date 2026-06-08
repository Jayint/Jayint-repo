from __future__ import annotations
from typing import Any


def response_text(response: Any) -> str:
    """Return the assistant message text from an OpenAI-compatible chat completion.

    Prefers the standard ``choices[0].message.content``. Reasoning models such as
    MiniMax via OpenRouter often return ``content=None``/empty and put the actual
    text in a separate ``reasoning`` field (either as a message attribute or under
    ``model_extra``); this helper falls back to that field so the parser still sees
    the action. Returns ``""`` if no usable text is found or the response shape is
    malformed.

    :param response: An OpenAI-compatible chat completion object (or any object
        that does not match the expected shape).
    :return: The non-empty assistant text, the reasoning fallback, or ``""``.
    """
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError, KeyError):
        return ""

    content = getattr(message, "content", None)
    if content:
        return content

    reasoning = getattr(message, "reasoning", None)
    if reasoning:
        return reasoning

    extra = getattr(message, "model_extra", None) or {}
    return extra.get("reasoning") or ""
