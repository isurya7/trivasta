from django.conf import settings

class AIQuotaExceeded(Exception):
    pass

def generate_itinerary(destination: str, days: int, budget: int,
                        travel_type: str = "", travel_mode: str = "any",
                        origin: str = "", num_people: int = 1,
                        budget_type: str = "total",
                        start_date: str = "") -> str:

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

    try:
        return _try_groq(prompt)
    except AIQuotaExceeded:
        pass
    except Exception:
        pass

    try:
        return _try_gemini(prompt)
    except AIQuotaExceeded:
        pass
    except Exception:
        pass

    try:
        return _try_llama(prompt)
    except Exception:
        pass

    return "\n".join([
        f"📅 Day {i}: Explore {destination} — local sights, food & culture 🌍"
        for i in range(1, days + 1)
    ])


def _try_groq(prompt: str) -> str:
    from groq import Groq
    client = Groq(api_key=settings.GROQ_API_KEY)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=5000
    )
    return response.choices[0].message.content


def _try_gemini(prompt: str) -> str:
    from google import genai
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.0-flash-lite",
        contents=prompt
    )
    return response.text


def _try_llama(prompt: str) -> str:
    from groq import Groq
    client = Groq(api_key=settings.GROQ_API_KEY)
    response = client.chat.completions.create(
        model="gemma2-9b-it",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=5000
    )
    return response.choices[0].message.content