import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings

logger = logging.getLogger(__name__)


class AIQuotaExceeded(Exception):
    """Raised when every configured AI provider is currently rate-limited / out of quota."""
    pass


class AIGenerationFailed(Exception):
    """Raised when every configured AI provider failed for a non-quota reason."""
    pass


# Substrings that indicate a rate-limit / quota error across the different
# provider SDKs (Groq, OpenRouter, etc. all phrase this slightly differently).
_QUOTA_HINTS = ("rate limit", "rate_limit", "quota", "429", "resource_exhausted", "too many requests")

# Per-call timeout (seconds). Keeps a single slow/hung provider from
# stalling the whole request.
_PROVIDER_TIMEOUT = 15


def _looks_like_quota_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(hint in text for hint in _QUOTA_HINTS)


def generate_itinerary(destination: str, days: int, budget: int,
                        travel_type: str = "", travel_mode: str = "any",
                        origin: str = "", num_people: int = 1,
                        budget_type: str = "total",
                        start_date: str = "") -> str:

    print(f"[ai.py] generate_itinerary called: destination={destination}, days={days}, budget={budget}")

    total_budget = budget if budget_type == 'total' else budget * num_people
    per_person   = total_budget // num_people if num_people > 1 else total_budget

    travel_mode_text = {
        'flight': 'flights ✈️',
        'train':  'trains 🚂',
        'bus':    'buses 🚌',
        'car':    'self-drive 🚗',
        'cruise': 'cruise 🚢',
        'any':    'flexible transport'
    }.get(travel_mode, 'flexible transport')

    date_text = f"Starting {start_date}" if start_date else ""

    getting_there_block = f"""
🚂 GETTING THERE
─────────────────────────────────
• Transport : [Mode + route from {origin}]
• Duration  : [Travel time]
• Est. Cost : ₹[amount]
""" if origin else ""

    prompt = f"""
You are an expert Indian travel planner. Create a highly structured, visually clean, and detailed {days}-day travel itinerary.

═══════════════════════════════════════════
TRIP DETAILS
═══════════════════════════════════════════
• Destination   : {destination}
• From          : {origin if origin else 'Not specified'}
• Travel Style  : {travel_type if travel_type else 'General'}
• Transport     : {travel_mode_text}
• Group Size    : {num_people} {'person' if num_people == 1 else 'people'}
• Total Budget  : ₹{total_budget:,} (₹{per_person:,} per person)
• {date_text}

═══════════════════════════════════════════
OUTPUT FORMAT — follow this EXACTLY for each day
═══════════════════════════════════════════

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 DAY [N] — [Creative Day Theme in CAPS]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{getting_there_block}
🌅 MORNING  [8:00 AM – 12:00 PM]
─────────────────────────────────
• Activity  : [Specific activity + venue name]
• Location  : [Exact area / landmark]
• Duration  : [e.g. 2 hours]
• Entry Fee : ₹[amount] or Free
• Tip       : [One quick insider tip]

☕ MORNING CAFE
─────────────────────────────────
• Cafe Name : [Real, well-known local cafe]
• Must Try  : [Signature drink or snack]
• Cost      : ₹[amount] per person
• Vibe      : [2-3 words — e.g. Cosy, Rooftop, Busy street-side]

☀️ AFTERNOON  [12:00 PM – 5:00 PM]
─────────────────────────────────
• Activity  : [Specific activity + venue name]
• Location  : [Exact area / landmark]
• Duration  : [e.g. 3 hours]
• Entry Fee : ₹[amount] or Free
• Tip       : [One quick insider tip]

🍽️ LUNCH
─────────────────────────────────
• Restaurant: [Real, well-known local restaurant]
• Must Order: [Signature dish]
• Cuisine   : [Type of cuisine]
• Cost      : ₹[amount] per person
• Why Go    : [One-line reason]

🌙 EVENING  [5:00 PM – 8:00 PM]
─────────────────────────────────
• Activity  : [Specific activity + venue name]
• Location  : [Exact area / landmark]
• Duration  : [e.g. 2 hours]
• Entry Fee : ₹[amount] or Free
• Tip       : [One quick insider tip]

🍷 DINNER
─────────────────────────────────
• Restaurant: [Real, well-known local restaurant]
• Must Order: [Signature dish]
• Cuisine   : [Type of cuisine]
• Ambiance  : [e.g. Rooftop, Fine dining, Local dhaba]
• Cost      : ₹[amount] per person

💡 DAY TOTAL ESTIMATE : ₹[amount] per person

[Repeat the above block for all {days} days]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
☕ TOP CAFES & RESTAURANTS IN {destination.upper()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏆 MUST-VISIT CAFES
─────────────────────────────────
1. [Cafe Name] — [Cuisine / Vibe]
   • Signature : [Item]
   • Cost      : ₹[amount] per person
   • Best For  : [Breakfast / Evening snacks / etc.]

2–5. [Same structure for 4 more cafes]

🍴 MUST-VISIT RESTAURANTS
─────────────────────────────────
1. [Restaurant Name] — [Cuisine Type]
   • Signature : [Dish]
   • Cost      : ₹[amount] per person
   • Ambiance  : [e.g. Casual, Rooftop, Heritage]
   • Why Go    : [One line]

2–5. [Same structure for 4 more restaurants]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 MUST-VISIT LOCATIONS IN {destination.upper()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. [Location Name]
   • Category  : [Heritage / Nature / Adventure / Spiritual / etc.]
   • Best Time : [Morning / Evening / Anytime]
   • Entry Fee : ₹[amount] or Free
   • Why Visit : [One compelling line]

2–8. [Same structure for 7 more locations]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 BUDGET BREAKDOWN  ({num_people} {'person' if num_people == 1 else 'people'})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• {'✈️ 🚂 🚌' if origin else '🚗'} Transport      : ₹[amount]
• 🏨 Accommodation  : ₹[amount]
• 🍽️ Food & Dining  : ₹[amount]
• 🎯 Activities     : ₹[amount]
• 🛍️ Shopping/Misc  : ₹[amount]
                     ─────────
• 📦 TOTAL          : ₹{total_budget:,}
• 💵 Per Person     : ₹{per_person:,}

═══════════════════════════════════════════
STRICT RULES — you must follow all of these:
- Use ONLY real, well-known venues and restaurants specific to {destination}. Never invent names.
- Every section MUST use bullet points (•). No long paragraphs anywhere.
- Keep all sections visually separated by the ─── dividers shown above.
- Include actual ₹ costs for every single item.
- Budget breakdown numbers must sum to exactly ₹{total_budget:,}.
- Do not skip any section. Do not merge sections.
- Use emojis only as shown — do not add random emojis mid-sentence.
═══════════════════════════════════════════
    """

    return _race_providers(prompt)


