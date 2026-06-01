import os
import re
import json
import httpx
import base64
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

pdf_cache = {}

async def fetch_pdf(url: str):
    if url in pdf_cache:
        return pdf_cache[url]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                pdf_cache[url] = resp.content
                return resp.content
    except Exception as e:
        print(f"Erro PDF {url}: {e}")
    return None

async def find_pdfs_on_zantia(query: str) -> list:
    urls = []
    try:
        search_url = f"https://www.zantia.com/?s={query.replace(' ', '+')}"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(search_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                found = re.findall(r'https://www\.zantia\.com/storage/[^"\'>\s]+\.pdf', resp.text)
                urls = list(set(found))[:2]
    except Exception as e:
        print(f"Erro pesquisa: {e}")
    return urls

async def ask_gemini(question: str, pdf_urls: list) -> str:
    try:
        parts = []

        # Adicionar PDFs como inline_data
        for url in pdf_urls:
            pdf_data = await fetch_pdf(url)
            if pdf_data:
                parts.append({
                    "inline_data": {
                        "mime_type": "application/pdf",
                        "data": base64.b64encode(pdf_data).decode("utf-8")
                    }
                })

        # Adicionar instrução + pergunta
        parts.append({
            "text": f"""És o assistente de apoio técnico da Zantia, empresa portuguesa de energia e climatização.
Respondes sempre em português de Portugal, de forma clara e concisa.
Baseia a tua resposta nos documentos fornecidos.
Se não encontrares a informação, diz que não tens essa informação e sugere visitar www.zantia.com ou ligar para a Zantia.
Mantém a resposta curta (máximo 200 palavras), adequada para Telegram.

Pergunta do utilizador: {question}"""
        })

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 512
            }
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(GEMINI_URL, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                print(f"Gemini erro: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"Erro Gemini: {e}")
    return "Desculpe, ocorreu um erro ao processar a sua pergunta. Tente novamente ou visite www.zantia.com"

async def send_message(chat_id: int, text: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        })

async def send_typing(chat_id: int):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendChatAction", json={
            "chat_id": chat_id,
            "action": "typing"
        })

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
                "Posso ajudar-te com dúvidas sobre os nossos produtos e manuais técnicos.\n\n"
                "💬 Faz-me uma pergunta, por exemplo:\n"
                "• _Como instalar uma bomba de calor?_\n"
                "• _Qual a potência do inversor solar?_\n"
                "• _Como configurar o termostato?_\n\n"
                "🌐 Mais informação em www.zantia.com")
            return JSONResponse({"ok": True})

        if text.startswith("/help"):
            await send_message(chat_id,
                "ℹ️ *Apoio Técnico Zantia*\n\n"
                "Faz-me qualquer pergunta sobre os produtos Zantia e vou procurar a resposta nos manuais técnicos.\n\n"
                "🌐 www.zantia.com")
            return JSONResponse({"ok": True})

        # Mostrar que está a processar
        await send_typing(chat_id)

        # Procurar PDFs relevantes
        pdf_urls = await find_pdfs_on_zantia(text)

        # Se não encontrou, usar PDF de exemplo
        if not pdf_urls:
            pdf_urls = [
                "https://www.zantia.com/storage/products_files/02/045/005/1/BphD9jzr3p-manualbellonapt.pdf",
                "https://www.zantia.com/storage/products/esybor1tzu.pdf"
            ]

        # Resposta da IA
        response = await ask_gemini(text, pdf_urls)
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
