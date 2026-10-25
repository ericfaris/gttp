"""Small helpers shared by the Claude calls in ranking, synthesis and enrich."""

from __future__ import annotations


def response_text(resp) -> str:
    """The text of a structured-output response, or a clear error.

    A refusal or a truncated response has no usable JSON; raising with the
    stop reason (instead of a bare StopIteration or JSON error) lets callers
    log why they fell back.
    """
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise RuntimeError(f"Claude declined the request (category: {category})")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Claude hit max_tokens before finishing the JSON")
    for block in resp.content:
        if block.type == "text":
            return block.text
    raise RuntimeError(f"no text in Claude response (stop_reason={resp.stop_reason})")
