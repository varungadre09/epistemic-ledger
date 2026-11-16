"""Resolution: judge whether a claim came true, with web retrieval + citations.

This is the make-or-break step (user pt 4). The model searches the web, then calls
the strict `record_verdict` tool. We then VALIDATE that every evidence URL actually
appeared in a web_search result — a verdict whose citations weren't really retrieved
is downgraded to 'unresolvable' (anti-hallucination).

Time-scoping guards against lookahead: the model is told today's date and the claim
window, and is instructed to return 'unresolvable' when the outcome is not yet
determinable, and not to treat the author's own posts as evidence.

Scaffold only — not executed.
"""
from __future__ import annotations
from datetime import date
from typing import List, Tuple
import anthropic

from config import RESOLVE_MODEL, WEB_SEARCH_TOOL
from schemas import RECORD_VERDICT_TOOL, Verdict

SYSTEM = """You determine whether a past prediction came true, using web search.
Procedure:
1. Search for authoritative evidence of the actual outcome.
2. Judge strictly against what the claim asserted and its resolution date.
3. Call record_verdict exactly once with your verdict and the evidence URLs you used.

Hard rules:
- Today's date is {today}. If the outcome is not yet determinable (resolution date in
  the future, event hasn't happened, or no authoritative source found), verdict =
  'unresolvable' with evidence_urls = [].
- Do NOT use the author's own posts/account as evidence.
- evidence_urls MUST be URLs returned by web_search. Do not invent URLs.
- 'true'/'false' require at least one evidence URL."""


def _collect_search_urls(content) -> set:
    """Pull every URL the web_search server tool actually returned this turn."""
    urls = set()
    for block in content:
        if getattr(block, "type", None) == "web_search_tool_result":
            results = getattr(block, "content", None)
            if isinstance(results, list):  # success -> list; error -> object
                for r in results:
                    u = getattr(r, "url", None)
                    if u:
                        urls.add(u)
    return urls


def resolve_claim(client: anthropic.Anthropic, claim_text: str,
                  posted_at: str, resolves_on: str,
                  today: str | None = None) -> Verdict:
    today = today or date.today().isoformat()
    system = SYSTEM.format(today=today)
    user = (f"CLAIM: {claim_text}\n"
            f"Posted: {posted_at}\nResolves on: {resolves_on}\n\n"
            f"Research the outcome and call record_verdict.")
    messages = [{"role": "user", "content": user}]
    tools = [WEB_SEARCH_TOOL, RECORD_VERDICT_TOOL]

    # usage accumulators across the bounded loop (for cost calibration)
    in_tok = out_tok = n_search = 0

    def stamp(v: Verdict) -> Verdict:
        v.input_tokens, v.output_tokens, v.web_searches = in_tok, out_tok, n_search
        return v

    searched_urls: set = set()
    for _ in range(8):  # bounded loop
        resp = client.messages.create(
            model=RESOLVE_MODEL, max_tokens=8000,
            output_config={"effort": "high"},
            system=system, messages=messages, tools=tools,
        )
        u = getattr(resp, "usage", None)
        if u is not None:
            in_tok += getattr(u, "input_tokens", 0) or 0
            out_tok += getattr(u, "output_tokens", 0) or 0
            stu = getattr(u, "server_tool_use", None)
            if stu is not None:
                n_search += getattr(stu, "web_search_requests", 0) or 0
        searched_urls |= _collect_search_urls(resp.content)

        if resp.stop_reason == "pause_turn":          # server tool still working
            messages.append({"role": "assistant", "content": resp.content})
            continue

        verdict_block = next((b for b in resp.content
                              if getattr(b, "type", None) == "tool_use"
                              and b.name == "record_verdict"), None)
        if verdict_block is None:
            # Model ended without recording — treat as unresolvable.
            if resp.stop_reason == "end_turn":
                return stamp(Verdict(verdict="unresolvable",
                                     reasoning="Model ended without a verdict."))
            messages.append({"role": "assistant", "content": resp.content})
            continue

        v = Verdict(**verdict_block.input)
        # ---- citation validation (anti-hallucination) ----
        if v.verdict in ("true", "false"):
            cited = [u for u in v.evidence_urls if u in searched_urls]
            if not cited:
                return stamp(Verdict(verdict="unresolvable",
                             reasoning="Cited evidence not found in search results; "
                                       "downgraded. Original: " + v.reasoning[:300],
                             confidence=0.0))
            v.evidence_urls = cited
        return stamp(v)

    return stamp(Verdict(verdict="unresolvable", reasoning="Resolution loop exhausted."))
