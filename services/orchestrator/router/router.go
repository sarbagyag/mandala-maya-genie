package router

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/redis/go-redis/v9"

	"orchestrator/models"
	"orchestrator/session"
)

const (
	streamKey      = "msg:inbound"
	consumerGroup  = "orchestrator-group"
	consumerName   = "orchestrator-1"
	responsePrefix = "response:"
	httpTimeout    = 60 * time.Second
)

type Router struct {
	rdb            *redis.Client
	sessionMgr     *session.Manager
	cognitiveURL   string
	httpClient     *http.Client
}

func New(rdb *redis.Client, sessionMgr *session.Manager, cognitiveURL string) *Router {
	return &Router{
		rdb:        rdb,
		sessionMgr: sessionMgr,
		cognitiveURL: cognitiveURL,
		httpClient: &http.Client{Timeout: httpTimeout},
	}
}

func (r *Router) EnsureConsumerGroup(ctx context.Context) error {
	err := r.rdb.XGroupCreateMkStream(ctx, streamKey, consumerGroup, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		return fmt.Errorf("failed to create consumer group: %w", err)
	}
	return nil
}

func (r *Router) ConsumeLoop(ctx context.Context) {
	log.Println("Starting consumer loop...")
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		streams, err := r.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group:    consumerGroup,
			Consumer: consumerName,
			Streams:  []string{streamKey, ">"},
			Count:    1,
			Block:    5 * time.Second,
		}).Result()

		if err == redis.Nil || err != nil && ctx.Err() != nil {
			continue
		}
		if err != nil {
			log.Printf("Error reading stream: %v", err)
			time.Sleep(1 * time.Second)
			continue
		}

		for _, stream := range streams {
			for _, msg := range stream.Messages {
				r.handleMessage(ctx, msg)
			}
		}
	}
}

func (r *Router) handleMessage(ctx context.Context, msg redis.XMessage) {
	envelopeJSON, ok := msg.Values["envelope"].(string)
	if !ok {
		log.Printf("Invalid message format, missing envelope field: %s", msg.ID)
		r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)
		return
	}

	var envelope models.MessageEnvelope
	if err := json.Unmarshal([]byte(envelopeJSON), &envelope); err != nil {
		log.Printf("Failed to unmarshal envelope: %v", err)
		r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)
		return
	}

	sessionID := envelope.SessionID
	log.Printf("Processing message %s for session %s", envelope.MessageID, sessionID)

	// Publish typing indicator
	r.publishResponse(ctx, sessionID, models.WSResponse{Type: "typing"})

	// Load conversation history
	history, err := r.sessionMgr.LoadHistory(ctx, sessionID)
	if err != nil {
		log.Printf("Failed to load history: %v", err)
		history = []models.ConversationMessage{}
	}

	// Build request for cognitive-core
	chatReq := models.ChatRequest{
		SessionID:           sessionID,
		Message:             envelope.Content.Text,
		ConversationHistory: history,
		Channel:             envelope.Channel,
		Language:            envelope.Metadata.Language,
	}

	// Call cognitive-core
	chatResp, err := r.callCognitiveCore(ctx, chatReq)
	if err != nil {
		log.Printf("Cognitive core error: %v", err)
		r.publishResponse(ctx, sessionID, models.WSResponse{
			Type: "error",
			Text: "Sorry, I'm having trouble responding right now. Please try again.",
		})
		r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)
		return
	}

	// Save conversation history
	if err := r.sessionMgr.AppendMessages(ctx, sessionID, envelope.Content.Text, chatResp.Response); err != nil {
		log.Printf("Failed to save history: %v", err)
	}

	// Publish response
	r.publishResponse(ctx, sessionID, models.WSResponse{
		Type:      "message",
		Text:      chatResp.Response,
		SessionID: sessionID,
	})

	// Acknowledge the stream message
	r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)
}

func (r *Router) callCognitiveCore(ctx context.Context, req models.ChatRequest) (*models.ChatResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	url := fmt.Sprintf("%s/chat", r.cognitiveURL)
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := r.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("cognitive-core returned %d: %s", resp.StatusCode, string(respBody))
	}

	var chatResp models.ChatResponse
	if err := json.Unmarshal(respBody, &chatResp); err != nil {
		return nil, fmt.Errorf("failed to unmarshal response: %w", err)
	}
	return &chatResp, nil
}

func (r *Router) publishResponse(ctx context.Context, sessionID string, resp models.WSResponse) {
	data, err := json.Marshal(resp)
	if err != nil {
		log.Printf("Failed to marshal response: %v", err)
		return
	}
	channel := fmt.Sprintf("%s%s", responsePrefix, sessionID)
	if err := r.rdb.Publish(ctx, channel, string(data)).Err(); err != nil {
		log.Printf("Failed to publish response: %v", err)
	}
}
