"""safety_scan: risk-related facts from the message text, any image text and any transcript.

The scan reports what it finds, such as "links to amazonpay-delivery.in, which is not
the sender's official domain amazon.in" or a snippet that mentions an OTP, and leaves
the judgement to the agent. A bank's anti-scam poster mentions OTPs too.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from llm import GroqError

from .base import ToolContext, clip, untrusted
from .profiles import business_facts

SNIPPET_CONTEXT, SNIPPET_LIMIT = 50, 130
MAX_SNIPPETS_PER_KIND, MAX_PHRASES = 2, 10
GUARD_TEXT_LIMIT = 1500

# Generic public link shorteners: the real destination is hidden behind them.
SHORTENERS = frozenset("bit.ly tinyurl.com t.co goo.gl ow.ly is.gd buff.ly cutt.ly rb.gy t.ly shorturl.at tiny.cc".split())
COMMON_TLDS = frozenset(
    "com net org info biz app io co xyz online site top club shop store live pro me tv ly gl cc link click page dev ai bank".split()
)
_DOMAIN = re.compile(
    r"(?<![@\w.-])(https?://)?(www\.)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,24}))(?![\w-])",
    re.IGNORECASE,
)

PHRASES: Tuple[Tuple[str, str], ...] = (
    ("mentions_otp_or_code", r"\b(?:otps?|one[- ]time (?:password|passcode|code)|verification codes?)\b"),
    ("mentions_pin_or_password", r"\b(?:upi pin|m?pin|passwords?|passcodes?)\b"),
    ("mentions_card_details", r"\b(?:card (?:number|details)|cvv|expiry date)\b"),
    ("mentions_identity_check", r"\b(?:re-?kyc|kyc|verify your (?:account|identity|details)|complete (?:the |this |your )?verification)\b"),
    ("asks_for_payment", r"\b(?:pay now|make (?:a |the )?payment|complete (?:the |your )?payment|(?:fee|charge|amount) (?:is )?(?:pending|due))\b"),
    ("asks_to_open_link", r"\b(?:click|tap|open|visit) (?:on )?(?:the |this )?(?:link|below|here|url)\b"),
    ("asks_to_forward", r"\b(?:forward (?:this|it) to|share (?:this|it) (?:in|with|to) (?:all|every)|send(?:ing)? (?:this )?to all)\b"),
    ("deadline_or_threat", r"\b(?:(?:will|would) be (?:blocked|suspended|deactivated|closed|frozen|cancelled)|expir(?:e|es|ing) (?:today|soon|tonight)|within \d+ (?:hours?|hrs?|minutes?|mins?)|last chance|final (?:notice|warning|reminder)|immediately)\b"),
    ("addresses_the_router", r"\b(?:ignore (?:all |any )?(?:previous|prior|above|earlier)|router|routing|mark (?:this|it)(?: message)? as|classify (?:this|it) as|system prompt|[a-z]+_[a-z_]+\s*=\s*\w+|(?:action|priority)\s*[:=]\s*\w+)"),
)
_PHRASES = tuple((kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in PHRASES)


def safety_scan(ctx: ToolContext) -> Dict[str, Any]:
    """Links, sensitive requests, pressure and router-directed text, each with where it was found."""
    m = ctx.message
    sources = _sources(ctx)
    result: Dict[str, Any] = {"scanned": [name for name, _ in sources], "sender": _sender_facts(ctx)}
    result["links"] = _links(ctx, sources)
    result["phrases"] = _phrases(sources)
    entry = ctx.media_entry(m.media_id)
    if entry and entry["media_type"] == "image":
        result["image_asks_for"] = entry["analysis"].get("asks_for", [])
        result["qr_code_in_image"] = entry["analysis"].get("qr_present")
    result["forwarded_count"] = m.forwarded_count
    guard = _injection_score(ctx, " ".join(text for _, text in sources))
    if guard is not None:
        result["injection_classifier"] = guard
    return result


def _sources(ctx: ToolContext) -> List[Tuple[str, str]]:
    sources = []
    if ctx.message.text.strip():
        sources.append(("message_text", ctx.message.text))
    entry = ctx.media_entry(ctx.message.media_id)
    if entry:
        a = entry["analysis"]
        if entry["media_type"] == "image":
            text = " ".join([a.get("ocr_text", "")] + a.get("urls", [])).strip()
            if text:
                sources.append(("image_text", text))
        elif a.get("transcript", "").strip():
            sources.append(("voice_transcript", a["transcript"]))
    return sources


def _sender_facts(ctx: ToolContext) -> Dict[str, Any]:
    m = ctx.message
    if m.business_id:
        facts = business_facts(ctx.store.businesses[m.business_id])
        keep = ("verified", "official_domain", "sender_domain", "sender_domain_matches_official",
                "sender_domain_age_days", "account_age_days", "user_reports_30d")
        return {"kind": "business", **{k: facts[k] for k in keep}}
    return {
        "kind": "person",
        "registered_user": m.sender_user_id in ctx.store.users,
        "messaged_user_before": bool(ctx.history(sender_key=m.sender_key)),
    }


# ---- links -------------------------------------------------------------


def find_domains(text: str) -> List[str]:
    """Domains mentioned in text, lower-case and without www, in order of first appearance."""
    found = []
    for match in _DOMAIN.finditer(text):
        scheme, www, domain, tld = match.groups()
        if scheme or www or len(tld) == 2 or tld.lower() in COMMON_TLDS:
            found.append(domain.lower())
    return list(dict.fromkeys(found))


def _links(ctx: ToolContext, sources: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    seen: Dict[str, List[str]] = {}
    for name, text in sources:
        for domain in find_domains(text):
            seen.setdefault(domain, []).append(name)
    business = ctx.store.businesses.get(ctx.message.business_id or "")
    officials = _official_domains(ctx)
    links = []
    for domain, found_in in seen.items():
        facts: Dict[str, Any] = {"domain": domain, "found_in": found_in}
        if business:
            facts["is_sender_domain"] = bool(business.sender_domain) and _on_domain(domain, business.sender_domain)
            if business.official_domain:
                facts["on_sender_official_domain"] = _on_domain(domain, business.official_domain)
        owners = [f"{brand} ({official})" for official, brand in officials if _on_domain(domain, official)]
        if owners:
            facts["official_domain_of"] = owners[:3]
        else:
            similar = [f"{brand} ({official})" for official, brand in officials if _resembles(domain, official)]
            if similar:
                facts["contains_brand_name_of"] = similar[:3]
        if domain in SHORTENERS:
            facts["link_shortener"] = True
        links.append(facts)
    return links


def _official_domains(ctx: ToolContext) -> List[Tuple[str, str]]:
    pairs = {(b.official_domain.lower(), b.brand_name) for b in ctx.store.businesses.values() if b.official_domain}
    return sorted(pairs)


def _on_domain(domain: str, base: str) -> bool:
    base = base.lower()
    return domain == base or domain.endswith("." + base)


def _resembles(domain: str, official: str) -> bool:
    """True if a label of `domain` is, or starts with, the brand part of `official` (amazonpay-delivery.in vs amazon.in)."""
    core = official.split(".")[0]
    tokens = re.split(r"[.-]", domain)
    if len(core) >= 4:
        return any(token.startswith(core) for token in tokens)
    return core in tokens


# ---- phrases -----------------------------------------------------------


def _phrases(sources: List[Tuple[str, str]]) -> List[Dict[str, str]]:
    found = []
    for kind, pattern in _PHRASES:
        for name, text in sources:
            covered_until, kept = -1, 0
            for match in pattern.finditer(text):
                if match.start() <= covered_until:  # already inside the previous snippet
                    continue
                found.append({"kind": kind, "found_in": name, "snippet": _snippet(text, match.start(), match.end())})
                covered_until, kept = match.end() + SNIPPET_CONTEXT, kept + 1
                if kept == MAX_SNIPPETS_PER_KIND:
                    break
    return found[:MAX_PHRASES]


def _snippet(text: str, start: int, end: int) -> str:
    lo, hi = max(0, start - SNIPPET_CONTEXT), min(len(text), end + SNIPPET_CONTEXT)
    piece = ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")
    return untrusted(piece, SNIPPET_LIMIT)


# ---- optional prompt-injection classifier ------------------------------


def _injection_score(ctx: ToolContext, text: str) -> Optional[Dict[str, Any]]:
    """Score from a prompt-injection classifier, only when PROMPT_GUARD_MODEL is set."""
    model = os.environ.get("PROMPT_GUARD_MODEL", "").strip()
    if not model or ctx.client is None or not text.strip():
        return None
    try:
        response = ctx.client.chat(model=model, messages=[{"role": "user", "content": text[:GUARD_TEXT_LIMIT]}],
                                   seed=None)
    except GroqError as error:
        return {"model": model, "error": clip(str(error), 120)}
    reply = ctx.client.message_text(response).strip()
    number = re.search(r"\d*\.?\d+", reply)
    if number:
        return {"model": model, "injection_probability": round(float(number.group()), 3)}
    return {"model": model, "label": clip(reply, 40)}
