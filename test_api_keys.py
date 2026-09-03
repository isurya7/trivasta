"""
Quick sanity check for GROQ_API_KEY and OPENROUTER_API_KEY before deploying.

Usage:
    python test_api_keys.py

Reads keys from a .env file in the same directory (or the current working
directory), same as your Django settings.py does. Doesn't touch Django at
all, so you can run it standalone without `manage.py shell`.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv()  # also check cwd, in case you run this from the project root

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

# Same models your ai.py racers use — testing with these avoids surprises
# like a valid key but a deprecated/inaccessible model name.
# (Qwen3.6 27b is intentionally excluded — it leaks <think> reasoning into
# visible content, so it's not used as a racer in ai.py either.)
GROQ_MODELS_TO_TEST = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
]

# OpenRouter's own router that auto-selects among currently-available free
# models, so this test doesn't break every time a specific :free model
# rotates out.
OPENROUTER_MODEL_TO_TEST = "openrouter/free"

TEST_PROMPT = "Reply with exactly one word: OK"


def test_groq():
    print("\n== Groq ==")
    if not GROQ_API_KEY:
        print("  ✗ GROQ_API_KEY is not set (check your .env)")
        return False

    try:
        from groq import Groq
    except ImportError:
        print("  ✗ 'groq' package not installed — run: pip install groq")
        return False

    client = Groq(api_key=GROQ_API_KEY, timeout=15)

    all_ok = True
    for model in GROQ_MODELS_TO_TEST:
        try:
            kwargs = dict(
                model=model,
                messages=[{"role": "user", "content": TEST_PROMPT}],
                max_tokens=2000,
                reasoning_effort="low",  # all current test models are gpt-oss
            )
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            if content and content.strip():
                print(f"  ✓ {model} — responded: {content.strip()[:40]!r}")
            else:
                finish_reason = response.choices[0].finish_reason
                print(f"  ✗ {model} — empty content (finish_reason={finish_reason})")
                all_ok = False
        except Exception as exc:
            print(f"  ✗ {model} — {type(exc).__name__}: {exc}")
            all_ok = False

    return all_ok


def test_openrouter():
    print("\n== OpenRouter ==")
    if not OPENROUTER_API_KEY:
        print("  ✗ OPENROUTER_API_KEY is not set (check your .env)")
        return False

    try:
        from openai import OpenAI
    except ImportError:
        print("  ✗ 'openai' package not installed — run: pip install openai")
        return False

    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        timeout=15,
    )

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL_TO_TEST,
            messages=[{"role": "user", "content": TEST_PROMPT}],
            max_tokens=2000,
        )
        content = response.choices[0].message.content
        if content and content.strip():
            print(f"  ✓ {OPENROUTER_MODEL_TO_TEST} — responded: {content.strip()[:40]!r}")
            return True
        else:
            print(f"  ✗ {OPENROUTER_MODEL_TO_TEST} — empty content")
            return False
    except Exception as exc:
        print(f"  ✗ {OPENROUTER_MODEL_TO_TEST} — {type(exc).__name__}: {exc}")
        return False


if __name__ == "__main__":
    groq_ok = test_groq()
    openrouter_ok = test_openrouter()

    print("\n== Summary ==")
    print(f"  Groq:       {'PASS' if groq_ok else 'FAIL'}")
    print(f"  OpenRouter: {'PASS' if openrouter_ok else 'FAIL'}")

    if not (groq_ok and openrouter_ok):
        sys.exit(1)