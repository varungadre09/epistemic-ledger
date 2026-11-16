"""Tiny, dependency-free helper shared across the pipeline.

Kept separate so the read API (answer/analyze) doesn't import run_seed, which pulls
in pandas — the hosted API then needs only anthropic + psycopg + pydantic.
"""
from __future__ import annotations
import re


def slug_to_ltree(slug: str) -> str:
    """Sanitize a '>'-delimited slug into a valid ltree path.
    ltree labels allow [A-Za-z0-9_] only; '>' -> '.', other chars -> '_'."""
    labels = []
    for part in str(slug).split(">"):
        lab = re.sub(r"[^a-z0-9]+", "_", part.strip().lower()).strip("_")
        if lab:
            labels.append(lab)
    return ".".join(labels)
