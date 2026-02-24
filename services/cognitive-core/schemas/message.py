from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class MessageContent(BaseModel):
    type: str = "text"
    text: str


class MessageMetadata(BaseModel):
    language: str = "en"
    platform_data: dict = {}


class MessageEnvelope(BaseModel):
    message_id: str
    session_id: str
    channel: str = "web"
    user_id: str = "anonymous"
    timestamp: datetime
    content: MessageContent
    metadata: MessageMetadata = MessageMetadata()


class ConversationMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: str
    message: str
    conversation_history: list[ConversationMessage] = []
    channel: str = "web"
    language: str = "en"


class ChatResponse(BaseModel):
    session_id: str
    response: str
    sources: list[str] = []
    model_used: str
