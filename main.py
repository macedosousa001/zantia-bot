import os
import json
import httpx
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import anthropic

app = FastAPI()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Cache de PDFs já lidos
pdf_cache = {}

async def fetch_pdf_text(url: str) -> str:
    """Descarrega e extrai texto de um PDF"""
    if url in pdf_cache:
        return pdf_cache[url]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                # Envia o PDF para a API do Claude como documento
                pdf_cache[url] = resp.content
                return resp.content
    except Exception as e:
        print(f"Erro a ler PDF {url}: {e}")
    return None

async def find_pdf_urls_from_zantia(query: str) -> list:
    """Procura PDFs relevantes no zantia.com usando a pesquisa do site"""
    urls = []
    try:
        search_url = f"https://www.zantia.com/?s={query.replace(' ', '+')}"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(search_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                html = resp.text
                # Extrair URLs de PDFs do HTML
                import re
                pdf_urls = re.findall(r'https://www\.zantia\.com/storage/[^"\'>\s]+\.pdf', html)
                urls = list(set(pdf_urls))[:3]  # máximo 3 PDFs
    except Exception as e:
        print(f"Erro na pesquisa: {e}")
    return urls

async def ask_claude_with_pdfs(question: str, pdf_urls: list) -> str:
    """Pergunta ao Claude com os PDFs como contexto"""
    try:
        # Construir mensagem com PDFs
        content = []

        # Adicionar PDFs como documentos
        for url in pdf_urls:
            pdf_data = await fetch_pdf_text(url)
            if pdf_data:
                import base64
                content.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf_data).decode("utf-8")
                    },
                    "title": url.split("/")[-1],
                    "cache_control": {"type": "ephemeral"}
                })

        # Adicionar a pergunta
        content.append({
            "type": "text",
            "text": question
        })

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system="""És o assistente de apoio técnico da Zantia Formação. 
Respondes em português de Portugal de forma clara e concisa.
Baseias as tuas respostas nos manuais e documentos fornecidos.
Se não encontrares a informação nos documentos, diz que não tens essa informação disponível e sugere contactar a Zantia diretamente em www.zantia.com.
Mantém as respostas curtas e práticas, adequadas para Telegram (máximo 300 palavras).""",
            messages=[{"role": "user", "content": content}]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Erro Claude: {e}")
        return "Ocorreu um erro ao processar a tua pergunta. Tenta novamente ou visita www.zantia.com."

async def send_telegram_message(chat_id: int, text: str):
    """Envia mensagem pelo Telegram"""
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        })

async def send_typing(chat_id: int):
    """Mostra indicador de digitação"""
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendChatAction", json={
            "chat_id": chat_id,
            "action": "typing"
        })

@app.post("/webhook")
async def webhook(request: Request):
    """Recebe mensagens do Telegram"""
    try:
        data = await request.json()
        print(f"Recebido: {json.dumps(data, ensure_ascii=False)[:200]}")

        message = data.get("message", {})
        if not message:
            return JSONResponse({"ok": True})

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()
        first_name = message.get("from", {}).get("first_name", "")

        if not chat_id or not text:
            return JSONResponse({"ok": True})

        # Comandos especiais
        if text.startswith("/start"):
            await send_telegram_message(chat_id,
                f"👋 Olá {first_name}! Sou o assistente de apoio técnico da *Zantia Formação*.\n\n"
                "Posso responder às tuas perguntas sobre os produtos e manuais disponíveis em www.zantia.com\n\n"
                "💬 *Como posso ajudar-te hoje?*\n\n"
                "Exemplos:\n"
                "• Como instalar uma bomba de calor?\n"
                "• Qual a potência do inversor X?\n"
                "• Como configurar o termostato?")
            return JSONResponse({"ok": True})

        if text.startswith("/help"):
            await send_telegram_message(chat_id,
                "ℹ️ *Apoio Zantia*\n\n"
                "Faz-me qualquer pergunta sobre os produtos Zantia e eu vou procurar a resposta nos manuais técnicos.\n\n"
                "🌐 www.zantia.com")
            return JSONResponse({"ok": True})

        # Mostrar que está a processar
        await send_typing(chat_id)

        # Procurar PDFs relevantes no zantia.com
        pdf_urls = await find_pdf_urls_from_zantia(text)

        # Se não encontrou PDFs específicos, usa o PDF de exemplo
        if not pdf_urls:
            pdf_urls = ["https://www.zantia.com/storage/products/zmwbryvhni.pdf"]

        # Obter resposta da IA com os PDFs
        response_text = await ask_claude_with_pdfs(text, pdf_urls)

        await send_telegram_message(chat_id, response_text)

    except Exception as e:
        print(f"Erro no webhook: {e}")

    return JSONResponse({"ok": True})

@app.get("/")
async def root():
    return {"status": "Zantia Bot online", "bot": "@ApoioZantiaBot"}

@app.get("/health")
async def health():
    return {"status": "ok"}
