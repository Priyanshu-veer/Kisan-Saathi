# """FastAPI server: receives WhatsApp messages and calls, and connects them to the Kisan Saathi agent."""
import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response

load_dotenv()

import agent  # noqa: E402
import calls  # noqa: E402
import gnani  # noqa: E402  (after load_dotenv)

TOKEN = os.getenv("META_TOKEN")
PHONE_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY = os.getenv("VERIFY_TOKEN")
GRAPH = "https://graph.facebook.com/v25.0"

app = FastAPI()

@app.get("/webhook")
def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == VERIFY:
        return Response(content=q.get("hub.challenge"), media_type="text/plain")
    return Response(status_code=403)


@app.post("/webhook")
async def receive(request: Request):
    data = await request.json()
    try:
        change = data["entry"][0]["changes"][0]
        if change.get("field") == "calls":
            await calls.handle(change["value"], send_text)  # live WhatsApp call events
            return {"ok": True}
    except (KeyError, IndexError):
        return {"ok": True}
    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
    except (KeyError, IndexError):
        return {"ok": True}  # status updates, call events, etc.

    sender = msg["from"]
    kind = msg["type"]
    print("INCOMING:", kind, "from", sender)

    try:
        if kind == "text":
            # Text in -> text out only
            reply = await agent.agent_reply(sender, msg["text"]["body"])
            agent.pop_card(sender)  # the text reply is enough, drop any pending card
            await send_text(sender, reply)

        elif kind == "audio":
            audio = await download_media(msg["audio"]["id"])
            text = await gnani.stt(audio)  # Prisma
            print("HEARD:", text)
            if not text:
                await send_text(sender, "आवाज़ साफ़ नहीं आई। कृपया फिर से बोलिए।")
                return {"ok": True}
            reply = await agent.agent_reply(sender, text)
            # Voice in -> voice out only, plus a reference card in chat when useful
            wav = await gnani.tts(reply)  # Timbre
            ogg = gnani.wav_to_ogg_opus(wav)
            await send_voice(sender, ogg)
            card = agent.pop_card(sender)
            if card:
                await send_text(sender, card)

        elif kind == "image":
            await send_text(sender, "फोटो मिल गई। फसल की जाँच जल्द आएगी।")

    except Exception as e:
        print("ERROR:", repr(e))
        await send_text(sender, "कुछ गड़बड़ हो गई। कृपया दोबारा कोशिश करें।")

    return {"ok": True}


async def download_media(media_id: str) -> bytes:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(timeout=60) as client:
        meta = await client.get(f"{GRAPH}/{media_id}", headers=headers)
        url = meta.json()["url"]
        file = await client.get(url, headers=headers)
        return file.content


async def send_text(to: str, body: str):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GRAPH}/{PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": body},
            },
        )
        print("SEND text:", r.status_code, r.text[:200])


async def send_voice(to: str, ogg_bytes: bytes):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(timeout=60) as client:
        up = await client.post(
            f"{GRAPH}/{PHONE_ID}/media",
            headers=headers,
            data={"messaging_product": "whatsapp", "type": "audio/ogg"},
            files={"file": ("reply.ogg", ogg_bytes, "audio/ogg")},
        )
        print("UPLOAD:", up.status_code, up.text[:200])
        media_id = up.json()["id"]
        r = await client.post(
            f"{GRAPH}/{PHONE_ID}/messages",
            headers=headers,
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "audio",
                "audio": {"id": media_id},
            },
        )
        print("SEND voice:", r.status_code, r.text[:200])


async def send_image(to: str, path: str, caption: str = ""):
    """Send a local image (JPG or PNG, under 5 MB) into the chat, with an optional caption."""
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with open(path, "rb") as f:
        content = f.read()
    async with httpx.AsyncClient(timeout=60) as client:
        up = await client.post(
            f"{GRAPH}/{PHONE_ID}/media",
            headers=headers,
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (os.path.basename(path), content, mime)},
        )
        print("UPLOAD image:", up.status_code, up.text[:200])
        media_id = up.json()["id"]
        image = {"id": media_id}
        if caption:
            image["caption"] = caption
        r = await client.post(
            f"{GRAPH}/{PHONE_ID}/messages",
            headers=headers,
            json={"messaging_product": "whatsapp", "to": to, "type": "image", "image": image},
        )
        print("SEND image:", r.status_code, r.text[:200])