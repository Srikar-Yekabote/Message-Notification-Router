"""search_history: find past messages in the recipient's own history, with their reactions.

No user has more than about 30 past messages, so plain keyword matching over the
text (plus the cached image summary or transcript) is enough; no embeddings needed.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from context import ContextStore
from context.records import Message

from .base import ToolContext, ToolInputError, clip, media_text, past_message

SCOPES = ("same_sender", "same_group", "all")
DEFAULT_LIMIT = 5
MAX_LIMIT = 8
STOPWORDS = frozenset(
    "the and for are was were been this that with from you your our not but can will has have had "
    "its all any per via into than then them they their there here please just also".split()
)
_WORD = re.compile(r"[a-z0-9]+")


def search_history(ctx: ToolContext, query: str = "", scope: str = "same_sender", limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """Keyword search within one scope of the recipient's history; an empty query lists the most recent."""
    m = ctx.message
    if scope not in SCOPES:
        raise ToolInputError(f"scope must be one of {', '.join(SCOPES)}")
    if scope == "same_group" and not m.group_id:
        raise ToolInputError("same_group only works for group messages; use same_sender or all")
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolInputError(f"limit must be between 1 and {MAX_LIMIT}")

    if scope == "same_sender":
        pool = ctx.history(sender_key=m.sender_key)
    elif scope == "same_group":
        pool = ctx.history(group_id=m.group_id)
    else:
        pool = ctx.history()

    terms = _terms(query)
    if terms:
        scored = []
        for p in pool:
            hits = sorted(terms & _terms(_searchable(p, ctx)))
            if hits:
                scored.append((len(hits), p.created_at, p, hits))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        picked = [(p, hits) for _, _, p, hits in scored]
    else:
        picked = [(p, []) for p in reversed(pool)]

    results = []
    for p, hits in picked[:limit]:
        r = past_message(p, ctx)
        if scope != "same_sender":
            r["same_sender"] = p.sender_key == m.sender_key
        if hits:
            r["matched_terms"] = hits
        results.append(r)
    return {
        "scope": scope,
        "query": clip(query, 100),
        "messages_in_scope": len(pool),
        "matching": len(picked),
        "user_reactions_in_scope": ContextStore.reaction_summary(pool),
        "results": results,
    }


def _searchable(m: Message, ctx: ToolContext) -> str:
    parts: List[str] = [m.text, media_text(ctx.media_entry(m.media_id))]
    if m.business_id in ctx.store.businesses:
        b = ctx.store.businesses[m.business_id]
        parts += [b.brand_name, b.display_name, b.category]
    return " ".join(parts)


def _terms(text: str) -> Set[str]:
    """Lower-case words of three or more letters, minus stopwords, with a plural 's' dropped."""
    terms = set()
    for word in _WORD.findall(text.lower()):
        if len(word) < 3 or word in STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        terms.add(word)
    return terms
