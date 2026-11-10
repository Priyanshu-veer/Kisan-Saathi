# agent
import os
from datetime import datetime

import httpx
from agno.agent import Agent
from agno.models.groq import Groq
from dotenv import load_dotenv

load_dotenv()

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WX_URL = "https://api.open-meteo.com/v1/forecast"

# Per-farmer state, keyed by WhatsApp number. In memory only: resets when the server restarts.
STATE: dict[str, dict] = {}
AGENTS: dict[str, Agent] = {}
# Reference card (text) to show in chat next to a voice reply.
CARDS: dict[str, str] = {}

ASK_VILLAGE = "आप किस गाँव और ज़िले से हैं? बताइए, फिर मैं आपको सही जानकारी दूँगा।"

FILLER = {
    "se", "hu", "hun", "hoon", "mein", "me", "main", "mai", "ka", "ki", "ke", "gaon", "gav", "jila", "zila", "district",
    "village", "from", "i", "am", "in", "of", "the", "से", "हूँ", "हूं", "हु", "में", "मैं", "मै", "का", "की", "के",
    "गाँव", "गांव", "जिला", "जिले", "ज़िला", "ज़िले",
}

INSTRUCTIONS = [
    "You are Kisan Saathi, a friendly voice assistant for Indian farmers.",
    "Always reply in simple spoken Hindi written in Devanagari script, even if the farmer types Hindi in English letters (Hinglish), because the voice reads Devanagari best. If the farmer writes in proper English, reply in English.",
    "Greeting rule: if the farmer only greets you (hi, hello, hey, namaste, नमस्ते), reply exactly: नमस्ते, मैं किसान साथी हूँ। मैं आपकी क्या मदद कर सकता हूँ? Do not ask for their village in this case.",
    "Keep every reply under 40 words, at most 2 short sentences. Your reply is read aloud on a phone call, so long answers are bad. Use no markdown, no lists, no emojis, no symbols.",
    "Varieties, fertilizer, medicine and pesticide questions: you have no verified data for these yet. Do not name products or give amounts. Say in one sentence that the exact choice and dose should be confirmed with the local KVK or agriculture officer, and offer the weather or sowing guide instead.",
    "The message includes the farmer's saved village and the recent conversation. Use them. Never ask for the village yourself: just call the tool. If a tool returns NEED_VILLAGE, reply with one short sentence only; the system will ask for the village.",
    "Use get_weather for any weather or rain question. Never guess weather numbers. Give one simple farming tip based on the weather.",
    "Which crop to sow: use get_sowing_advice. Answer in one go for both cases, for example: with irrigation wheat is good, without irrigation chickpea is good. Do not ask extra questions.",
    "End every crop answer with a short reminder to confirm the variety and seed amount with the local agriculture department or KVK.",
    "If the farmer states their village, call save_village.",
    "Mandi prices and crop photo diagnosis are not available yet. If asked, say they will be available soon.",
    "Never invent facts. If a tool fails, say so and ask for a nearby bigger town name.",
]

RABI_CROPS = [
    ("गेहूँ", "अक्टूबर के अंत से नवंबर के मध्य तक", "सिंचाई ज़रूरी"),
    ("चना", "अक्टूबर के पहले हफ़्ते से नवंबर के पहले हफ़्ते तक", "बिना सिंचाई (बारानी) में भी मुख्य फसल"),
    ("मसूर और मटर", "चने जैसी ही अवधि", "हल्की सिंचाई से बेहतर"),
    ("सरसों", "रबी मौसम में बुवाई की सलाह", "कम पानी में भी हो जाती है"),
]
RABI_SOURCE = "स्रोत: ICAR (Indian Farming), IMD मध्य प्रदेश कृषि मौसम सलाह"
SAFETY = "किस्म, बीज की मात्रा और खाद के लिए अपने कृषि विभाग या KVK से ज़रूर पूछें।"


