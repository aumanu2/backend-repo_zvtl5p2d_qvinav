from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime

# Auth & RBAC
class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    password_hash: str = Field(..., description="Hashed password")
    role: Literal["customer", "agent", "admin"] = Field("customer", description="Role for RBAC")
    is_active: bool = Field(True, description="Whether user is active")

# Knowledge base / FAQ
class Faq(BaseModel):
    question: str
    answer: str
    tags: Optional[List[str]] = []
    views: int = 0

# Ticketing
class Ticket(BaseModel):
    title: str
    description: str
    status: Literal["open", "pending", "resolved", "closed"] = "open"
    priority: Literal["low", "medium", "high"] = "medium"
    customer_email: EmailStr
    assigned_to: Optional[EmailStr] = None

class Message(BaseModel):
    ticket_id: str
    sender_email: EmailStr
    content: str
    type: Literal["text", "system"] = "text"

# Feedback
class Feedback(BaseModel):
    email: Optional[EmailStr] = None
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None

# For convenience in viewers
class Post(BaseModel):
    title: str
    body: str
