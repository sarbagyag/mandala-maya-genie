package models

import "time"

type MessageContent struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type MessageMetadata struct {
	Language     string                 `json:"language"`
	PlatformData map[string]interface{} `json:"platform_data"`
}

type MessageEnvelope struct {
	MessageID string          `json:"message_id"`
	SessionID string          `json:"session_id"`
	Channel   string          `json:"channel"`
	UserID    string          `json:"user_id"`
	Timestamp time.Time       `json:"timestamp"`
	Content   MessageContent  `json:"content"`
	Metadata  MessageMetadata `json:"metadata"`
}

type ConversationMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type ChatRequest struct {
	SessionID           string                `json:"session_id"`
	Message             string                `json:"message"`
	ConversationHistory []ConversationMessage `json:"conversation_history"`
	Channel             string                `json:"channel"`
	Language            string                `json:"language"`
}

type ChatResponse struct {
	SessionID string   `json:"session_id"`
	Response  string   `json:"response"`
	Sources   []string `json:"sources"`
	ModelUsed string   `json:"model_used"`
}

type WSResponse struct {
	Type      string `json:"type"`
	Text      string `json:"text,omitempty"`
	SessionID string `json:"session_id,omitempty"`
}