# Built-in coordinates for common Madhya Pradesh towns (Roman and Devanagari spellings).
# Fallback only: used when the online geocoder fails or does not recognise a spelling.
CITIES = [
    (["mandsaur", "mandsor", "mandasaur", "मंदसौर", "मन्दसौर"], "Mandsaur", 24.0725, 75.0693),
    (["indore", "इंदौर", "इन्दौर"], "Indore", 22.7196, 75.8577),
    (["dewas", "देवास"], "Dewas", 22.9676, 76.0534),
    (["ujjain", "उज्जैन"], "Ujjain", 23.1765, 75.7885),
    (["ratlam", "रतलाम"], "Ratlam", 23.3315, 75.0367),
    (["neemuch", "nimach", "नीमच"], "Neemuch", 24.4764, 74.8624),
    (["shajapur", "शाजापुर"], "Shajapur", 23.4269, 76.2733),
    (["dhar", "धार"], "Dhar", 22.5979, 75.2971),
    (["khargone", "खरगोन"], "Khargone", 21.8236, 75.6153),
    (["bhopal", "भोपाल"], "Bhopal", 23.2599, 77.4126),
    (["sehore", "सीहोर"], "Sehore", 23.2000, 77.0833),
    (["vidisha", "विदिशा"], "Vidisha", 23.5251, 77.8081),
    (["jabalpur", "जबलपुर"], "Jabalpur", 23.1815, 79.9864),
    (["gwalior", "ग्वालियर"], "Gwalior", 26.2183, 78.1828),
]


def _lookup_city(place: str):
    for tok in place.replace(",", " ").lower().split():
        for aliases, name, lat, lon in CITIES:
            if tok in aliases:
                return {"name": name, "admin1": "Madhya Pradesh", "latitude": lat, "longitude": lon}
    return None


def _state(phone: str) -> dict:
    return STATE.setdefault(
        phone,
        {"village": "", "history": [], "pending": None, "awaiting_village": False, "needs_village": False},
    )


def _geocode(client: httpx.Client, place: str):
    """Online geocoder first (any place in India). The built-in town list is only a fallback
    for when the online lookup fails or finds nothing."""
    words = [w for w in place.replace(",", " ").split() if w.lower() not in FILLER]
    for cand in [" ".join(words), *words]:
        if not cand:
            continue
        try:
            g = client.get(
                GEO_URL,
                params={"name": cand, "count": 1, "language": "en", "country_code": "IN"},
                timeout=8,
            ).json()
        except Exception as e:
            print("GEOCODE ERROR:", cand, repr(e))
            continue
        if g.get("results"):
            print("GEOCODE (online):", cand, "->", g["results"][0].get("name"))
            return g["results"][0]
    city = _lookup_city(place)
    if city:
        print("GEOCODE (fallback list):", place, "->", city["name"])
        return city
    print("GEOCODE FAILED:", place)
    return None


def _weather(place: str) -> tuple[str, str]:
    with httpx.Client(timeout=20) as c:
        loc = _geocode(c, place)
        if not loc:
            return f"Could not find '{place}'. Ask the farmer for a nearby bigger town or the tehsil name.", ""
        w = c.get(
            WX_URL,
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 3,
            },
        ).json()
    cur, day = w["current"], w["daily"]
    lines = [
        f"Place: {loc['name']}, {loc.get('admin1', '')}",
        f"Now: {cur['temperature_2m']} C, humidity {cur['relative_humidity_2m']} percent, wind {cur['wind_speed_10m']} km/h, rain now {cur['precipitation']} mm",
    ]
    for i, d in enumerate(day["time"]):
        lines.append(
            f"{d}: min {day['temperature_2m_min'][i]} C, max {day['temperature_2m_max'][i]} C, "
            f"rain {day['precipitation_sum'][i]} mm, rain chance {day['precipitation_probability_max'][i]} percent"
        )
    card = [
        f"मौसम: {loc['name']}, {loc.get('admin1', '')}",
        f"अभी: {cur['temperature_2m']}°C | नमी {cur['relative_humidity_2m']}% | हवा {cur['wind_speed_10m']} km/h",
    ]
    for i, d in enumerate(day["time"]):
        card.append(
            f"{d}: {day['temperature_2m_min'][i]}-{day['temperature_2m_max'][i]}°C | "
            f"बारिश {day['precipitation_sum'][i]} mm ({day['precipitation_probability_max'][i]}%)"
        )
    card.append("स्रोत: Open-Meteo")
    return "\n".join(lines), "\n".join(card)


