import os
import httpx
import psycopg2
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="ZenFuture Multi-Channel AI Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

NGROK_URL = os.getenv("NGROK_URL")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "ZEN_TOKEN_2026")

class ChatRequest(BaseModel):
    session_key: str
    message: str
    channel: str

def get_db_connection():
    return psycopg2.connect(SUPABASE_DB_URL)

def search_knowledge_base(user_prompt: str) -> str:
    try:
        # 1. Transform raw text into mathematical vector weights locally via Ngrok tunnel
        with httpx.Client() as client:
            res = client.post(f"{NGROK_URL}/api/embeddings", json={"model": "nomic-embed-text", "prompt": user_prompt})
            embedding = res.json()["embedding"]
        
        # 2. Query centralized PostgreSQL DB using Cosine Distance Operators
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM corporate_knowledge ORDER BY embedding <=> %s::vector LIMIT 3;",
            (str(embedding),)
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if rows:
            return "\n".join([r[0] for r in rows])
    except Exception as e:
        print(f"Knowledge Base Query Error: {e}")
    
    # Fallback contextual safeguard
    return "ZenFuture Technologies specializes in Core Banking (Finacle) Support, Cloud Migration, Enterprise CRM/POS products, and SaaS Software Engineering."

@app.post("/api/chat")
async def process_chat(payload: ChatRequest):
    key = payload.session_key
    msg = payload.message.strip()
    channel = payload.channel
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT current_stage, full_name, email, phone_number FROM leads WHERE session_key = %s;", (key,))
    lead = cursor.fetchone()
    
    # Lead Collection Sequence Logic Switchboard
    if not lead:
        cursor.execute("INSERT INTO leads (session_key, current_stage, source) VALUES (%s, 'collect_name', %s);", (key, channel))
        conn.commit()
        reply = "Welcome to ZenFuture Technologies! Before we begin, could you please tell me your full name?"
    else:
        stage, name, email, phone = lead

        if stage == 'collect_name':
            cursor.execute("UPDATE leads SET full_name = %s, current_stage = 'collect_email' WHERE session_key = %s;", (msg, key))
            conn.commit()
            reply = f"Thank you, {msg}! What is your preferred business email address?"
            
        elif stage == 'collect_email':
            if "@" not in msg or "." not in msg:
                reply = "Please enter a valid business email address containing '@' and corporate domains."
            else:
                cursor.execute("UPDATE leads SET email = %s, current_stage = 'collect_phone' WHERE session_key = %s;", (msg, key))
                conn.commit()
                reply = "Got it. Finally, what is your contact phone number?"
                
        elif stage == 'collect_phone':
            cursor.execute("UPDATE leads SET phone_number = %s, current_stage = 'rag_active' WHERE session_key = %s;", (msg, key))
            conn.commit()
            reply = "Perfect! Your profile details are verified. How can I assist you with ZenFuture's Core Banking, Cloud, SaaS, or Enterprise Solutions today?"
            
        else:
            # RAG Activation Logic Block (Triggers when the lead capture steps are fully complete)
            context_data = search_knowledge_base(msg)
            system_instructions = (
                f"You are the official ZenFuture Technologies AI Assistant. "
                f"Strictly answer queries using this provided domain documentation context:\n{context_data}\n\n"
                f"Keep explanations clear and brief. If the query cannot be answered by this context, "
                f"politely ask the customer to leave an explicit human support note or email info@zenfuture.in."
            )
            
            async with httpx.AsyncClient() as client:
                ai_res = await client.post(
                    f"{NGROK_URL}/api/generate",
                    json={"model": "llama3.2", "prompt": f"{system_instructions}\n\nUser Question: {msg}", "stream": False},
                    timeout=60.0
                )
            reply = ai_res.json().get("response", "Connection timeout. Please re-verify operational status.")

    # Mirror logs seamlessly to centralized schema structure
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
    raise HTTPException(status_code=403, detail="Token Verification Mismatch.")

@app.post("/api/whatsapp")
async def incoming_whatsapp(request: Request):
    data = await request.json()
    try:
        msg_entry = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender_phone = msg_entry["from"]
        incoming_text = msg_entry["text"]["body"]
    except KeyError:
        return {"status": "ignored"}

    # Process via the same system logic flow, passing the phone number as the database key
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