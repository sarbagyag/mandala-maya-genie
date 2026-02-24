package session

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"

	"orchestrator/models"
)

const (
	sessionTTL     = 24 * time.Hour
	maxMessages    = 10
	sessionPrefix  = "session:"
)

type Manager struct {
	rdb *redis.Client
}

func NewManager(rdb *redis.Client) *Manager {
	return &Manager{rdb: rdb}
}

func (m *Manager) LoadHistory(ctx context.Context, sessionID string) ([]models.ConversationMessage, error) {
	key := fmt.Sprintf("%s%s", sessionPrefix, sessionID)
	data, err := m.rdb.Get(ctx, key).Bytes()
	if err == redis.Nil {
		return []models.ConversationMessage{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("failed to load session: %w", err)
	}

	var history []models.ConversationMessage
	if err := json.Unmarshal(data, &history); err != nil {
		return nil, fmt.Errorf("failed to unmarshal session: %w", err)
	}
	return history, nil
}

func (m *Manager) SaveHistory(ctx context.Context, sessionID string, history []models.ConversationMessage) error {
	// Keep only last maxMessages
	if len(history) > maxMessages {
		history = history[len(history)-maxMessages:]
	}

	data, err := json.Marshal(history)
	if err != nil {
		return fmt.Errorf("failed to marshal session: %w", err)
	}

	key := fmt.Sprintf("%s%s", sessionPrefix, sessionID)
	if err := m.rdb.Set(ctx, key, data, sessionTTL).Err(); err != nil {
		return fmt.Errorf("failed to save session: %w", err)
	}
	return nil
}

func (m *Manager) AppendMessages(ctx context.Context, sessionID string, userMsg, assistantMsg string) error {
	history, err := m.LoadHistory(ctx, sessionID)
	if err != nil {
		return err
	}

	history = append(history,
		models.ConversationMessage{Role: "user", Content: userMsg},
		models.ConversationMessage{Role: "assistant", Content: assistantMsg},
	)

	return m.SaveHistory(ctx, sessionID, history)
}