def _race_providers(prompt: str) -> str:
    """
    Fires the fast Groq models in parallel and returns whichever responds
    first with usable content. If every Groq racer fails or times out,
    falls back to OpenRouter's free-model router (a genuinely separate
    provider/infra, so a Groq-wide outage doesn't take the whole system
    down with it).

    Groq's rate limits are per-model, not per-key, so racing distinct
    models here also spreads quota load rather than just chasing speed.
    """
    print("[ai.py] _race_providers: starting")

    # llama-3.3-70b-versatile and llama-3.1-8b-instant were deprecated by
    # Groq (June 2026); these are their recommended replacements.
    # Qwen3.6 27b is excluded here — it leaks its internal <think> reasoning
    # into visible content, which would pollute the itinerary output.
    racers = (
        ("Groq gpt-oss-20b", lambda: _try_groq(prompt, model="openai/gpt-oss-20b")),
        ("Groq gpt-oss-120b", lambda: _try_groq(prompt, model="openai/gpt-oss-120b")),
    )

    quota_hit = False

    with ThreadPoolExecutor(max_workers=len(racers)) as pool:
        futures = {pool.submit(fn): name for name, fn in racers}
        try:
            for future in as_completed(futures, timeout=_PROVIDER_TIMEOUT):
                name = futures[future]
                try:
                    content = future.result()
                    print(f"[ai.py] {name}: got result, length={len(content) if content else 0}")
                    if content and content.strip():
                        print(f"[ai.py] {name}: SUCCESS, returning its content")
                        return content
                    print(f"[ai.py] {name}: returned empty itinerary; waiting on other racers.")
                except Exception as exc:
                    print(f"[ai.py] {name}: RAISED {type(exc).__name__}: {exc}")
                    if _looks_like_quota_error(exc):
                        quota_hit = True
                        print(f"[ai.py] {name}: classified as quota error")
                    else:
                        print(f"[ai.py] {name}: classified as non-quota failure")
        except TimeoutError:
            print(f"[ai.py] All Groq racers exceeded the {_PROVIDER_TIMEOUT}s timeout.")

    print("[ai.py] All Groq racers exhausted, falling back to OpenRouter")

    # Every Groq racer failed, was empty, or timed out — fall back to a
    # genuinely separate provider before giving up.
    try:
        content = _try_openrouter(prompt)
        print(f"[ai.py] OpenRouter: got result, length={len(content) if content else 0}")
        if content and content.strip():
            print("[ai.py] OpenRouter: SUCCESS, returning its content")
            return content
        print("[ai.py] OpenRouter: returned empty itinerary.")
    except Exception as exc:
        print(f"[ai.py] OpenRouter: RAISED {type(exc).__name__}: {exc}")
        if _looks_like_quota_error(exc):
            quota_hit = True
            print("[ai.py] OpenRouter: classified as quota error")
        else:
            print("[ai.py] OpenRouter: classified as non-quota failure")

    print(f"[ai.py] Every provider failed. quota_hit={quota_hit}. Raising exception.")

    if quota_hit:
        raise AIQuotaExceeded("All configured AI providers are currently rate limited or out of quota.")
    raise AIGenerationFailed("All configured AI providers failed to generate an itinerary.")


