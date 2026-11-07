"""Small helpers shared by the Claude calls in ranking, synthesis and enrich."""

from __future__ import annotations

import json
import os
import time

from .config import MODEL, ROOT

# $ per million tokens (input, output). Unknown models log tokens only.
PRICES_PER_MTOK = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
}
# One JSON line per Claude call; gitignored with the rest of .cache/.
USAGE_LOG = ROOT / ".cache" / "usage.jsonl"

# Tokens allowed on top of the prompt's byte length for the system prompt,
# JSON schema and message framing when reserving a call's worst case.
PROMPT_OVERHEAD_TOKENS = 4000


class BudgetExceeded(RuntimeError):
    """A Claude call was refused because it could push spend past the cap."""


# One `gttp build` process is one job, so the spend accumulator lives at
# module level. `_tripped` is sticky: once a call is refused, every later
# call in the process is refused too.
_spent_usd = 0.0
_tripped = False


def reset_budget() -> None:
    """Forget this process's spend and trip state (tests)."""
    global _spent_usd, _tripped
    _spent_usd = 0.0
    _tripped = False


def budget_limit() -> float | None:
    """The hard per-process cap from GTTP_MAX_BUDGET_USD, or None when unset
    (manual CLI use has no guard). An invalid value returns NaN, which
    check_budget treats as "refuse everything" (fail closed)."""
    raw = os.environ.get("GTTP_MAX_BUDGET_USD", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return float("nan")
    return value if value > 0 else float("nan")


def _job_id() -> str | None:
    return os.environ.get("GTTP_JOB_ID") or None


def _log(line: dict) -> None:
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_LOG.open("a") as f:
        f.write(json.dumps(line) + "\n")


def worst_case_usd(prompt: str, max_tokens: int, model: str = MODEL) -> float | None:
    """The most a call can cost: every input token is at least one byte
    (byte-level BPE) plus a fixed overhead allowance, and output (thinking
    included) can't exceed max_tokens. None for a model with no known price."""
    price = PRICES_PER_MTOK.get(model)
    if not price:
        return None
    tokens_in = len(prompt.encode("utf-8")) + PROMPT_OVERHEAD_TOKENS
    return (tokens_in * price[0] + max_tokens * price[1]) / 1e6


def check_budget(
    prompt: str, max_tokens: int, step: str, book: str, model: str = MODEL
) -> None:
    """Refuse a Claude call (raise BudgetExceeded) if its worst-case cost
    could push this process past GTTP_MAX_BUDGET_USD. Call it immediately
    before `messages.create`. A no-op when no cap is set."""
    global _tripped
    limit = budget_limit()
    if limit is None:
        return
    if limit != limit:  # NaN: invalid env value
        _tripped = True
        raise BudgetExceeded("invalid GTTP_MAX_BUDGET_USD")
    if _tripped:
        raise BudgetExceeded(f"budget already exhausted; refused {step}")
    worst = worst_case_usd(prompt, max_tokens, model)
    if worst is None:
        _tripped = True
        raise BudgetExceeded(f"no price known for model {model!r}; refused {step}")
    if _spent_usd + worst > limit:
        _tripped = True
        print(
            f"    BUDGET: refused {step} (spent ${_spent_usd:.3f} + worst case "
            f"${worst:.3f} > cap ${limit:.2f})"
        )
        try:
            _log({
                "t": int(time.time()), "book": book, "step": "budget_refused",
                "refused_step": step, "spent": round(_spent_usd, 6),
                "worst": round(worst, 6), "limit": limit, "usd": 0, "job": _job_id(),
            })
        except Exception as exc:  # pragma: no cover - defensive
            print(f"    (budget refusal not recorded: {exc})")
        raise BudgetExceeded(
            f"refused {step}: spent ${_spent_usd:.3f} + worst case ${worst:.3f} "
            f"> cap ${limit:.2f}"
        )


def record_usage(resp, step: str, book: str) -> None:
    """Print and log one call's token usage and estimated cost, and add it to
    this process's budget. Never raises: accounting must not break a build."""
    global _spent_usd, _tripped
    try:
        usage = getattr(resp, "usage", None)
        if usage is None:
            if budget_limit() is not None:
                _tripped = True  # spend unknown under a cap: fail closed
            return
        tin = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        tcache = getattr(usage, "cache_read_input_tokens", 0) or 0
        tout = usage.output_tokens or 0
        model = getattr(resp, "model", "")
        price = PRICES_PER_MTOK.get(model)
        cost = None
        if price:
            exact = (tin * price[0] + tcache * price[0] * 0.1 + tout * price[1]) / 1e6
            _spent_usd += exact
            cost = round(exact, 4)
        elif budget_limit() is not None:
            _tripped = True  # spend unknown under a cap: fail closed
        shown = f" ~${cost:.3f}" if cost is not None else ""
        print(f"    {step}: {tin + tcache} in / {tout} out tokens{shown}")
        _log({
            "t": int(time.time()), "book": book, "step": step, "model": model,
            "input": tin, "cache_read": tcache, "output": tout, "usd": cost,
            "job": _job_id(),
        })
    except Exception as exc:  # pragma: no cover - defensive
        if budget_limit() is not None:
            _tripped = True
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
