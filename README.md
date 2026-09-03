# Trivasta

**Trivasta** is a Django-based AI travel-planning marketplace. A traveller describes a trip, gets an AI-generated day-by-day itinerary, and can then put it out to verified travel agencies who submit priced offers, negotiate over real-time chat, and get paid (with automatic commission/GST splitting) through Razorpay.

## How it works

1. **Plan a trip** (`/trips/planner/`) — multi-step form (origin, destination, dates, travel style, transport, group size, budget). Enforces minimum budgets (₹8,000/person, ₹20,000 total for groups) before saving.
2. **AI itinerary generation** (`trips/services/ai.py`) — builds a strict structured prompt and *races* two Groq models (`openai/gpt-oss-20b` and `openai/gpt-oss-120b`) in parallel via a thread pool, returning whichever responds first with usable content. If both Groq racers fail, are empty, or time out (15s), it falls back to OpenRouter's free-model router (`openrouter/free`) as a genuinely separate provider. If everything fails, it raises `AIQuotaExceeded` or `AIGenerationFailed`, and `trips/views.py` swaps in a structured, hand-written fallback itinerary (`generate_fallback_itinerary`) rather than showing a broken response.
3. **Trip detail** (`/trips/<id>/`) — shows the itinerary, budget breakdown, and a "Get Offers from Agencies" CTA.
4. **Agency marketplace** — approved agencies (`marketplace.Agency`) see open trip requests and submit `Offer`s. Travellers compare offers (`/trips/trips/<id>/compare/`), pick one, and either chat first or check out directly.
5. **Real-time chat** — each accepted offer/package gets a `ChatRoom`, served over WebSockets via **Django Channels + Redis** (`marketplace/consumers.py`, `ws/chat/<room_id>/`). Agencies can raise in-chat payment requests that the traveller accepts/rejects before paying.
6. **Contact-info guard** (`marketplace/contact_guard.py`) — regex-based filter that blocks phone numbers, emails, UPI IDs, and social-media handles from being shared in chat (with spaced-out/obfuscated digit detection), so agencies and travellers can't route around the platform. Repeated attempts create an `AgencyWarning`.
7. **Payments** (`marketplace/payment_service.py`) — Razorpay Orders for checkout, with a 10% Trivasta commission and 5% GST calculated per booking, optional coupon discounts, and payouts to agencies recorded via `PayoutRecord` (Razorpay Route linked accounts, KYC-gated via `AgencyBankDetails`).
8. **Reviews** — `trips.Review` (tied to a completed `marketplace.Booking`, with per-category ratings, helpful votes, and agency replies) plus a separate `PackageReview` for direct package bookings; agency rating caches are recomputed on save.
9. **Support & refunds** — travellers can open `SupportTicket`s answered first by an AI assistant (`marketplace/ai_support.py`, alternating Groq/Gemini with a keyword-based final fallback) before optional escalation to a human via `support_dashboard`; `RefundRequest`s are reviewed and processed by staff through `refund_dashboard`.
10. **Auth** — standard Django auth plus Google OAuth2 (`social-auth-app-django`), with a `users.pipeline` step that creates a `Profile` and sends a welcome email to new OAuth signups.
11. **Role-aware navbar** — `base.html` adapts per session: travellers see *My Dashboard* / *Packages* / *Help*; agency accounts see *Agency Dashboard*; staff (`is_staff`) additionally see *Admin*, *Support*, and *Refunds*, each with a small role badge.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Django 4.2, served over ASGI via **Daphne** |
| Real-time | Django Channels + `channels_redis` (WebSocket chat) |
| AI itinerary | Groq (`gpt-oss-20b` / `gpt-oss-120b`, raced in parallel) → OpenRouter (`openrouter/free`) fallback |
| AI support chat | Groq (`llama-3.3-70b-versatile`) / Gemini (`gemini-1.5-flash`), randomised order, keyword fallback |
| Payments | Razorpay (Orders + Route payouts) |
| Auth | Django auth + Google OAuth2 (`social-auth-app-django`) |
| Database | PostgreSQL in production (`dj-database-url`), SQLite fallback for local dev |
| Email | SMTP via Resend |
| Static files | WhiteNoise |
| Frontend | Django templates, vanilla JS, dark "ocean" glassmorphism theme (Playfair Display + Syne) |

## Project structure

