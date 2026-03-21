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

    prompt = f"""
    Create a fun, detailed {days}-day travel itinerary.
    
    Trip Details:
    - From: {origin if origin else 'Not specified'}
    - To: {destination}
    - Travel Style: {travel_type}
    - Preferred Transport: {travel_mode_text}
    - Group Size: {num_people} {'person' if num_people == 1 else 'people'}
    - Total Budget: ₹{total_budget} (₹{per_person} per person)
    - {date_text}

    Use emojis throughout to make it engaging and easy to read.

    Format strictly as shown below:

    📅 Day 1: [Creative theme for the day]
    
    {'🚂 Getting There: [Transport details from ' + origin + ' if specified]' if origin else ''}
    
    🌅 Morning: [Activity with venue name, details and estimated cost]
    ☀️ Afternoon: [Activity with venue name, details and estimated cost]  
    🌙 Evening: [Restaurant/activity recommendation with cost]
    💡 Local Tip: [One insider tip for the day]
    
    Repeat this format for each of the {days} days.

    At the end add:

    💰 Budget Breakdown (for {num_people} {'person' if num_people == 1 else 'people'}):
    {'✈️ 🚂 🚌' if origin else '🚗'} Transport: ₹X
    🏨 Accommodation: ₹X
    🍽️ Food & Dining: ₹X
    🎯 Activities & Entry: ₹X
    🛍️ Shopping & Misc: ₹X
    📦 Total: ₹{total_budget}
    💵 Per Person: ₹{per_person}

    Keep the tone friendly, exciting and practical.
    Include specific restaurant names, hotel suggestions and activity costs.
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
        max_tokens=3000
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
        max_tokens=3000
    )
    return response.choices[0].message.content