def _sowing(place: str) -> tuple[str, str]:
    month = datetime.now().month
    if month not in (10, 11, 12):
        return (
            "Only rabi (winter) sowing advice is loaded, and it is not rabi season now. "
            "Tell the farmer to ask the local KVK or agriculture officer.",
            "",
        )
    lines = [f"Rabi season sowing guide for {place} (Malwa region, black soil). Today is month {month}."]
    card = ["इस समय बोने लायक फसलें"]
    for name, window, water in RABI_CROPS:
        lines.append(f"{name}: sowing window {window}; water: {water}")
        card.append(f"• {name}: {window}। {water}।")
    card += [RABI_SOURCE, SAFETY]
    return "\n".join(lines), "\n".join(card)


def pop_card(phone: str) -> str:
    """Return and clear the pending reference card for this farmer."""
    return CARDS.pop(phone, "")


def _make_agent(phone: str) -> Agent:
    st = _state(phone)

    def save_village(village: str) -> str:
        """Save the farmer's village or town name (and district if given)."""
        st["village"] = village
        return f"Saved village: {village}"

    def get_weather(place: str = "") -> str:
        """Get current weather and a 3 day forecast. Leave place empty to use the saved village."""
        target = place or st["village"]
        if not target:
            st["needs_village"] = True
            return "NEED_VILLAGE"
        summary, card = _weather(target)
        CARDS[phone] = card
        return summary

    def get_sowing_advice(place: str = "") -> str:
        """Get which rabi crops can be sown now for the farmer's area. Leave place empty to use the saved village."""
        target = place or st["village"]
        if not target:
            st["needs_village"] = True
            return "NEED_VILLAGE"
        summary, card = _sowing(target)
        CARDS[phone] = card
        return summary

    return Agent(
        model=Groq(id=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")),
        tools=[save_village, get_weather, get_sowing_advice],
        instructions=INSTRUCTIONS,
        markdown=False,
    )


async def _run(phone: str, question: str) -> str:
    st = _state(phone)
    if phone not in AGENTS:
        AGENTS[phone] = _make_agent(phone)
    parts = []
    if st["village"]:
        parts.append(f"Farmer's saved village: {st['village']}")
    if st["history"]:
        parts.append("Recent conversation:\n" + "\n".join(st["history"][-6:]))
    parts.append(f"Farmer's new message: {question}")
    result = await AGENTS[phone].arun("\n".join(parts))
    return (result.content or "").strip()


async def agent_reply(phone: str, text: str) -> str:
    st = _state(phone)
    text = text.strip()

    if st["awaiting_village"] and len(text.split()) <= 6:
        # The farmer is answering "which village?". Save it and answer the earlier question.
        st["village"] = text
        question = st["pending"] or text
        st["awaiting_village"] = False
        st["pending"] = None
    else:
        st["awaiting_village"] = False
        question = text

    st["needs_village"] = False
    reply = await _run(phone, question)

    if st["needs_village"]:
        # A tool needed the village and it is not known yet: ask, and remember the question.
        st["needs_village"] = False
        st["awaiting_village"] = True
        st["pending"] = question
        reply = ASK_VILLAGE
        CARDS.pop(phone, None)

    if not reply:
        reply = "माफ़ कीजिए, मैं समझ नहीं पाया। कृपया फिर से बोलिए।"

    st["history"] += [f"Farmer: {question}", f"Kisan Saathi: {reply}"]
    st["history"] = st["history"][-8:]
    return reply