"""Claim extraction: tweet text + canonical taxonomy -> structured claims.

Constraint (user pt 2): the extraction model sees ONLY the tweet text and the
taxonomy. It does NOT judge truth here — resolution is a separate step.

Scaffold only — not executed.
"""
from __future__ import annotations
from typing import List
import anthropic

from config import EXTRACT_MODEL
from schemas import ExtractionResult, ExtractedClaim

SYSTEM = """You extract falsifiable claims/predictions from a single social-media post.
Rules:
- Only extract statements that are FALSIFIABLE (could later be judged true/false
  against real-world outcomes). Opinions, vibes, and unfalsifiable talk -> skip.
- Do NOT judge whether the claim is true. That is done elsewhere.
- Classify each claim into the provided canonical taxonomy. Reuse an existing slug
  whenever one reasonably fits (prefer reuse over precision).
- Only if NO existing path fits, leave topic_slug empty and give proposed_path plus
  the existing proposed_parent it should sit under.
- stated_confidence: the author's own hedging as a 0-1 number if expressed, else null.
- resolves_on: the date the claim can first be judged, if stated or clearly implied."""


def build_user_prompt(tweet_text: str, taxonomy_slugs: List[str]) -> str:
    tax = "\n".join(f"- {s}" for s in taxonomy_slugs)
    return (f"CANONICAL TAXONOMY (reuse these slugs):\n{tax}\n\n"
            f"POST:\n{tweet_text}")


def extract_claims(client: anthropic.Anthropic, tweet_text: str,
                   taxonomy_slugs: List[str]) -> List[ExtractedClaim]:
    """Return the falsifiable claims found in one tweet (possibly empty)."""
    resp = client.messages.parse(
        model=EXTRACT_MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},  # bulk classification
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": build_user_prompt(tweet_text, taxonomy_slugs)}],
        output_format=ExtractionResult,
    )
    result = resp.parsed_output
    return result.claims if (result and result.has_claim) else []
