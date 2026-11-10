# Kisan Saathi (किसान साथी)

A Hindi voice agent for farmers that works inside WhatsApp. A farmer sends a voice note or makes a live call, asks a question in Hindi, and hears the answer in a Hindi voice. Reference cards (weather, sowing guide) appear in the same chat.

Built solo from Mandsaur, Madhya Pradesh, for the **Great Indian AI Internship Challenge by Gnani AI**.

**Demo video:** <[LINKEDIN POST LINK](https://lnkd.in/p/dvribK6X)>

## Problem

A farmer in the field has urgent questions (Will it rain? What should I sow now?), but typing in an app, or reading advice in English, is not easy. The information exists. The interface does not fit the farmer. Kisan Saathi puts a voice interface inside WhatsApp, which farmers already use, so there is nothing new to install.

## What it does

- Understands Hindi **voice notes** and **live WhatsApp calls**
- Replies in a spoken Hindi voice (voice in, voice out; text in, text out)
- Gives weather and rain forecasts for the farmer's village
- Suggests which rabi crops can be sown now, with sowing windows
- Remembers the farmer's village and the pending question, so it never asks twice
- Sends a reference card into the chat while the voice answer plays

## How it works

```
Farmer on WhatsApp
   |  voice note / text / call
   v
WhatsApp Cloud API  ->  FastAPI webhook (main.py)
   |
   |-- voice note --> Gnani Prisma (speech to text)
   |-- live call  --> WebRTC audio (calls.py) --> Gnani Prisma
   v
Agno agent + Groq model (agent.py)
   |-- get_weather        -> Open-Meteo (live forecast)
   |-- get_sowing_advice  -> rabi sowing table (ICAR / IMD guidance)
   |-- save_village       -> per-farmer state
   v
Gnani Timbre (text to speech) -> voice note or call audio
   + reference card in the chat
```

| File | Purpose |
|------|---------|
| `main.py` | WhatsApp webhook: messages, voice notes, images, hands calls to `calls.py` |
| `gnani.py` | Gnani Prisma (STT) and Timbre (TTS) clients, audio conversion |
| `agent.py` | Agno agent, tools, per-farmer state, weather and sowing logic |
| `calls.py` | Live calls: WebRTC (aiortc), speech detection, answer playback |

## Gnani models used

- **Prisma** (speech to text), Hindi
- **Timbre** (text to speech), Hindi, beta

## Setup

1. Python 3.10+ and a virtual environment:
   ```
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your own values (Meta token and phone number ID, Gnani API key, Groq API key). **Never commit `.env`.**
3. Meta: create an app with the WhatsApp use case, use the test number, subscribe the webhook to `messages` and `calls`, and subscribe the app to your WhatsApp account.
4. Run the server and a public tunnel:
   ```
   uvicorn main:app --port 8000
   ngrok http 8000
   ```
   Set the Callback URL in Meta to `https://<your-ngrok-url>/webhook` with your verify token.
5. Message or call the test number from a phone added as a recipient.

Meta's temporary access token expires every 24 hours. Create a permanent system-user token for longer use.

## Honest limits

- Works in **Hindi** today. Other Indian languages are the next step.
- Sowing advice covers the **rabi season** for the Malwa region of Madhya Pradesh. It is **general guidance** from ICAR and IMD material, not district-specific, and not a live government service. Farmers should confirm varieties, seed amounts and doses with their local KVK or agriculture officer.
- Weather is live from Open-Meteo. For Hindi spellings of common Madhya Pradesh towns there is a small fallback list of coordinates, used only when the online lookup fails.
- Mandi prices and crop photo diagnosis are **not built yet**. The bot says so instead of guessing.
- Farmer state (village, history) is kept in memory and resets when the server restarts.
- The bot cannot be interrupted while it speaks on a call.
- Runs on the WhatsApp test number, which allows a small set of recipient numbers.

## Next steps

More Indian languages, mandi prices (Agmarknet), crop photo diagnosis with verified treatments, saved farmer profiles, a permanent token and cloud deployment.

## Author
Priyanshu Veer, B.Tech (Computer Science, AI), Mandsaur University.