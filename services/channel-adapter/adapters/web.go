package adapters

import (
	"time"

	"github.com/google/uuid"

	"channel-adapter/models"
)

// NormalizeWebMessage converts a raw WebSocket text message into a MessageEnvelope.
func NormalizeWebMessage(sessionID, text string) models.MessageEnvelope {
	return models.MessageEnvelope{
		MessageID: uuid.New().String(),
		SessionID: sessionID,
		Channel:   "web",
		UserID:    "anonymous",
		Timestamp: time.Now().UTC(),
		Content: models.MessageContent{
			Type: "text",
			Text: text,
		},
		Metadata: models.MessageMetadata{
			Language:     "en",
			PlatformData: map[string]interface{}{},
		},
	}
}
