package handlers

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
	"github.com/redis/go-redis/v9"

	"channel-adapter/adapters"
	"channel-adapter/models"
)

const streamKey = "msg:inbound"

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool {
		return true // overridden at handler level
	},
}

type WSHandler struct {
	rdb            *redis.Client
	allowedOrigins map[string]bool
}

func NewWSHandler(rdb *redis.Client, allowedOrigins []string) *WSHandler {
	origins := make(map[string]bool)
	for _, o := range allowedOrigins {
		origins[o] = true
	}
	return &WSHandler{rdb: rdb, allowedOrigins: origins}
}

func (h *WSHandler) checkOrigin(r *http.Request) bool {
	if len(h.allowedOrigins) == 0 {
		return true
	}
	origin := r.Header.Get("Origin")
	if origin == "" {
		return true // allow non-browser clients
	}
	return h.allowedOrigins[origin]
}

func (h *WSHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	upgrader.CheckOrigin = h.checkOrigin

	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("WebSocket upgrade failed: %v", err)
		return
	}
	defer conn.Close()

	// Determine session ID
	sessionID := r.URL.Query().Get("session_id")
	if sessionID == "" {
		sessionID = uuid.New().String()
	}

	// Send connected message
	connMsg := models.WSResponse{
		Type:      "connected",
		SessionID: sessionID,
	}
	if err := conn.WriteJSON(connMsg); err != nil {
		log.Printf("Failed to send connected message: %v", err)
		return
	}

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()

	// Subscribe to response channel
	responseCh := fmt.Sprintf("response:%s", sessionID)
	pubsub := h.rdb.Subscribe(ctx, responseCh)
	defer pubsub.Close()

	// Forward responses from Redis pub/sub to WebSocket
	go func() {
		ch := pubsub.Channel()
		for {
			select {
			case <-ctx.Done():
				return
			case msg, ok := <-ch:
				if !ok {
					return
				}
				var resp models.WSResponse
				if err := json.Unmarshal([]byte(msg.Payload), &resp); err != nil {
					log.Printf("Failed to unmarshal response: %v", err)
					continue
				}
				if err := conn.WriteJSON(resp); err != nil {
					log.Printf("Failed to write to WebSocket: %v", err)
					cancel()
					return
				}
			}
		}
	}()

	// Read messages from WebSocket and publish to Redis Streams
	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
				log.Printf("WebSocket closed unexpectedly: %v", err)
			}
			return
		}

		var incoming models.WSIncoming
		if err := json.Unmarshal(message, &incoming); err != nil {
			log.Printf("Invalid message format: %v", err)
			conn.WriteJSON(models.WSResponse{
				Type: "error",
				Text: "Invalid message format. Send JSON with a 'text' field.",
			})
			continue
		}

		if incoming.Text == "" {
			continue
		}

		// Normalize to envelope
		envelope := adapters.NormalizeWebMessage(sessionID, incoming.Text)
		envelopeJSON, err := json.Marshal(envelope)
		if err != nil {
			log.Printf("Failed to marshal envelope: %v", err)
			continue
		}

		// Publish to Redis Streams
		if err := h.rdb.XAdd(ctx, &redis.XAddArgs{
			Stream: streamKey,
			Values: map[string]interface{}{
				"envelope": string(envelopeJSON),
			},
		}).Err(); err != nil {
			log.Printf("Failed to publish to stream: %v", err)
			conn.WriteJSON(models.WSResponse{
				Type: "error",
				Text: "Sorry, I'm having trouble processing your message. Please try again.",
			})
		}
	}
}