def generate_fallback_itinerary(destination: str, days: int, budget: int,
                                 num_people: int = 1, travel_type: str = "",
                                 travel_mode: str = "any", origin: str = "",
                                 budget_type: str = "total") -> str:
    """
    A hand-built itinerary used only when every AI provider is unavailable.
    Structured the same way the AI output is (headers, dividers, bullets) so
    it still renders cleanly through {{ itinerary.content|linebreaks }},
    instead of the bare "Day N: Explore X" one-liner shown before.
    """
    total_budget = budget if budget_type == 'total' else budget * num_people
    per_person   = total_budget // num_people if num_people > 1 else total_budget

    lines = [
        "⚠️ Our AI planner is briefly unavailable, so here's a starter outline for your "
        f"{days}-day trip to {destination}. Regenerate later for full venue-level detail.",
        "",
    ]

    if origin:
        lines += [
            "🚂 GETTING THERE",
            "─────────────────────────────────",
            f"• Route      : {origin} → {destination}",
            "• Transport  : Check flights, trains, or buses depending on distance",
            "",
        ]

    for day in range(1, days + 1):
        lines += [
            f"📅 DAY {day}",
            "─────────────────────────────────",
            f"🌅 Morning   : Explore a well-known landmark or neighbourhood in {destination}",
            "☀️ Afternoon : Local sightseeing, markets, or a relaxed cafe break",
            "🌙 Evening   : Dinner at a popular local restaurant, then unwind",
            "",
        ]

    lines += [
        f"💰 BUDGET BREAKDOWN ({num_people} {'person' if num_people == 1 else 'people'})",
        "─────────────────────────────────",
        f"• Travel Style : {travel_type or 'General'}",
        f"• Transport    : {travel_mode}",
        f"• Total Budget : ₹{total_budget:,}",
        f"• Per Person   : ₹{per_person:,}",
    ]

    return "\n".join(lines)


def _try_groq(prompt: str, model: str) -> str:
    print(f"[ai.py] _try_groq: calling {model}")
    from groq import Groq
    client = Groq(api_key=settings.GROQ_API_KEY, timeout=_PROVIDER_TIMEOUT)

    kwargs = dict(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        # gpt-oss models are reasoning models: they spend part of the
        # token budget on hidden "thinking" tokens before writing any
        # visible content. With a low budget that can eat the whole
        # response and leave content empty, so give plenty of headroom
        # and cap reasoning effort so most of it goes to the itinerary.
        max_tokens=8000,
    )
    if model.startswith("openai/gpt-oss"):
        kwargs["reasoning_effort"] = "low"

    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content
    print(f"[ai.py] _try_groq: {model} responded, finish_reason={response.choices[0].finish_reason}, content_len={len(content) if content else 0}")

    if not content or not content.strip():
        finish_reason = response.choices[0].finish_reason
        logger.warning(
            "%s returned empty content (finish_reason=%s) — likely exhausted "
            "its token budget on reasoning.", model, finish_reason
        )
    return content


def _try_openrouter(prompt: str) -> str:
    print("[ai.py] _try_openrouter: called")
    api_key = getattr(settings, "OPENROUTER_API_KEY", None)
    if not api_key:
        print("[ai.py] _try_openrouter: OPENROUTER_API_KEY missing from settings!")
        raise AIGenerationFailed(
            "OPENROUTER_API_KEY is not configured in settings — skipping OpenRouter fallback."
        )

    # OpenRouter is OpenAI-compatible, so the standard openai package works
    # against their base_url — no separate SDK needed.
    from openai import OpenAI
    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=_PROVIDER_TIMEOUT,
    )
    response = client.chat.completions.create(
        # "openrouter/free" is OpenRouter's own router that auto-selects
        # among currently-available free models. Individual :free model
        # IDs rotate out with little notice, so pinning one directly is
        # fragile — the router absorbs that churn for us.
        model="openrouter/free",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=8000
    )
    content = response.choices[0].message.content
    print(f"[ai.py] _try_openrouter: responded, content_len={len(content) if content else 0}")
    return content