from __future__ import annotations
import re
from typing import Any


def strip_reasoning_markup(text: str | None) -> str:
    """Remove ``<think>...</think>`` reasoning markup leaked into message content.

    Handles two cases produced by reasoning models (e.g. MiniMax M2.7 via
    OpenRouter):

    * **Complete blocks** – ``<think>...</think>`` anywhere in the text
      (including multiple occurrences, ``re.DOTALL``).
    * **Leading orphan fragment** – the opening ``<think>`` was placed in the
      separate ``reasoning`` field so only the tail (ending in ``</think>``)
      appears at the start of ``content``.  Strip from position 0 through the
      first ``</think>`` when no preceding ``<think>`` exists in that prefix.

    The function is idempotent; returns ``""`` for ``None`` or empty input;
    leaves clean text (no think markup) byte-identical.

    :param text: Raw content string, potentially containing think markup.
    :return: Cleaned string with all think markup removed and outer whitespace
        stripped.
    """
    if not text:
        return ""

    # Remove complete <think>...</think> blocks (non-greedy so each block is
    # matched independently; re.DOTALL).
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # Remove a leading orphan fragment that ends at the first </think> when
    # there is no matching opening <think> before it.  Only strip when ALL of:
    #   1. No earlier <think> in the prefix (not a matched pair).
    #   2. The </think> tag is on its own line — the prefix ends with a newline
    #      or is entirely whitespace.  This prevents eating a real answer like
    #      "Answer with </think> rest" where </think> is mid-sentence.
    #   3. The prefix contains no Thought:/Action:/Final Answer: directive,
    #      preventing "Action: ls\n</think>\nmore" from losing the Action line.
    close_match = re.search(r"</think>", cleaned)
    if close_match:
        prefix = cleaned[: close_match.start()]
        _prefix_ends_at_line_start = not prefix or prefix[-1] == "\n" or not prefix.strip()
        _no_directive = not re.search(
            r"^\s*(Thought:|Action:|Final Answer:)", prefix, re.MULTILINE
        )
        if "<think>" not in prefix and _prefix_ends_at_line_start and _no_directive:
            cleaned = cleaned[close_match.end():]

    return cleaned.strip()


def response_text(response: Any) -> str:
    """Return the assistant message text from an OpenAI-compatible chat completion.

    Prefers the standard ``choices[0].message.content``. Reasoning models such as
    MiniMax via OpenRouter often return ``content=None``/empty and put the actual
    text in a separate ``reasoning`` field (either as a message attribute or under
    ``model_extra``); this helper falls back to that field so the parser still sees
    the action. Returns ``""`` if no usable text is found or the response shape is
    malformed.

    ``strip_reasoning_markup`` is applied to ``content`` before the non-empty
    check so that a content field consisting solely of a ``<think>`` fragment
    correctly falls through to the reasoning fallback.

    :param response: An OpenAI-compatible chat completion object (or any object
        that does not match the expected shape).
    :return: The non-empty assistant text, the reasoning fallback, or ``""``.
    """
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError, KeyError):
        return ""

    content = getattr(message, "content", None)
    clean = strip_reasoning_markup(content)
    if clean:
        return clean

    reasoning = getattr(message, "reasoning", None)
    if reasoning:
        return strip_reasoning_markup(reasoning) or reasoning

    extra = getattr(message, "model_extra", None) or {}
    return extra.get("reasoning") or ""