```
trivasta/
├── trivasta/                # project config
│   ├── settings.py
│   ├── urls.py               # /, /about, /contact, /trips/, /oauth/, /marketplace/, /users/
│   ├── asgi.py                # ProtocolTypeRouter: http + websocket (chat)
│   └── wsgi.py
├── trips/                    # trip planning, AI itinerary, reviews
│   ├── models.py             #   Trip, Itinerary, Review, ReviewReply, ReviewHelpfulVote
│   ├── services/ai.py        #   itinerary generation (Groq race → OpenRouter → fallback)
│   ├── forms.py, views.py, urls.py
│   └── migrations/
├── marketplace/               # agencies, offers, chat, payments, support, refunds
│   ├── models.py              #   Agency, Package(+Image/Review), Offer, ChatRoom, Message,
│   │                          #   PaymentRequest, Booking, TripStatus/Update, SupportTicket(+Message),
│   │                          #   RefundRequest, Coupon(+Usage), AgencyBankDetails, PayoutRecord
│   ├── consumers.py, routing.py  # WebSocket chat consumer
│   ├── contact_guard.py        # anti-circumvention regex filter for chat
│   ├── ai_support.py           # AI-assisted support ticket responses
│   ├── payment_service.py      # Razorpay orders, commission/GST/coupon math
│   ├── views.py, urls.py
│   └── migrations/
├── users/                     # auth, dashboards, admin/support/refund staff views
│   ├── models.py               #   ContactMessage, Profile
│   ├── pipeline.py             #   social-auth pipeline (profile + welcome email)
│   ├── context_processors.py   #   injects `user_agency` for template role checks
│   ├── views.py, urls.py
│   └── migrations/
├── templates/                  # base.html + trips/marketplace/support/users pages
├── static/                     # css/js/images
├── manage.py
├── requirements.txt
├── Procfile                    # web: daphne -b 0.0.0.0 -p $PORT trivasta.asgi:application
├── build.sh                    # pip install, collectstatic, migrate
├── runtime.txt                 # python-3.11.0
└── test_api_keys.py            # standalone Groq/OpenRouter key sanity check (run before deploying)
```

## Environment variables

Read from a `.env` file in the project root (`python-dotenv`, loaded in `settings.py`):

```
SECRET_KEY=
DEBUG=True

DATABASE_URL=            # postgres://... in production; omit/leave non-postgres to use SQLite locally
REDIS_URL=                # redis://... for Channels; defaults to 127.0.0.1:6379 if unset

GROQ_API_KEY=
OPENROUTER_API_KEY=       # itinerary fallback provider
GEMINI_API_KEY=           # used by marketplace/ai_support.py's Gemini branch — see Known issues

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=     # Google OAuth2 login

RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=

EMAIL_BACKEND=            # defaults to console backend if unset
EMAIL_HOST=smtp.resend.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=
SUPPORT_EMAIL=
```

> The repo's `.env` also defines `TWILLO_RECOVERY_CODE` and `SENDGRID_API_KEY`, but neither is referenced anywhere in `settings.py` or the app code — they appear to be unused leftovers.

## Known issues

- **`marketplace/ai_support.py` references `settings.GEMINI_API_KEY`**, but `settings.py` never defines it (only `GROQ_API_KEY` and `OPENROUTER_API_KEY` are set from the environment there). The Gemini branch is wrapped in a `try/except`, so it fails silently and falls through to Groq or the keyword-based fallback rather than crashing — but the Gemini fallback for the support chat is effectively dead code until `GEMINI_API_KEY` is added to `settings.py`.
- **`data.json`** at the repo root is a stray/broken file — it contains a single `[` byte, not valid JSON.
- **`.env` is committed to the zip you uploaded** (not just `.env.example`), containing real-looking secrets (Razorpay, Groq, OpenRouter, Google OAuth, SMTP credentials). If this repo is or will be pushed to GitHub, rotate those keys and add `.env` to `.gitignore` (it's currently *not* excluded — worth double-checking `.gitignore`).

## Local setup

```bash
git clone https://github.com/isurya7/trivasta.git
cd trivasta
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in the variables listed above

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

Local development defaults to SQLite and a local Redis at `127.0.0.1:6379` (needed for the chat feature — `redis-server` must be running). To sanity-check your AI keys before deploying, run:

```bash
python test_api_keys.py
```

## Deployment

Deploys as a Heroku/Render-style app:

- `build.sh` — installs dependencies, collects static files, runs migrations.
- `Procfile` — `web: daphne -b 0.0.0.0 -p $PORT trivasta.asgi:application` (ASGI, not WSGI — required for the WebSocket chat).
- `runtime.txt` — pins Python 3.11.0.
- `settings.py` sets production-only security flags (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, HSTS-adjacent headers) when `DEBUG=False`, and trusts `trivasta.onrender.com`, `trivasta.in`, and `www.trivasta.in` for CSRF — update `CSRF_TRUSTED_ORIGINS` if deploying elsewhere.
