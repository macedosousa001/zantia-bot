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

# ID da pasta pública do Google Drive
DRIVE_FOLDER_ID = "1ICjv_iwtxq7D5c9UenyKriw85lKAI-AS"

pdf_cache = {}

async def list_drive_files(folder_id: str) -> list:
    """Lista ficheiros PDF numa pasta pública do Google Drive"""
    files = []
    try:
        # Usar a API pública do Google Drive sem autenticação
        url = f"https://drive.google.com/drive/folders/{folder_id}"
        # Usar export do Google Drive para listar ficheiros
        api_url = f"https://www.googleapis.com/drive/v3/files?q=%27{folder_id}%27+in+parents+and+mimeType%3D%27application%2Fpdf%27&key=AIzaSyDXhE1YpmlVCPnZackQ2iypVtMUq8jO8RA&fields=files(id,name,mimeType)"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(api_url)
            if resp.status_code == 200:
                data = resp.json()
                files = data.get("files", [])
    except Exception as e:
        print(f"Erro a listar Drive: {e}")
    return files

async def search_drive_recursive(folder_id: str, query: str) -> list:
    """Pesquisa PDFs relevantes em todas as subpastas"""
    results = []
    try:
        # Pesquisar PDFs em toda a pasta e subpastas
        api_url = f"https://www.googleapis.com/drive/v3/files?q=%27{folder_id}%27+in+parents&key=AIzaSyDXhE1YpmlVCPnZackQ2iypVtMUq8jO8RA&fields=files(id,name,mimeType)&pageSize=50"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(api_url)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("files", [])
                
                query_lower = query.lower()
                query_words = query_lower.split()
                
                for item in items:
                    name = item.get("name", "").lower()
                    mime = item.get("mimeType", "")
                    
                    # Se é uma subpasta, pesquisa recursivamente
                    if mime == "application/vnd.google-apps.folder":
                        sub_results = await search_drive_recursive(item["id"], query)
                        results.extend(sub_results)
                    
                    # Se é PDF e o nome corresponde à pesquisa
                    elif mime == "application/pdf":
                        score = sum(1 for word in query_words if word in name)
                        if score > 0:
                            results.append({
                                "id": item["id"],
                                "name": item["name"],
                                "score": score
                            })
                
                # Ordenar por relevância
                results.sort(key=lambda x: x["score"], reverse=True)
    except Exception as e:
        print(f"Erro pesquisa Drive: {e}")
    return results

async def fetch_pdf_from_drive(file_id: str) -> bytes:
    """Descarrega PDF do Google Drive"""
    if file_id in pdf_cache:
        return pdf_cache[file_id]
    try:
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200 and len(resp.content) > 1000:
                pdf_cache[file_id] = resp.content
                return resp.content
    except Exception as e:
        print(f"Erro download PDF {file_id}: {e}")
    return None

async def ask_gemini(question: str, pdfs: list) -> str:
    """Pergunta ao Gemini com os PDFs como contexto"""
    try:
        parts = []

        # Adicionar PDFs
        for pdf_info in pdfs[:2]:  # máximo 2 PDFs
            pdf_data = await fetch_pdf_from_drive(pdf_info["id"])
            if pdf_data:
                print(f"PDF carregado: {pdf_info['name']} ({len(pdf_data)} bytes)")
                parts.append({
                    "inline_data": {
                        "mime_type": "application/pdf",
                        "data": base64.b64encode(pdf_data).decode("utf-8")
                    }
                })

        # Texto da pergunta
        parts.append({
            "text": f"""És o assistente de apoio técnico da Zantia, empresa portuguesa de energia e climatização.
Respondes sempre em português de Portugal, de forma clara e concisa.
Baseia a tua resposta nos documentos fornecidos.
Se não encontrares a informação, diz que não tens essa informação e sugere visitar www.zantia.com.
Mantém a resposta curta (máximo 200 palavras), adequada para Telegram.

Pergunta: {question}"""
        })

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 512}
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(GEMINI_URL, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                print(f"Gemini erro: {resp.status_code} {resp.text[:300]}")
    except Exception as e:
        print(f"Erro Gemini: {e}")
    return "Desculpe, ocorreu um erro. Tente novamente ou visite www.zantia.com"

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
                "💬 Exemplos:\n"
                "• _Como instalar uma caldeira a gás?_\n"
                "• _Qual a manutenção de um esquentador?_\n"
                "• _Como configurar a bomba de calor?_\n\n"
                "🌐 www.zantia.com")
            return JSONResponse({"ok": True})

        if text.startswith("/help"):
            await send_message(chat_id,
                "ℹ️ *Apoio Técnico Zantia*\n\n"
                "Faz-me qualquer pergunta sobre produtos Zantia.\n"
                "🌐 www.zantia.com")
            return JSONResponse({"ok": True})

        await send_typing(chat_id)

        # Pesquisar PDFs relevantes no Google Drive
        pdfs = await search_drive_recursive(DRIVE_FOLDER_ID, text)
        print(f"PDFs encontrados para '{text}': {[p['name'] for p in pdfs[:3]]}")

        if pdfs:
            response = await ask_gemini(text, pdfs[:2])
        else:
            # Sem PDFs específicos, responder com conhecimento geral
            response = await ask_gemini(text, [])

        await send_message(chat_id, response)

    except Exception as e:
        print(f"Erro webhook: {e}")
        import traceback
        traceback.print_exc()

    return JSONResponse({"ok": True})

@app.get("/")
async def root():
    return {"status": "Zantia Bot online ✅", "bot": "@ApoioZantiaBot"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/test-drive")
async def test_drive():
    """Testa acesso ao Google Drive"""
    files = await search_drive_recursive(DRIVE_FOLDER_ID, "caldeira")
    return {"files_found": len(files), "files": files[:5]}
