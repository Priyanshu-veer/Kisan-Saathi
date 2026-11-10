# Kisan Saathi

**A Hindi voice agent for farmers, inside WhatsApp.**
Built solo by Priyanshu Veer (B.Tech CSE-AI, Mandsaur University) for the Great Indian AI Internship Challenge by Gnani AI.

**Demo video:** [Watch on LinkedIn](https://lnkd.in/p/dvribK6X)

**Code:** [GitHub repository](https://github.com/Priyanshu-veer/Kisan-Saathi)

## Problem
A farmer in the field has urgent questions: Will it rain? What should I sow now? Typing in an app, or reading advice in English, is hard. The information exists, but the interface does not fit the farmer.

## Solution
Kisan Saathi puts a voice interface inside WhatsApp, which farmers already use. Nothing new to install, nothing to type.
- Send a Hindi **voice note** or make a **live WhatsApp call**.
- The agent answers in a spoken Hindi voice (voice in, voice out).
- A **reference card** (weather, sowing guide) appears in the same chat while the answer plays.
- It remembers the farmer's village and the pending question, so it never asks twice.

## How Gnani's models are used
- **Gnani Prisma** (speech to text): understands the farmer's Hindi voice, for voice notes and live calls.
- **Gnani Timbre** (text to speech): speaks every answer in Hindi.
- **Agno agent + Groq**: picks the right tool for the question.
- **Open-Meteo**: live weather. **Sowing table**: rabi crops and sowing windows from ICAR and IMD guidance.
- **WhatsApp Cloud API + WebRTC (aiortc)**: voice notes and real-time calls.

## What works today
Hindi voice notes and live calls, weather and rain forecast for the farmer's village, and rabi sowing guidance for the Malwa region of Madhya Pradesh.

## Honest limits
Sowing advice is general guidance, not district-specific, and the bot tells farmers to confirm varieties and doses with their local KVK or agriculture officer. Mandi prices and crop photo diagnosis are not built yet, and the bot says so instead of guessing. Hindi only for now.

## Next steps
More Indian languages (Gnani's models cover several), mandi prices, crop photo diagnosis with verified treatments, district-level advisory data, and cloud deployment with a permanent WhatsApp number.

## Why it matters
Voice is the natural interface for people who cannot or prefer not to type. Built from Mandsaur, a Tier 3 town, for farmers like those around it.