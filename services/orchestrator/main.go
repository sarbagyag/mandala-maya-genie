package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"github.com/redis/go-redis/v9"

	"orchestrator/router"
	"orchestrator/session"
)

func main() {
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379"
	}
	cognitiveURL := os.Getenv("COGNITIVE_CORE_URL")
	if cognitiveURL == "" {
		cognitiveURL = "http://localhost:8083"
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8082"
	}

	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		log.Fatalf("Invalid REDIS_URL: %v", err)
	}
	rdb := redis.NewClient(opts)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Verify Redis connection
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("Failed to connect to Redis: %v", err)
	}
	log.Println("Connected to Redis")

	sessionMgr := session.NewManager(rdb)
	r := router.New(rdb, sessionMgr, cognitiveURL)

	// Create consumer group
	if err := r.EnsureConsumerGroup(ctx); err != nil {
		log.Fatalf("Failed to create consumer group: %v", err)
	}

	// Start consumer loop in background
	go r.ConsumeLoop(ctx)

	// Health endpoint
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})

	server := &http.Server{
		Addr:    fmt.Sprintf(":%s", port),
		Handler: mux,
	}

	// Graceful shutdown
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("Shutting down...")
		cancel()
		server.Close()
	}()

	log.Printf("Orchestrator listening on :%s", port)
	if err := server.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
}
