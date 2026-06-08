import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

async def ask_gemini(question: str) -> str:
    try:
        payload = {
            "contents": [{"parts": [{"text": f"""És o assistente de apoio técnico da Zantia, empresa portuguesa especializada em energia e climatização.

A Zantia vende e instala:
- Caldeiras a gás, gasóleo e pellets
- Esquentadores
- Bombas de calor (ar-água, AQS, piscina, geotérmica)
- Ar condicionado (split, multi-split, cassete, conduta, VRF)
- Painéis solares fotovoltaicos e acessórios
- Inversores, baterias, estruturas de fixação
- Termostatos e sistemas de controlo

Responde SEMPRE em português de Portugal, de forma clara, prática e profissional.
Dá conselhos técnicos úteis baseados no teu conhecimento sobre estes produtos.
Se a pergunta for muito específica de um modelo, sugere contactar a Zantia em www.zantia.com ou pelo telefone.
Mantém a resposta curta (máximo 250 palavras), adequada para Telegram.

Pergunta do utilizador: {question}"""}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 600}
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(GEMINI_URL, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                print(f"Gemini erro {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"Erro Gemini: {e}")
    return "Desculpe, ocorreu um erro temporário. Por favor tente novamente ou contacte a Zantia em www.zantia.com"

async def send_message(chat_id: int, text: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown"
            })
    except Exception as e:
        print(f"Erro send: {e}")

async def send_typing(chat_id: int):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{TELEGRAM_API}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
    except:
        pass

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        message = data.get("message", {})
        if not message:
            return JSONResponse({"ok": True})

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()
        first_name = message.get("from", {}).get("first_name", "")

        if not chat_id or not text:
            return JSONResponse({"ok": True})

        if text.startswith("/start"):
            await send_message(chat_id,
                f"👋 Olá {first_name}! Sou o assistente de apoio técnico da *Zantia*.\n\n"
                "Posso ajudar-te com dúvidas sobre:\n"
                "🔥 Caldeiras e esquentadores\n"
                "♨️ Bombas de calor\n"
                "❄️ Ar condicionado\n"
                "☀️ Painéis solares e fotovoltaico\n\n"
                "💬 Faz-me uma pergunta técnica!\n\n"
                "🌐 www.zantia.com")
            return JSONResponse({"ok": True})

        if text.startswith("/help"):
            await send_message(chat_id,
                "ℹ️ *Apoio Técnico Zantia*\n\n"
                "Faz qualquer pergunta sobre produtos de energia e climatização.\n"
                "🌐 www.zantia.com")
            return JSONResponse({"ok": True})

        await send_typing(chat_id)
        response = await ask_gemini(text)
        await send_message(chat_id, response)

    except Exception as e:
        print(f"Erro webhook: {e}")

    return JSONResponse({"ok": True})

@app.get("/")
async def root():
    return {"status": "Zantia Bot online ✅", "bot": "@ApoioZantiaBot"}

@app.get("/health")
async def health():
    return {"status": "ok"}
