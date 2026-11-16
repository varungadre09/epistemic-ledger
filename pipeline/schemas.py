"""Structured contracts for extraction and resolution. Pydantic models.

These are the LLM I/O schemas. The extraction model returns ExtractionResult
(via client.messages.parse output_format). The resolution model returns a verdict
by calling the strict `record_verdict` tool whose JSON schema is RECORD_VERDICT_TOOL.
"""
from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# ----------------------------- extraction -----------------------------
class ExtractedClaim(BaseModel):
    text: str = Field(description="The single falsifiable claim/prediction, normalized.")
    is_falsifiable: bool
    # Path chosen from the provided canonical taxonomy. If none fits, leave empty
    # and set proposed_path + proposed_parent so canonicalization can run.
    topic_slug: Optional[str] = Field(
        default=None, description="Existing canonical slug, e.g. 'finance>us-equities>earnings'.")
    proposed_path: Optional[str] = Field(
        default=None, description="Proposed NEW slug if no existing path fits.")
    proposed_parent: Optional[str] = Field(
        default=None, description="Existing canonical slug the proposal should sit under.")
    stated_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    resolves_on: Optional[str] = Field(default=None, description="YYYY-MM-DD when it can be judged.")
    edge_flags: List[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """A tweet may contain zero or several falsifiable claims."""
    has_claim: bool
    claims: List[ExtractedClaim] = Field(default_factory=list)


# ----------------------------- resolution -----------------------------
# Strict tool the resolver MUST call once it has searched. tool_choice stays
# 'auto' (forced choice 400s on Opus 5.5); the prompt instructs the model to call
# it. evidence_urls must come from web_search results — validated in resolve.py.
RECORD_VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Record the final verdict for the claim after researching the outcome.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["true", "false", "unresolvable"]},
            "evidence_urls": {
                "type": "array", "items": {"type": "string"},
                "description": "URLs from web_search that establish the outcome. "
                               "Required (>=1) for true/false; [] for unresolvable.",
            },
            "reasoning": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["verdict", "evidence_urls", "reasoning", "confidence"],
        "additionalProperties": False,
    },
}


class Verdict(BaseModel):
    verdict: Literal["true", "false", "unresolvable"]
    evidence_urls: List[str] = Field(default_factory=list)
    reasoning: str = ""
    confidence: float = 0.0
    # usage accounting (populated by resolve.py; for cost calibration)
    input_tokens: int = 0
    output_tokens: int = 0
    web_searches: int = 0
