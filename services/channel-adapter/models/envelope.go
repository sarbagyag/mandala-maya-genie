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

type WSIncoming struct {
	Text string `json:"text"`
}

type WSResponse struct {
	Type      string `json:"type"`
	Text      string `json:"text,omitempty"`
	SessionID string `json:"session_id,omitempty"`
}
