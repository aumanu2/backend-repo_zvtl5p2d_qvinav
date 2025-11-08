import os
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, EmailStr
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import User as UserSchema, Ticket as TicketSchema, Message as MessageSchema, Faq as FaqSchema, Feedback as FeedbackSchema

app = FastAPI(title="Customer Service API", description="Ticketing, Live Chat, FAQ, and Feedback API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Utils
class ObjectIdEncoder(JSONResponse):
    @staticmethod
    def encode_doc(doc: Dict[str, Any]):
        if not doc:
            return doc
        d = dict(doc)
        if "_id" in d and isinstance(d["_id"], ObjectId):
            d["id"] = str(d.pop("_id"))
        for k, v in list(d.items()):
            if isinstance(v, ObjectId):
                d[k] = str(v)
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    @staticmethod
    def encode_docs(docs: List[Dict[str, Any]]):
        return [ObjectIdEncoder.encode_doc(x) for x in docs]


def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")


# Health and schema
@app.get("/")
def read_root():
    return {"message": "Customer Service API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "❌ Not Set",
        "database_name": "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set"
            response["database_name"] = getattr(db, 'name', 'unknown')
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


@app.get("/schema")
def get_schema_definitions():
    # Expose available Pydantic schema names for DB viewer/tools
    return {
        "user": UserSchema.model_json_schema(),
        "ticket": TicketSchema.model_json_schema(),
        "message": MessageSchema.model_json_schema(),
        "faq": FaqSchema.model_json_schema(),
        "feedback": FeedbackSchema.model_json_schema(),
    }


# Auth (demo-grade: client provides pre-hashed passwords)
class RegisterPayload(BaseModel):
    name: str
    email: EmailStr
    password_hash: str
    role: Optional[str] = Field("customer", pattern="^(customer|agent|admin)$")


@app.post("/auth/register")
def register(payload: RegisterPayload):
    existing = db["user"].find_one({"email": payload.email})
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = UserSchema(name=payload.name, email=payload.email, password_hash=payload.password_hash, role=payload.role, is_active=True)
    user_id = create_document("user", user)
    doc = db["user"].find_one({"_id": ObjectId(user_id)})
    return ObjectIdEncoder.encode_doc(doc)


class LoginPayload(BaseModel):
    email: EmailStr
    password_hash: str


@app.post("/auth/login")
def login(payload: LoginPayload):
    user = db["user"].find_one({"email": payload.email, "password_hash": payload.password_hash, "is_active": True})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    # Demo token: echo email and role; in production use JWT
    return {"user": ObjectIdEncoder.encode_doc(user), "token": f"demo-{user['email']}"}


# FAQ
@app.get("/faq/search")
def faq_search(q: str = Query("", min_length=0), limit: int = 10):
    query = {"$or": [{"question": {"$regex": q, "$options": "i"}}, {"answer": {"$regex": q, "$options": "i"}}, {"tags": {"$regex": q, "$options": "i"}}]} if q else {}
    docs = list(db["faq"].find(query).limit(limit))
    return ObjectIdEncoder.encode_docs(docs)


@app.post("/faq")
def faq_create(faq: FaqSchema):
    _id = create_document("faq", faq)
    doc = db["faq"].find_one({"_id": ObjectId(_id)})
    return ObjectIdEncoder.encode_doc(doc)


# Tickets
@app.post("/tickets")
def create_ticket(ticket: TicketSchema):
    _id = create_document("ticket", ticket)
    doc = db["ticket"].find_one({"_id": ObjectId(_id)})
    return ObjectIdEncoder.encode_doc(doc)


@app.get("/tickets")
def list_tickets(customer_email: Optional[EmailStr] = None, status: Optional[str] = None, limit: int = 25):
    q: Dict[str, Any] = {}
    if customer_email:
        q["customer_email"] = customer_email
    if status:
        q["status"] = status
    docs = list(db["ticket"].find(q).sort("created_at", -1).limit(limit))
    return ObjectIdEncoder.encode_docs(docs)


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    doc = db["ticket"].find_one({"_id": oid(ticket_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ObjectIdEncoder.encode_doc(doc)


class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(open|pending|resolved|closed)$")
    priority: Optional[str] = Field(None, pattern="^(low|medium|high)$")
    assigned_to: Optional[EmailStr] = None


@app.patch("/tickets/{ticket_id}")
def update_ticket(ticket_id: str, payload: TicketUpdate):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        return {"updated": False}
    updates["updated_at"] = datetime.now(timezone.utc)
    res = db["ticket"].update_one({"_id": oid(ticket_id)}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    doc = db["ticket"].find_one({"_id": oid(ticket_id)})
    return ObjectIdEncoder.encode_doc(doc)


# Messages
@app.post("/tickets/{ticket_id}/messages")
def post_message(ticket_id: str, msg: MessageSchema):
    if msg.ticket_id != ticket_id:
        raise HTTPException(status_code=400, detail="ticket_id mismatch")
    _id = create_document("message", msg)
    doc = db["message"].find_one({"_id": ObjectId(_id)})
    # Broadcast to websocket clients
    for ws in _ws_manager.active_connections.get(ticket_id, []):
        try:
            payload = ObjectIdEncoder.encode_doc(doc)
            payload["event"] = "new_message"
            import json
            _ws_manager.safe_send(ws, payload)
        except Exception:
            pass
    return ObjectIdEncoder.encode_doc(doc)


@app.get("/tickets/{ticket_id}/messages")
def get_messages(ticket_id: str, limit: int = 50):
    docs = list(db["message"].find({"ticket_id": ticket_id}).sort("created_at", -1).limit(limit))
    docs.reverse()  # chronological
    return ObjectIdEncoder.encode_docs(docs)


# Simple WebSocket manager per ticket
class WSManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, ticket_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(ticket_id, []).append(websocket)

    def disconnect(self, ticket_id: str, websocket: WebSocket):
        conns = self.active_connections.get(ticket_id, [])
        if websocket in conns:
            conns.remove(websocket)

    def safe_send(self, websocket: WebSocket, data: Dict[str, Any]):
        import anyio
        import json
        try:
            anyio.from_thread.run(websocket.send_text, json.dumps(data))
        except Exception:
            pass

_ws_manager = WSManager()


@app.websocket("/ws/tickets/{ticket_id}")
async def ticket_ws(websocket: WebSocket, ticket_id: str):
    await _ws_manager.connect(ticket_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Echo back as system message; clients should POST to REST for persistence
            await websocket.send_text(data)
    except WebSocketDisconnect:
        _ws_manager.disconnect(ticket_id, websocket)


# Feedback
@app.post("/feedback")
def post_feedback(fb: FeedbackSchema):
    _id = create_document("feedback", fb)
    doc = db["feedback"].find_one({"_id": ObjectId(_id)})
    return ObjectIdEncoder.encode_doc(doc)


@app.get("/feedback")
def list_feedback(limit: int = 50):
    docs = list(db["feedback"].find({}).sort("created_at", -1).limit(limit))
    return ObjectIdEncoder.encode_docs(docs)


# Seed sample data
@app.post("/seed")
def seed():
    # Only seed if empty
    created = {"faq": 0, "ticket": 0}
    if db["faq"].count_documents({}) == 0:
        faqs = [
            FaqSchema(question="How to reset my password?", answer="Click 'Forgot password' on the sign-in page.", tags=["account", "password"]),
            FaqSchema(question="How to contact support?", answer="Create a ticket or use live chat.", tags=["support"]),
        ]
        for f in faqs:
            create_document("faq", f)
            created["faq"] += 1
    if db["ticket"].count_documents({}) == 0:
        t = TicketSchema(title="Demo issue", description="My app is not loading.", priority="high", customer_email="customer@example.com")
        create_document("ticket", t)
        created["ticket"] += 1
    return {"seeded": created}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
