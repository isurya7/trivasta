"""
Trivasta AI Support — Multi-provider setup
Primary:  Groq (llama-3.3-70b-versatile) — fast, free tier generous
Fallback: Gemini (gemini-1.5-flash) — Google free tier
Final:    Keyword-based local responses — always works, no API needed

Load balancing: alternates between Groq and Gemini on each call
to distribute load and avoid hitting rate limits.
"""

import os
import random
from django.conf import settings

# ── Groq client ───────────────────────────────────────────────────────────────
def _groq_response(messages: list[dict]) -> str:
    """Call Groq API. Returns text or raises."""
    from groq import Groq
    client = Groq(api_key=settings.GROQ_API_KEY)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=600,
        temperature=0.4,
    )
    return resp.choices[0].message.content.strip()


# ── Gemini client ─────────────────────────────────────────────────────────────
def _gemini_response(messages: list[dict]) -> str:
    """Call Gemini API. Returns text or raises."""
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

    # Convert OpenAI-style messages to Gemini format
    history = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        history.append({"role": role, "parts": [msg["content"]]})

    # Gemini needs history to start with user
    chat = model.start_chat(history=history[:-1])
    response = chat.send_message(history[-1]["parts"][0])
    return response.text.strip()


# ── Keyword fallback ──────────────────────────────────────────────────────────
KEYWORD_RESPONSES = {
    'payment': (
        "I can see you have a payment-related concern. Here's how I can help:\n\n"
        "• **Payment status** — Go to Dashboard → Bookings to check your payment status\n"
        "• **GST receipt** — Available under each booking in your dashboard\n"
        "• **Payment failed** — Your amount is safe and will be refunded within 5–7 business days automatically\n"
        "• **Charged but no confirmation** — This sometimes happens due to network delays. Check your dashboard first.\n\n"
        "If you need more help, please describe your specific issue and I'll escalate to our team."
    ),
    'booking': (
        "For booking issues, here are the most common solutions:\n\n"
        "• **Can't find your booking** — Go to Dashboard → My Bookings\n"
        "• **Wrong details in booking** — Contact the agency directly through the Chat section\n"
        "• **Need to cancel** — Cancellation depends on the agency's policy. I'll escalate this for you.\n"
        "• **Agency not responding** — Agencies must respond within 24 hours. I can flag this.\n\n"
        "Would you like me to connect you with a support agent?"
    ),
    'agency': (
        "For agency-related issues:\n\n"
        "• **Agency not responding** — They are required to reply within 24 hours. I'll flag this.\n"
        "• **Agency asked to pay outside Trivasta** — This is a violation. Please report it immediately.\n"
        "• **Quality was not as promised** — You can submit a review after your trip completes.\n"
        "• **Agency shared contact before payment** — Our system blocks this automatically.\n\n"
        "🚨 I'm escalating your complaint to our support team who will review within 2 hours."
    ),
    'technical': (
        "For technical issues:\n\n"
        "• **Page not loading** — Try clearing your browser cache (Ctrl+Shift+Delete or Cmd+Shift+Delete)\n"
        "• **Can't upload images** — Supported formats: JPG, PNG, WebP (max 5MB per image)\n"
        "• **Login not working** — Try 'Forgot Password' on the login page\n"
        "• **Payment page not loading** — Disable any ad blockers and try again\n\n"
        "If the issue persists, please describe what you see on screen and I'll escalate to our technical team."
    ),
    'refund': (
        "I understand you need a refund. Here's what happens:\n\n"
        "• Our team will review your request within **2 hours**\n"
        "• Once approved, the refund is sent within **5–7 business days** to your original payment method\n"
        "• You'll receive an email confirmation with a Razorpay refund ID\n\n"
        "🚨 I'm escalating this to our refund team right now. Please use the **Request Refund** button on your booking for the fastest processing."
    ),
    'default': (
        "Thank you for reaching out to Trivasta Support! 👋\n\n"
        "I'm your AI assistant and I'm here to help resolve your issue quickly.\n\n"
        "**Common topics I can help with:**\n"
        "• 💳 Payment issues and queries\n"
        "• 📋 Booking problems and cancellations\n"
        "• 🏢 Agency complaints\n"
        "• 💰 Refund requests\n"
        "• 🔧 Technical issues\n\n"
        "Please describe your issue in detail and I'll either resolve it immediately or connect you with a human agent."
    ),
}

