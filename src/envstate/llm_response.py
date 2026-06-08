from __future__ import annotations
import re
from typing import Any, Callable, Optional


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


_DEFAULT_RETRY_NUDGE = (
    "Your previous response was empty or invalid. "
    "Respond now in the exact required format."
)


def complete_with_retry(
    client: Any,
    model: str,
    messages: list[dict],
    accept: Optional[Callable[[str], Any]] = None,
    max_attempts: int = 3,
    retry_nudge: Optional[str] = None,
    **kwargs: Any,
) -> tuple[str, dict, Any]:
    """Call ``client.chat.completions.create`` with retry on empty/unacceptable responses.

    Attempts up to *max_attempts* times.  A response is "good" iff its text
    (via :func:`response_text`) is non-empty AND ``accept(text)`` is truthy
    (or *accept* is ``None``).  On each failed attempt a corrective user
    message is appended to a **copy** of *messages* (the caller's list is
    never mutated).  Usage counters are accumulated across all attempts.

    After *max_attempts* the last (text, usage, response) is returned
    unconditionally.  Exceptions from ``create`` propagate unchanged.
    If ``accept`` raises, that attempt is treated as not-good and retried.

    :param client: OpenAI-compatible client with ``chat.completions.create``.
    :param model: Model identifier forwarded to every ``create`` call.
    :param messages: Initial message list.  Never mutated.
    :param accept: Optional predicate; if provided, text must also satisfy it
        to be considered good.  Exceptions from ``accept`` are suppressed and
        treated as a not-good result.
    :param max_attempts: Maximum total ``create`` calls (default 3).
    :param retry_nudge: Content of the corrective user message appended on
        retries.  Defaults to a generic "respond in the required format" nudge.
    :param kwargs: Additional keyword arguments forwarded verbatim to every
        ``create`` call (e.g. ``temperature=0``).
    :return: ``(text, usage, response)`` where *usage* has keys
        ``input_tokens``, ``output_tokens``, ``total_tokens`` accumulated
        across all attempts, and *response* is the final raw completion object
        (suitable for passing to :func:`log_llm_exchange`).
    """
    nudge = retry_nudge if retry_nudge is not None else _DEFAULT_RETRY_NUDGE
    accumulated = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # Working copy of messages; grows with nudge entries on retries.
    current_messages = list(messages)
    last_text = ""
    last_response: Any = None

    for attempt in range(max_attempts):
        response = client.chat.completions.create(
            model=model, messages=current_messages, **kwargs
        )
        text = response_text(response)

        # Accumulate usage (getattr-safe, mirrors maintainer.py pattern).
        usage_obj = getattr(response, "usage", None)
        accumulated["input_tokens"] += getattr(usage_obj, "prompt_tokens", 0) or 0
        accumulated["output_tokens"] += getattr(usage_obj, "completion_tokens", 0) or 0
        accumulated["total_tokens"] += getattr(usage_obj, "total_tokens", 0) or 0

        last_text = text
        last_response = response

        try:
            accept_ok = accept is None or bool(accept(text))
        except Exception:
            accept_ok = False
        good = bool(text.strip()) and accept_ok
        if good:
            return text, accumulated, response

        if attempt < max_attempts - 1:
            # Append nudge to a copy so the caller list stays unmutated.
            current_messages = current_messages + [{"role": "user", "content": nudge}]

    return last_text, accumulated, last_response
