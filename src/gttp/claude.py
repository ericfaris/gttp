"""Small helpers shared by the Claude calls in ranking, synthesis and enrich."""

from __future__ import annotations

import json
import time

from .config import ROOT

# $ per million tokens (input, output). Unknown models log tokens only.
PRICES_PER_MTOK = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
}
# One JSON line per Claude call; gitignored with the rest of .cache/.
USAGE_LOG = ROOT / ".cache" / "usage.jsonl"


def record_usage(resp, step: str, book: str) -> None:
    """Print and log one call's token usage and estimated cost. Never raises:
    accounting must not break a build."""
    try:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        tin = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        tcache = getattr(usage, "cache_read_input_tokens", 0) or 0
        tout = usage.output_tokens or 0
        model = getattr(resp, "model", "")
        price = PRICES_PER_MTOK.get(model)
        cost = None
        if price:
            cost = round((tin * price[0] + tcache * price[0] * 0.1 + tout * price[1]) / 1e6, 4)
        shown = f" ~${cost:.3f}" if cost is not None else ""
        print(f"    {step}: {tin + tcache} in / {tout} out tokens{shown}")
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a") as f:
            f.write(json.dumps({
                "t": int(time.time()), "book": book, "step": step, "model": model,
                "input": tin, "cache_read": tcache, "output": tout, "usd": cost,
            }) + "\n")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"    (usage not recorded: {exc})")


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