ESCALATION_TRIGGERS = [
    'refund', 'fraud', 'scam', 'cheated', 'stolen', 'lawsuit', 'police',
    'complaint', 'not received', 'money lost', 'not delivered', 'fake',
    'dispute', 'chargeback', 'double charged', 'overcharged', 'threatening',
    'legal', 'consumer court', 'rto', 'defraud',
]

SYSTEM_PROMPT = """You are a helpful, professional customer support agent for Trivasta — an Indian AI-powered travel marketplace.

You help users with: payment issues, booking problems, agency complaints, refund requests, and technical issues.

Key facts about Trivasta:
- Indian travel marketplace connecting travellers with verified agencies
- Payments are processed securely through Razorpay
- GST (5%) is included in all payments
- Refunds take 5–7 business days
- Agencies must respond to travellers within 24 hours
- Contact details are only shared after payment is confirmed
- Support team responds within 2 hours for escalated issues

Guidelines:
- Be warm, empathetic and professional
- Give clear, actionable steps
- Keep responses concise (under 200 words)
- Use bullet points for steps
- If the issue involves money, fraud, or is complex — say you're escalating to a human agent
- Always respond in English
- Do NOT make up information you don't know"""


def _keyword_response(text: str) -> str:
    """Local keyword-based response, no API needed."""
    t = text.lower()
    if any(w in t for w in ['refund', 'money back', 'return money', 'reimburse']):
        return KEYWORD_RESPONSES['refund']
    if any(w in t for w in ['payment', 'paid', 'charge', 'transaction', 'razorpay', 'upi', 'bank']):
        return KEYWORD_RESPONSES['payment']
    if any(w in t for w in ['booking', 'booked', 'reservation', 'cancel', 'trip', 'package']):
        return KEYWORD_RESPONSES['booking']
    if any(w in t for w in ['agency', 'guide', 'operator', 'agent', 'company']):
        return KEYWORD_RESPONSES['agency']
    if any(w in t for w in ['technical', 'error', 'bug', 'not working', 'loading', 'crash', 'page']):
        return KEYWORD_RESPONSES['technical']
    return KEYWORD_RESPONSES['default']


def _needs_escalation(text: str) -> bool:
    t = text.lower()
    return any(trigger in t for trigger in ESCALATION_TRIGGERS)


def get_ai_support_response(user_message: str, conversation_history: list = None) -> tuple[str, bool]:
    """
    Get AI support response.

    Args:
        user_message: The user's latest message
        conversation_history: List of {'role': 'user'/'assistant', 'content': str}

    Returns:
        (response_text, needs_escalation)
    """
    needs_escalation = _needs_escalation(user_message)

    # Build messages list
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conversation_history:
        messages.extend(conversation_history[-6:])  # last 3 turns to stay within context
    messages.append({"role": "user", "content": user_message})

    # Try AI providers — load balance between Groq and Gemini
    providers = ['groq', 'gemini']
    random.shuffle(providers)  # randomise order each call

    for provider in providers:
        try:
            if provider == 'groq' and settings.GROQ_API_KEY:
                response = _groq_response(messages)
                return response, needs_escalation

            elif provider == 'gemini' and settings.GEMINI_API_KEY:
                response = _gemini_response(messages)
                return response, needs_escalation

        except Exception as e:
            # Log the error silently and try next provider
            import logging
            logging.getLogger(__name__).warning(f"AI provider {provider} failed: {e}")
            continue

    # All AI providers failed — use keyword fallback
    response = _keyword_response(user_message)
    return response, needs_escalation