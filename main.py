import os
import httpx
import psycopg2
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ⚠️ PASTE YOUR REPLACED LINKS INSIDE THE QUOTES BELOW:
NGROK_URL = "https://xxxx-xxxx.ngrok-free.app" 
# Fixed connection pooler string structure:
# The standard connection format accepted directly by psycopg2
SUPABASE_DB_URL = "postgres://postgres.oidwubtejmgppfbwtjqy:Sanjeyyyy%402005@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"

META_ACCESS_TOKEN = "PASTE_YOUR_GENERATED_META_TOKEN_HERE"
WHATSAPP_PHONE_NUMBER_ID = "1146483951889355"
META_VERIFY_TOKEN = "ZEN_TOKEN_2026"

class ChatRequest(BaseModel):
    session_key: str
    message: str
    channel: str

def get_db_connection():
    return psycopg2.connect(SUPABASE_DB_URL)

def search_knowledge_base(user_prompt: str) -> str:
    try:
        with httpx.Client() as client:
            res = client.post(f"{NGROK_URL}/api/embeddings", json={"model": "nomic-embed-text", "prompt": user_prompt})
            embedding = res.json()["embedding"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM corporate_knowledge ORDER BY embedding <=> %s::vector LIMIT 2;",
            (str(embedding),)
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        if rows:
            return "\n".join([r[0] for r in rows])
    except Exception as e:
        print(f"Error reading notebook: {e}")
    return "Zenfuture Technologies provides custom software engineering and enterprise applications."

@app.post("/api/chat")
async def process_chat(payload: ChatRequest):
    key = payload.session_key
    msg = payload.message.strip()
    channel = payload.channel
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT current_stage, full_name, email, phone_number FROM leads WHERE session_key = %s;", (key,))
    lead = cursor.fetchone()
    
    if not lead:
        cursor.execute("INSERT INTO leads (session_key, current_stage, source) VALUES (%s, 'collect_name', %s);", (key, channel))
        conn.commit()
        stage, name, email, phone = 'collect_name', None, None, None
        reply = "Welcome to Zenfuture Technologies! Before we begin, what is your full name?"
    else:
        stage, name, email, phone = lead

    if stage == 'collect_name':
        cursor.execute("UPDATE leads SET full_name = %s, current_stage = 'collect_email' WHERE session_key = %s;", (msg, key))
        conn.commit()
        reply = f"Thank you, {msg}! What is your best business email address?"
        
    elif stage == 'collect_email':
        if "@" not in msg:
            reply = "Please enter a real email address containing an '@' symbol."
        else:
            cursor.execute("UPDATE leads SET email = %s, current_stage = 'collect_phone' WHERE session_key = %s;", (msg, key))
            conn.commit()
            reply = "Got it. Finally, what is your contact phone number?"
            
    elif stage == 'collect_phone':
        cursor.execute("UPDATE leads SET phone_number = %s, current_stage = 'rag_active' WHERE session_key = %s;", (msg, key))
        conn.commit()
        reply = "Perfect! Your inquiry details are checked in. How can I help you with Zenfuture's solutions today?"
        
    else:
        context_data = search_knowledge_base(msg)
        system_instructions = (
            f"You are the official AI Assistant for Zenfuture Technologies. "
            f"Answer using only this business context:\n{context_data}\n"
            f"Keep answers brief. If you don't know, ask them to leave an explicit human support note."
        )
        
        async with httpx.AsyncClient() as client:
            ai_res = await client.post(
                f"{NGROK_URL}/api/generate",
                json={"model": "llama3.2", "prompt": f"{system_instructions}\n\nUser Question: {msg}", "stream": False},
                timeout=60.0
            )
        reply = ai_res.json().get("response", "System connection issue. Please retry.")

    cursor.execute("INSERT INTO chat_logs (session_key, sender, message, channel) VALUES (%s, 'user', %s, %s);", (key, msg, channel))
    cursor.execute("INSERT INTO chat_logs (session_key, sender, message, channel) VALUES (%s, 'bot', %s, %s);", (key, reply, channel))
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"reply": reply}

@app.get("/api/whatsapp")
async def verify_whatsapp(request: Request):
    params = dict(request.query_params)
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == META_VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    raise HTTPException(status_code=403, detail="Token mismatch.")

@app.post("/api/whatsapp")
async def incoming_whatsapp(request: Request):
    data = await request.json()
    try:
        msg_entry = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender_phone = msg_entry["from"]
        incoming_text = msg_entry["text"]["body"]
    except KeyError:
        return {"status": "ignored"}

    bot_response = await process_chat(ChatRequest(session_key=sender_phone, message=incoming_text, channel="whatsapp"))
    
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": sender_phone,
                "type": "text",
                "text": {"body": bot_response["reply"]}
            }
        )
    return {"status": "processed"}
