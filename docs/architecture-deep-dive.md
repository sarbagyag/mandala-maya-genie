# Maya Genie — Architecture & Go Deep Dive

> A complete technical reference for the Maya Genie codebase. Written as a learning guide for Go patterns, concurrency, async handling, and distributed system design as expressed in this project.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Full Architecture Map](#2-full-architecture-map)
3. [The Shared Contract: MessageEnvelope](#3-the-shared-contract-messageenvelope)
4. [Service: channel-adapter (Go)](#4-service-channel-adapter-go)
   - 4.1 [Entry Point & Dependency Wiring](#41-entry-point--dependency-wiring)
   - 4.2 [The WSHandler Type — Go's Interface Pattern](#42-the-wshandler-type--gos-interface-pattern)
   - 4.3 [WebSocket Upgrade — HTTP → Full-Duplex TCP](#43-websocket-upgrade--http--full-duplex-tcp)
   - 4.4 [Origin Checking — Security at the Handler Layer](#44-origin-checking--security-at-the-handler-layer)
   - 4.5 [Context Propagation & Cancellation](#45-context-propagation--cancellation)
   - 4.6 [Goroutines — Spawning the Response Listener](#46-goroutines--spawning-the-response-listener)
   - 4.7 [The `select` Statement — Concurrent Fan-in](#47-the-select-statement--concurrent-fan-in)
   - 4.8 [Redis Pub/Sub — Receiving Responses](#48-redis-pubsub--receiving-responses)
   - 4.9 [Redis Streams — Publishing Inbound Messages](#49-redis-streams--publishing-inbound-messages)
   - 4.10 [The Adapter Layer — NormalizeWebMessage](#410-the-adapter-layer--normalizewebmessage)
5. [Service: orchestrator (Go)](#5-service-orchestrator-go)
   - 5.1 [Entry Point — Background Goroutines & Graceful Shutdown](#51-entry-point--background-goroutines--graceful-shutdown)
   - 5.2 [Signal Handling — OS-Level Shutdown](#52-signal-handling--os-level-shutdown)
   - 5.3 [Router Type — Struct-Based Dependency Injection](#53-router-type--struct-based-dependency-injection)
   - 5.4 [Redis Streams Consumer Group — XREADGROUP](#54-redis-streams-consumer-group--xreadgroup)
   - 5.5 [The Consume Loop — Blocking Poll Pattern](#55-the-consume-loop--blocking-poll-pattern)
   - 5.6 [Message Processing Pipeline](#56-message-processing-pipeline)
   - 5.7 [HTTP Client — Calling cognitive-core](#57-http-client--calling-cognitive-core)
   - 5.8 [Publishing Responses — Redis Pub/Sub](#58-publishing-responses--redis-pubsub)
   - 5.9 [Session Manager](#59-session-manager)
6. [Service: cognitive-core (Python)](#6-service-cognitive-core-python)
7. [Redis as the Nervous System](#7-redis-as-the-nervous-system)
8. [Complete Message Lifecycle — End-to-End Trace](#8-complete-message-lifecycle--end-to-end-trace)
9. [Concurrency Model — Visual Map](#9-concurrency-model--visual-map)
10. [Error Handling Patterns](#10-error-handling-patterns)
11. [Infrastructure & Deployment](#11-infrastructure--deployment)
12. [Go Idiom Reference](#12-go-idiom-reference)

---

## 1. System Overview

Maya Genie is a **multi-service AI assistant** for Mandala Foods Nepal. The user asks nutrition questions via a WebSocket chat widget; an AI (backed by a LangChain RAG pipeline) answers using a knowledge base of product documents stored in pgvector on Supabase.

**Three services, three languages, one Redis:**

| Service | Language | Port | Role |
|---|---|---|---|
| `channel-adapter` | Go 1.22 | 8081 | WebSocket gateway — public internet facing |
| `orchestrator` | Go 1.22 | 8082 | Session management + Redis Streams consumer |
| `cognitive-core` | Python 3.11 | 8083 | LangChain RAG + LLM — internal only |

**The golden rule:** Only `channel-adapter` is reachable from the internet. `orchestrator` and `cognitive-core` live exclusively on the internal Docker network.

---

## 2. Full Architecture Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PUBLIC INTERNET                                │
│                                                                             │
│   Browser / Chat Widget                                                     │
│   wss://maya.mandalafoods.co/ws?session_id=<uuid>                          │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │  WebSocket (TLS via Traefik)
                               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         TRAEFIK REVERSE PROXY                               │
│                                                                             │
│  Host: maya.mandalafoods.co → channel-adapter:8081  (maya-ws router)       │
│  Host: maya.mandalafoods.co/admin/* → cognitive-core:8083 (maya-admin)     │
│  HTTP → HTTPS redirect on :80                                               │
│  TLS via Let's Encrypt (ACME httpChallenge)                                 │
│  WebSocket Upgrade headers forwarded automatically                          │
└────────────────┬─────────────────────────────────────────────────────────────┘
                 │
    ┌────────────▼────────────┐
    │    channel-adapter      │  Go 1.22 — net/http + gorilla/websocket
    │        :8081            │
    │                         │
    │  ┌─────────────────┐   │
    │  │  WSHandler      │   │
    │  │  ServeHTTP()    │   │
    │  │                 │   │
    │  │  1. Upgrade     │   │
    │  │  2. Assign/     │   │
    │  │     Accept      │   │
    │  │     session_id  │   │
    │  │  3. Subscribe   │   │
    │  │     Redis PS    │   │
    │  │  4. goroutine → │   │
    │  │     PS → WS     │   │
    │  │  5. Loop:       │   │
    │  │     WS → XAdd   │   │
    │  └─────────────────┘   │
    └────────────┬────────────┘
                 │
    ┌────────────▼────────────────────────────────┐
    │                  REDIS 7                    │
    │                                             │
    │  Streams:                                   │
    │    msg:inbound  ──► orchestrator reads      │
    │    (XADD / XREADGROUP consumer group)       │
    │                                             │
    │  Pub/Sub channels:                          │
    │    response:{session_id}  ──► adapter reads │
    │                                             │
    │  String keys:                               │
    │    session:{session_id}  → JSON history     │
    │    TTL: 24 hours, max 10 messages           │
    └─────────────────┬───────────────────────────┘
                      │
    ┌─────────────────▼───────────┐
    │       orchestrator          │  Go 1.22 — redis/go-redis v9
    │           :8082             │
    │                             │
    │  ┌─────────────────────┐   │
    │  │  ConsumeLoop()      │   │
    │  │  (goroutine)        │   │
    │  │                     │   │
    │  │  XREADGROUP block   │   │
    │  │  5s timeout         │   │
    │  │  → handleMessage()  │   │
    │  │    1. Unmarshal     │   │
    │  │    2. Publish       │   │
    │  │       "typing"      │   │
    │  │    3. LoadHistory   │   │
    │  │    4. HTTP POST →   │   │
    │  │       cognitive     │   │
    │  │    5. AppendHistory │   │
    │  │    6. Publish resp  │   │
    │  │    7. XACK          │   │
    │  └─────────────────────┘   │
    └─────────────┬───────────────┘
                  │  HTTP POST /chat  (internal Docker network only)
    ┌─────────────▼───────────────┐
    │      cognitive-core         │  Python 3.11 — FastAPI + LangChain
    │          :8083              │
    │                             │
    │  POST /chat                 │
    │  POST /admin/ingest         │
    │  GET  /health               │
    │                             │
    │  RAG Pipeline:              │
    │  ┌───────────────────────┐  │
    │  │ ConversationalRetrieval│  │
    │  │ Chain                  │  │
    │  │  ┌──────────────────┐ │  │
    │  │  │  PGVector        │ │  │
    │  │  │  Supabase        │ │  │
    │  │  │  mandala_        │ │  │
    │  │  │  public_kb       │ │  │
    │  │  └──────────────────┘ │  │
    │  │  ┌──────────────────┐ │  │
    │  │  │ GeminiREST       │ │  │
    │  │  │ Embeddings       │ │  │
    │  │  │ (embed-001)      │ │  │
    │  │  └──────────────────┘ │  │
    │  │  ┌──────────────────┐ │  │
    │  │  │ LLM (pluggable)  │ │  │
    │  │  │ Claude/GPT/Gemini│ │  │
    │  │  └──────────────────┘ │  │
    │  └───────────────────────┘  │
    └─────────────────────────────┘
```

### Network Topology

```
┌──────────────────────────────────────────────────────────┐
│  Docker Networks                                         │
│                                                          │
│  ┌──────────────── proxy (external) ─────────────────┐  │
│  │  Traefik ◄──► channel-adapter ◄──► (TLS only)     │  │
│  │               cognitive-core  ◄──► /admin only    │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────── internal (bridge) ────────────────┐  │
│  │  redis ◄──► channel-adapter                       │  │
│  │  redis ◄──► orchestrator                          │  │
│  │  redis ◄──► cognitive-core                        │  │
│  │  cognitive-core ◄──► orchestrator (HTTP)          │  │
│  └───────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 3. The Shared Contract: MessageEnvelope

Both Go services define an **identical** `MessageEnvelope` struct. This is the shared language of the system — every message that flows through Redis Streams is a JSON-serialized `MessageEnvelope`.

```go
// services/channel-adapter/models/envelope.go
// services/orchestrator/models/envelope.go  (identical struct)

type MessageEnvelope struct {
    MessageID string          `json:"message_id"` // uuid v4 — unique per message
    SessionID string          `json:"session_id"` // uuid v4 — browser tab lifetime
    Channel   string          `json:"channel"`    // "web", future: "whatsapp", "sms"
    UserID    string          `json:"user_id"`    // "anonymous" in Phase 1
    Timestamp time.Time       `json:"timestamp"`  // UTC, set at ingestion point
    Content   MessageContent  `json:"content"`
    Metadata  MessageMetadata `json:"metadata"`
}

type MessageContent struct {
    Type string `json:"type"` // always "text" today
    Text string `json:"text"` // the user's message
}

type MessageMetadata struct {
    Language     string                 `json:"language"`      // "en", "ne"
    PlatformData map[string]interface{} `json:"platform_data"` // channel-specific extras
}
```

**Why duplicate the struct?** Each service is its own Go module (`module channel-adapter`, `module orchestrator`). There is no shared library. This is intentional in microservice design — it avoids tight coupling via shared packages. The duplication is small and the serialization contract (JSON field names via struct tags) is what actually matters.

**Go struct tags** (`json:"..."`) tell `encoding/json` what field name to use during Marshal/Unmarshal. Without them, Go would use the capitalized field name as-is. The backtick syntax is a Go raw string literal used for struct tag metadata.

---

## 4. Service: channel-adapter (Go)

### 4.1 Entry Point & Dependency Wiring

```go
// services/channel-adapter/main.go

func main() {
    // 1. Read config from environment (twelve-factor app pattern)
    redisURL := os.Getenv("REDIS_URL")
    if redisURL == "" {
        redisURL = "redis://localhost:6379"  // sensible default for local dev
    }
    port := os.Getenv("PORT")
    if port == "" {
        port = "8081"
    }
    allowedOriginsStr := os.Getenv("ALLOWED_ORIGINS")
    var allowedOrigins []string
    if allowedOriginsStr != "" {
        allowedOrigins = strings.Split(allowedOriginsStr, ",")
    }

    // 2. Build the Redis client — this is a connection pool, not a single conn
    opts, err := redis.ParseURL(redisURL)
    if err != nil {
        log.Fatalf("Invalid REDIS_URL: %v", err)  // Fatalf = log + os.Exit(1)
    }
    rdb := redis.NewClient(opts)

    // 3. Wire dependencies by value — pass rdb into the handler
    wsHandler := handlers.NewWSHandler(rdb, allowedOrigins)

    // 4. Register routes on the standard library's ServeMux
    mux := http.NewServeMux()
    mux.Handle("/ws", wsHandler)       // wsHandler implements http.Handler
    mux.HandleFunc("/health", ...)     // inline func for simple routes

    // 5. Start blocking — ListenAndServe blocks until error or shutdown
    log.Printf("Channel adapter listening on :%s", port)
    if err := http.ListenAndServe(fmt.Sprintf(":%s", port), mux); err != nil {
        log.Fatalf("Server error: %v", err)
    }
}
```

**Key Go patterns here:**

- **`log.Fatalf`**: Calls `log.Printf` then `os.Exit(1)`. Use at startup for unrecoverable configuration errors. Never use mid-request.
- **`%v` format verb**: The "default" formatter. For errors it prints the error message. For structs it prints `{field1 field2 ...}`.
- **`%w` (in `fmt.Errorf`)**: Wraps an error, preserving its chain for `errors.Is` / `errors.As` unwrapping. You'll see this in deeper layers.
- **`http.ServeMux`**: Go's built-in router. No third-party dependency needed for simple route matching.

### 4.2 The WSHandler Type — Go's Interface Pattern

```go
// services/channel-adapter/handlers/websocket.go

type WSHandler struct {
    rdb            *redis.Client
    allowedOrigins map[string]bool  // O(1) lookup map, not a slice
}

func NewWSHandler(rdb *redis.Client, allowedOrigins []string) *WSHandler {
    origins := make(map[string]bool)  // make() allocates the map
    for _, o := range allowedOrigins {
        origins[o] = true
    }
    return &WSHandler{rdb: rdb, allowedOrigins: origins}
}
```

`WSHandler` implements `http.Handler` implicitly — it has a `ServeHTTP(w http.ResponseWriter, r *http.Request)` method with the exact signature the interface requires. Go interfaces are satisfied structurally, not by declaration.

```go
// This line in main.go works because WSHandler has ServeHTTP():
mux.Handle("/ws", wsHandler)  // mux.Handle expects an http.Handler
```

**The `make()` pattern**: Slices, maps, and channels in Go must be initialized before use. `make(map[string]bool)` creates an empty, ready-to-use map. Accessing an uninitialized nil map reads are safe (returns zero value), but writes panic.

**Converting `[]string` → `map[string]bool`**: Origin lookups happen on every WebSocket connection. A map lookup is O(1); a slice scan is O(n). The constructor does the conversion once at startup — a classic Go initialization pattern.

### 4.3 WebSocket Upgrade — HTTP → Full-Duplex TCP

```go
// Package-level upgrader — shared across all connections
var upgrader = websocket.Upgrader{
    CheckOrigin: func(r *http.Request) bool {
        return true // placeholder; overridden per-handler call below
    },
}

func (h *WSHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // Inject the real origin checker (captures h.allowedOrigins via closure)
    upgrader.CheckOrigin = h.checkOrigin

    conn, err := upgrader.Upgrade(w, r, nil)
    if err != nil {
        log.Printf("WebSocket upgrade failed: %v", err)
        return  // Upgrade already wrote an HTTP 400 to w
    }
    defer conn.Close()  // Always close when ServeHTTP returns
    // ...
}
```

**What `Upgrade()` does internally:**
1. Verifies `Connection: Upgrade` and `Upgrade: websocket` headers exist.
2. Calls `CheckOrigin(r)` — returns 403 if false.
3. Performs the WebSocket handshake (101 Switching Protocols).
4. Returns a `*websocket.Conn` wrapping the underlying TCP connection.

**`defer conn.Close()`**: The `defer` keyword schedules the call for when the surrounding function (`ServeHTTP`) returns — regardless of which return path is taken (normal return, panic, early return). This guarantees cleanup. It's equivalent to Java's `finally` block.

**Session ID resolution:**
```go
sessionID := r.URL.Query().Get("session_id")
if sessionID == "" {
    sessionID = uuid.New().String()  // generate if client didn't provide one
}
```

Clients reconnecting (browser refresh, mobile app resume) can provide their old `session_id` to resume conversation history. New clients get a fresh UUID v4.

### 4.4 Origin Checking — Security at the Handler Layer

```go
func (h *WSHandler) checkOrigin(r *http.Request) bool {
    if len(h.allowedOrigins) == 0 {
        return true  // dev mode: allow everything when ALLOWED_ORIGINS is unset
    }
    origin := r.Header.Get("Origin")
    if origin == "" {
        return true  // non-browser clients (curl, Postman) have no Origin header
    }
    return h.allowedOrigins[origin]  // map lookup: true if present, false if not
}
```

In production (`docker-compose.prod.yml`):
```
ALLOWED_ORIGINS=https://mandalafoods.co,https://www.mandalafoods.co,https://maya.mandalafoods.co,http://localhost:3000
```

The comma-separated string is split at startup into the `map[string]bool`. This is a **CORS-equivalent guard at the WebSocket layer** — browsers always send an `Origin` header on WebSocket upgrades, so only listed origins can connect.

### 4.5 Context Propagation & Cancellation

```go
func (h *WSHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // ...upgrade...

    // Create a cancellable context rooted at the request context.
    // r.Context() is cancelled when the HTTP server shuts down.
    ctx, cancel := context.WithCancel(r.Context())
    defer cancel()  // cancel is always called when ServeHTTP returns

    // ...rest of handler
}
```

**`context.WithCancel`** creates a child context and a `cancel` function. Calling `cancel()` signals all code holding this `ctx` to stop work. It's a cancellation tree:

```
context.Background()
  └─ r.Context()           (cancelled when HTTP server shuts down)
       └─ ctx (ours)       (cancelled when cancel() is called, OR when parent cancels)
```

When the WebSocket disconnects, `ServeHTTP` returns, `defer cancel()` fires, which cancels `ctx`. The goroutine listening to Redis pub/sub (below) sees `<-ctx.Done()` close and exits cleanly.

**This is Go's answer to "how do I stop a goroutine?"** — you pass it a context.

### 4.6 Goroutines — Spawning the Response Listener

```go
// Subscribe to the Redis pub/sub channel for this session
responseCh := fmt.Sprintf("response:%s", sessionID)
pubsub := h.rdb.Subscribe(ctx, responseCh)
defer pubsub.Close()

// Launch a goroutine to forward pub/sub messages → WebSocket
go func() {
    ch := pubsub.Channel()  // returns a Go channel (<-chan *redis.Message)
    for {
        select {
        case <-ctx.Done():
            return  // context cancelled — WebSocket closed, clean exit
        case msg, ok := <-ch:
            if !ok {
                return  // Redis channel closed
            }
            var resp models.WSResponse
            if err := json.Unmarshal([]byte(msg.Payload), &resp); err != nil {
                log.Printf("Failed to unmarshal response: %v", err)
                continue  // skip bad message, keep running
            }
            if err := conn.WriteJSON(resp); err != nil {
                log.Printf("Failed to write to WebSocket: %v", err)
                cancel()  // tell the main loop to stop too
                return
            }
        }
    }
}()
```

**`go func() { ... }()`** — the `go` keyword spawns a new goroutine. The `func() { ... }()` is an anonymous function (closure) being defined and immediately called. Goroutines are lightweight (~2KB stack, grows dynamically) — Go can run hundreds of thousands concurrently.

**The goroutine captures variables from the outer scope** (`ctx`, `cancel`, `conn`, `pubsub`) via closure. This is how Go goroutines share state — through captured references and channels, not global variables.

**Why a goroutine here?** The main loop (below) blocks on `conn.ReadMessage()` waiting for *incoming* WebSocket messages. Meanwhile, we need to *also* listen for responses from Redis and send them *out* on the WebSocket. Two simultaneous blocking reads require two goroutines (or one goroutine + select with non-blocking reads, but that pattern is messier here).

### 4.7 The `select` Statement — Concurrent Fan-in

```go
select {
case <-ctx.Done():
    return
case msg, ok := <-ch:
    if !ok {
        return
    }
    // process msg
}
```

`select` is Go's multiplexer for channels — it blocks until one of its cases is ready, then executes that case. If multiple cases are ready simultaneously, Go picks one at random (fair scheduling).

**`ctx.Done()`** returns a channel that is closed when the context is cancelled. `<-ctx.Done()` on a closed channel returns immediately with the zero value — that's how the cancellation signal propagates.

**Two-value receive `msg, ok := <-ch`**: When `ok` is `false`, the channel was closed. A closed channel always returns immediately with `(zero-value, false)`. Checking `ok` prevents infinite loops when the channel closes.

### 4.8 Redis Pub/Sub — Receiving Responses

```
Response flow:
  orchestrator                Redis                  channel-adapter
       │                        │                          │
       │─── PUBLISH ───────────►│                          │
       │  response:{session_id} │◄─── Subscribe ───────────│
       │  {"type":"message"...} │                          │
       │                        │──── msg payload ────────►│
       │                        │                          │── WriteJSON → WS
```

The orchestrator publishes a JSON `WSResponse` to `response:{session_id}`. The channel-adapter's goroutine receives it via `pubsub.Channel()` (a Go channel backed by a background goroutine in the Redis client library) and writes it to the WebSocket connection.

**Why pub/sub for responses, but streams for inbound?** Streams guarantee delivery (messages persist until ACKed). Pub/sub is fire-and-forget (no persistence) but has lower latency. Inbound messages must not be lost (stream); responses going to a specific live WebSocket connection can be fire-and-forget (pub/sub) because if the connection is dead, there's nobody to receive anyway.

### 4.9 Redis Streams — Publishing Inbound Messages

```go
// Main read loop — blocking wait on WebSocket
for {
    _, message, err := conn.ReadMessage()
    if err != nil {
        if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
            log.Printf("WebSocket closed unexpectedly: %v", err)
        }
        return  // clean exit from ServeHTTP — defer cleanup runs
    }

    var incoming models.WSIncoming
    if err := json.Unmarshal(message, &incoming); err != nil {
        log.Printf("Invalid message format: %v", err)
        conn.WriteJSON(models.WSResponse{
            Type: "error",
            Text: "Invalid message format. Send JSON with a 'text' field.",
        })
        continue  // bad message: tell the client, keep the connection
    }

    if incoming.Text == "" {
        continue  // silently drop empty messages
    }

    // Build the canonical envelope
    envelope := adapters.NormalizeWebMessage(sessionID, incoming.Text)
    envelopeJSON, err := json.Marshal(envelope)
    if err != nil {
        log.Printf("Failed to marshal envelope: %v", err)
        continue
    }

    // XADD — append to stream
    if err := h.rdb.XAdd(ctx, &redis.XAddArgs{
        Stream: "msg:inbound",
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
```

**`conn.ReadMessage()`** blocks the current goroutine until:
- A complete WebSocket frame arrives → returns `(messageType, []byte, nil)`.
- The connection closes → returns `(0, nil, err)`.
- An unexpected error → returns `(0, nil, err)`.

**`websocket.IsUnexpectedCloseError`** distinguishes intentional closes (browser tab closed = `CloseGoingAway`; clean disconnect = `CloseNormalClosure`) from network errors. Only unexpected errors warrant a log line — intentional closes are normal.

**`for { }` with no condition** is Go's idiomatic infinite loop, equivalent to `while(true)` in other languages. The `return` inside `if err != nil` is how you break out.

**`XAdd` with `Values: map[string]interface{}`**: Redis Streams messages are key-value maps. We use a single key `"envelope"` with the full JSON blob as the value. The orchestrator reads this same key.

### 4.10 The Adapter Layer — NormalizeWebMessage

```go
// services/channel-adapter/adapters/web.go

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
```

The `adapters/` package is the **normalization layer**. Raw WebSocket input (`WSIncoming{Text: "..."}`) is converted into the canonical `MessageEnvelope`. When WhatsApp or SMS channels are added, they'll each have an adapter in this package — different input shapes, same output shape.

**Named return / struct literal**: Go allows initializing a struct by listing field names explicitly. Any omitted fields get their zero value. This is preferred over positional initialization (which breaks when fields are reordered).

---

## 5. Service: orchestrator (Go)

### 5.1 Entry Point — Background Goroutines & Graceful Shutdown

```go
// services/orchestrator/main.go

func main() {
    // ...config, redis setup...

    ctx, cancel := context.WithCancel(context.Background())
    defer cancel()

    // Verify Redis connection at startup — fail fast
    if err := rdb.Ping(ctx).Err(); err != nil {
        log.Fatalf("Failed to connect to Redis: %v", err)
    }

    sessionMgr := session.NewManager(rdb)
    r := router.New(rdb, sessionMgr, cognitiveURL)

    // Initialize the consumer group in Redis (idempotent)
    if err := r.EnsureConsumerGroup(ctx); err != nil {
        log.Fatalf("Failed to create consumer group: %w", err)
    }

    // Launch consumer loop as a background goroutine
    go r.ConsumeLoop(ctx)  // runs forever until ctx is cancelled

    // HTTP server for health checks
    mux := http.NewServeMux()
    mux.HandleFunc("/health", ...)
    server := &http.Server{Addr: fmt.Sprintf(":%s", port), Handler: mux}

    // Graceful shutdown goroutine
    go func() {
        sigCh := make(chan os.Signal, 1)
        signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
        <-sigCh                // block until OS sends a signal
        log.Println("Shutting down...")
        cancel()               // cancel ctx → stops ConsumeLoop
        server.Close()         // stop accepting new HTTP requests
    }()

    // Block main goroutine here
    if err := server.ListenAndServe(); err != http.ErrServerClosed {
        log.Fatalf("Server error: %v", err)
    }
}
```

**`context.Background()`**: The root context. Never cancelled, never times out. Only used at the top level of `main()` or in tests. Everything else should use derived contexts.

**`go r.ConsumeLoop(ctx)`**: Launches the core of the service as a background goroutine. `main()` continues to the HTTP server setup and then blocks on `server.ListenAndServe()`.

**Checking `http.ErrServerClosed`**: `server.Close()` causes `ListenAndServe` to return `http.ErrServerClosed`. This is the expected error during graceful shutdown — we don't want to `log.Fatalf` on it.

### 5.2 Signal Handling — OS-Level Shutdown

```go
go func() {
    sigCh := make(chan os.Signal, 1)     // buffered channel — size 1
    signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
    <-sigCh
    log.Println("Shutting down...")
    cancel()
    server.Close()
}()
```

**`make(chan os.Signal, 1)`**: Creates a buffered channel with capacity 1. The `1` buffer is important — it prevents the signal from being dropped if the goroutine isn't ready to receive exactly when the signal arrives. `signal.Notify` requires a buffered channel.

**`syscall.SIGINT`**: Ctrl+C in a terminal.  
**`syscall.SIGTERM`**: Docker's default stop signal (`docker stop` sends SIGTERM, waits 10s, then sends SIGKILL).

**`<-sigCh`**: Blocks until one of the specified signals arrives. Then we cancel the shared context (stopping `ConsumeLoop`) and close the HTTP server.

**Why a goroutine for signal handling?** Because `<-sigCh` is a blocking operation. If run in `main()`, it would block before `server.ListenAndServe()`. Putting it in a goroutine lets both run concurrently.

### 5.3 Router Type — Struct-Based Dependency Injection

```go
// services/orchestrator/router/router.go

const (
    streamKey      = "msg:inbound"
    consumerGroup  = "orchestrator-group"
    consumerName   = "orchestrator-1"
    responsePrefix = "response:"
    httpTimeout    = 60 * time.Second
)

type Router struct {
    rdb          *redis.Client
    sessionMgr   *session.Manager
    cognitiveURL string
    httpClient   *http.Client  // shared, reusable HTTP client
}

func New(rdb *redis.Client, sessionMgr *session.Manager, cognitiveURL string) *Router {
    return &Router{
        rdb:          rdb,
        sessionMgr:   sessionMgr,
        cognitiveURL: cognitiveURL,
        httpClient:   &http.Client{Timeout: httpTimeout},
    }
}
```

**Constants block** (`const (...)`): Go convention for grouping related constants. Constants are evaluated at compile time.

**Shared `*http.Client`**: The HTTP client is created once in the constructor and reused for all requests. `http.Client` manages a connection pool internally. Creating a new `http.Client` per request would not reuse connections and would be wasteful. The 60-second timeout accounts for LLM inference latency.

**`60 * time.Second`**: `time.Duration` arithmetic. `time.Second` is a constant equal to `1,000,000,000` nanoseconds. Multiplying by 60 gives a 60-second duration. Go uses `time.Duration` (a `int64` of nanoseconds) throughout the standard library — no magic string parsing like `"60s"`.

### 5.4 Redis Streams Consumer Group — XREADGROUP

**What is a Consumer Group?**

```
Stream: msg:inbound
  ┌─────┬────────────────────────────────────────────────────┐
  │ ID  │ Values                                             │
  ├─────┼────────────────────────────────────────────────────┤
  │1001 │ envelope: {...}                                     │
  │1002 │ envelope: {...}                                     │
  │1003 │ envelope: {...}   ◄── ">" means new (undelivered)  │
  └─────┴────────────────────────────────────────────────────┘
              │
  Consumer Group: "orchestrator-group"
              │
  Consumer: "orchestrator-1"
    Reads  ">" (new messages only)
    Moves  delivered-but-not-ACKed messages to PEL (Pending Entry List)
    After  XACK: message is removed from PEL
```

**Consumer groups allow:**
- Multiple consumers to process different messages concurrently (horizontal scaling).
- At-least-once delivery (messages stay in PEL until ACKed).
- Recovery: if a consumer dies mid-process, the message stays in PEL and can be reclaimed.

```go
func (r *Router) EnsureConsumerGroup(ctx context.Context) error {
    err := r.rdb.XGroupCreateMkStream(ctx, streamKey, consumerGroup, "0").Err()
    // "0" means: start reading from the beginning of the stream
    // MkStream = create the stream if it doesn't exist
    if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
        return fmt.Errorf("failed to create consumer group: %w", err)
    }
    return nil
}
```

**Idempotent setup**: `XGroupCreateMkStream` returns `BUSYGROUP` error if the group already exists. We ignore that specific error — this makes the function safe to call on every startup.

### 5.5 The Consume Loop — Blocking Poll Pattern

```go
func (r *Router) ConsumeLoop(ctx context.Context) {
    log.Println("Starting consumer loop...")
    for {
        // Check if we should exit before blocking
        select {
        case <-ctx.Done():
            return
        default:
            // continue to the blocking read below
        }

        streams, err := r.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
            Group:    consumerGroup,
            Consumer: consumerName,
            Streams:  []string{streamKey, ">"},  // ">" = only new messages
            Count:    1,                          // one message at a time
            Block:    5 * time.Second,            // block for up to 5 seconds
        }).Result()

        if err == redis.Nil || err != nil && ctx.Err() != nil {
            continue  // timeout or context cancelled — loop again
        }
        if err != nil {
            log.Printf("Error reading stream: %v", err)
            time.Sleep(1 * time.Second)  // backoff on real errors
            continue
        }

        for _, stream := range streams {
            for _, msg := range stream.Messages {
                r.handleMessage(ctx, msg)  // synchronous — processes one at a time
            }
        }
    }
}
```

**The `select { default: }` pattern**: A non-blocking check on `ctx.Done()`. Without it, if the context is cancelled while `XReadGroup` is blocked, the Redis call unblocks (because the context is passed to it), we'd check the error and loop back — the `default` case provides an early exit before re-entering the blocking call. This is a subtle but correct pattern.

**`Block: 5 * time.Second`**: `XREADGROUP BLOCK 5000` — if no new messages arrive in 5 seconds, the command returns `redis.Nil`. This is better than a tight polling loop (which would hammer Redis at 100% CPU doing nothing).

**`Count: 1`**: Process one message at a time. Simple and correct. For higher throughput, increase `Count` and process messages concurrently with goroutines — not done here (Phase 1 design).

**`redis.Nil`**: The go-redis library returns `redis.Nil` (not an actual error) when a blocking command times out with no results. Treating it as `continue` is correct — just loop and try again.

### 5.6 Message Processing Pipeline

```go
func (r *Router) handleMessage(ctx context.Context, msg redis.XMessage) {
    // 1. Extract envelope JSON from stream message values
    envelopeJSON, ok := msg.Values["envelope"].(string)
    if !ok {
        log.Printf("Invalid message format, missing envelope field: %s", msg.ID)
        r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)  // ACK bad messages so they don't requeue
        return
    }

    // 2. Deserialize
    var envelope models.MessageEnvelope
    if err := json.Unmarshal([]byte(envelopeJSON), &envelope); err != nil {
        log.Printf("Failed to unmarshal envelope: %v", err)
        r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)
        return
    }

    sessionID := envelope.SessionID

    // 3. Publish typing indicator (immediate user feedback)
    r.publishResponse(ctx, sessionID, models.WSResponse{Type: "typing"})

    // 4. Load conversation history from Redis
    history, err := r.sessionMgr.LoadHistory(ctx, sessionID)
    if err != nil {
        log.Printf("Failed to load history: %v", err)
        history = []models.ConversationMessage{}  // fallback: start fresh
    }

    // 5. Build the request for cognitive-core
    chatReq := models.ChatRequest{
        SessionID:           sessionID,
        Message:             envelope.Content.Text,
        ConversationHistory: history,
        Channel:             envelope.Channel,
        Language:            envelope.Metadata.Language,
    }

    // 6. HTTP call to cognitive-core (blocking — this is where latency lives)
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

    // 7. Persist updated conversation history
    if err := r.sessionMgr.AppendMessages(ctx, sessionID, envelope.Content.Text, chatResp.Response); err != nil {
        log.Printf("Failed to save history: %v", err)
        // Non-fatal: log and continue — the response can still be sent
    }

    // 8. Send response back to the WebSocket client via pub/sub
    r.publishResponse(ctx, sessionID, models.WSResponse{
        Type:      "message",
        Text:      chatResp.Response,
        SessionID: sessionID,
    })

    // 9. Acknowledge — removes from PEL
    r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)
}
```

**Type assertion `msg.Values["envelope"].(string)`**: Redis stream message values are `map[string]interface{}`. The `.(string)` asserts the value is a string. The two-value form `val, ok := iface.(string)` is safe (won't panic). Using the single-value form `val := iface.(string)` panics if the type is wrong — always use the two-value form for safety.

**Error strategy**: ACK even on parse errors (steps 1-2). If we don't ACK bad messages, they stay in the PEL forever, clogging the queue. Log them for debugging, but move on. Only for genuine processing failures (cognitive-core down) do we still ACK — this is an at-least-once delivery tradeoff. An at-exactly-once system would require more complex dead letter queue logic.

**Typing indicator timing**: The `"typing"` pub/sub is sent immediately after parsing, before calling cognitive-core. The user sees the typing indicator appear, then after the LLM call completes, the actual response arrives. This is the pattern every messaging app uses.

### 5.7 HTTP Client — Calling cognitive-core

```go
func (r *Router) callCognitiveCore(ctx context.Context, req models.ChatRequest) (*models.ChatResponse, error) {
    body, err := json.Marshal(req)
    if err != nil {
        return nil, fmt.Errorf("failed to marshal request: %w", err)
    }

    url := fmt.Sprintf("%s/chat", r.cognitiveURL)

    // http.NewRequestWithContext: context-aware request — will abort if ctx is cancelled
    httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
    if err != nil {
        return nil, fmt.Errorf("failed to create request: %w", err)
    }
    httpReq.Header.Set("Content-Type", "application/json")

    resp, err := r.httpClient.Do(httpReq)
    if err != nil {
        return nil, fmt.Errorf("HTTP request failed: %w", err)
    }
    defer resp.Body.Close()  // always close body to free the connection back to pool

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
```

**`http.NewRequestWithContext`** vs `http.NewRequest`: The context-aware version will abort the in-flight HTTP request if `ctx` is cancelled (e.g., orchestrator shutting down). Always use `WithContext` in server code.

**`bytes.NewReader(body)`**: `http.NewRequest` expects an `io.Reader` for the body. `bytes.NewReader` wraps a `[]byte` as an `io.Reader`. The alternative `bytes.NewBuffer(body)` also works but is subtly different (buffer can be written to; reader is read-only).

**`defer resp.Body.Close()`**: Critical. If you forget to close the response body, the underlying TCP connection can't be returned to the connection pool, eventually exhausting all available connections. Go will not close it for you.

**`fmt.Errorf("...: %w", err)`**: The `%w` verb wraps the error. Callers can use `errors.Is(err, someSpecificErr)` or `errors.As(err, &target)` to inspect the chain. Using `%v` instead loses the wrapping — the error message is the same but the type chain is broken.

### 5.8 Publishing Responses — Redis Pub/Sub

```go
func (r *Router) publishResponse(ctx context.Context, sessionID string, resp models.WSResponse) {
    data, err := json.Marshal(resp)
    if err != nil {
        log.Printf("Failed to marshal response: %v", err)
        return
    }
    channel := fmt.Sprintf("%s%s", responsePrefix, sessionID)  // "response:{uuid}"
    if err := r.rdb.Publish(ctx, channel, string(data)).Err(); err != nil {
        log.Printf("Failed to publish response: %v", err)
    }
}
```

**`WSResponse` type used across both services:**
```go
type WSResponse struct {
    Type      string `json:"type"`               // "connected", "typing", "message", "error"
    Text      string `json:"text,omitempty"`      // omitempty: field excluded from JSON if empty string
    SessionID string `json:"session_id,omitempty"`
}
```

**`omitempty`** struct tag option: When marshaling to JSON, fields with this option are omitted entirely if the field is the zero value (empty string for `string`). So a `typing` message serializes to `{"type":"typing"}` with no wasted `text` or `session_id` fields.

### 5.9 Session Manager

```go
// services/orchestrator/session/manager.go

const (
    sessionTTL    = 24 * time.Hour
    maxMessages   = 10
    sessionPrefix = "session:"
)

type Manager struct {
    rdb *redis.Client
}

func (m *Manager) LoadHistory(ctx context.Context, sessionID string) ([]models.ConversationMessage, error) {
    key := fmt.Sprintf("%s%s", sessionPrefix, sessionID)  // "session:{uuid}"
    data, err := m.rdb.Get(ctx, key).Bytes()
    if err == redis.Nil {
        // Key doesn't exist yet — new session
        return []models.ConversationMessage{}, nil  // return empty slice, not nil
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
    // Sliding window: keep only the last maxMessages
    if len(history) > maxMessages {
        history = history[len(history)-maxMessages:]  // slice of last 10 elements
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
```

**`redis.Nil` sentinel**: The go-redis library returns `redis.Nil` (not a real error — a sentinel value) when a key doesn't exist. This is analogous to SQL's `NO ROWS FOUND`. Always check for it before treating the error as a real failure.

**Nil vs empty slice**: `return []models.ConversationMessage{}, nil` returns an empty slice (not `nil`). In Go, `nil` and an empty slice are different: `len(nil) == 0` and `len([]T{}) == 0` both work, but JSON marshaling differs: `nil` → `null`, `[]T{}` → `[]`. Returning an empty slice is safer for consumers.

**`history[len(history)-maxMessages:]`**: Slice expression. Go slices are views over arrays. `s[low:high]` creates a new slice header pointing to the same underlying array from index `low` to `high` (exclusive). No copying happens. `history[len(history)-10:]` takes the last 10 elements.

**`append(history, a, b)`**: `append` can take multiple elements. Both `ConversationMessage` structs are appended in one call.

---

## 6. Service: cognitive-core (Python)

A FastAPI service. Not Go — but important to understand how the Go orchestrator calls it.

**Request from orchestrator:**
```json
{
  "session_id": "uuid",
  "message": "What is in the Kiwi Crush?",
  "conversation_history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "channel": "web",
  "language": "en"
}
```

**Response to orchestrator:**
```json
{
  "session_id": "uuid",
  "response": "Kiwi Crush contains...",
  "sources": ["mandalafoods_knowledge_base_page_3"],
  "model_used": "claude-sonnet-4-20250514"
}
```

**LLM Factory (`llm/client.py`)** — pluggable providers via `LLM_PROVIDER` env var:

| `LLM_PROVIDER` | Library | Default Model |
|---|---|---|
| `anthropic` | `langchain_anthropic` | `claude-sonnet-4-20250514` |
| `openai` | `langchain_openai` | `gpt-4o-mini` |
| `gemini` | `langchain_google_genai` | `gemini-2.0-flash` |
| `claude-code` | `langchain_openai` (compat) | `claude-sonnet-4-6` via custom base URL |

**Embeddings**: Always Gemini `gemini-embedding-001` via direct REST API (bypasses gRPC, works with free AI Studio keys). The `GeminiRESTEmbeddings` class implements LangChain's `Embeddings` interface.

**RAG pipeline**: `ConversationalRetrievalChain` (LangChain) → PGVector similarity search (k=4 chunks) → LLM completion with retrieved context injected into the prompt.

---

## 7. Redis as the Nervous System

Redis serves three distinct roles simultaneously:

### Role 1: Streams (Inbound Message Queue)

```
Key:   msg:inbound
Type:  Stream
Used:  channel-adapter → (XADD) → orchestrator (XREADGROUP/XACK)

Message format:
  ID:     auto-generated (timestamp-based, e.g. "1234567890123-0")
  Fields: { "envelope": "<JSON string>" }
```

Streams provide **at-least-once delivery** with consumer group semantics. If the orchestrator crashes mid-processing, the message stays in the PEL and can be reclaimed on restart (not implemented in Phase 1, but the infrastructure is there).

### Role 2: Pub/Sub (Response Delivery)

```
Channel pattern:  response:{session_id}
Publisher:        orchestrator (PUBLISH)
Subscriber:       channel-adapter goroutine (SUBSCRIBE)

Message format:  JSON WSResponse
  {"type":"typing"}
  {"type":"message","text":"...","session_id":"..."}
  {"type":"error","text":"..."}
```

Pub/sub is transient — no persistence. The message is delivered only to currently subscribed clients. If the channel-adapter's goroutine isn't subscribed (e.g., the browser disconnected), the message is lost. This is acceptable because there's nobody to receive it anyway.

### Role 3: String Keys (Session Storage)

```
Key pattern:  session:{session_id}
Type:         String (JSON-encoded []ConversationMessage)
TTL:          24 hours
Max size:     10 messages (sliding window enforced by session.Manager)

Example value:
[
  {"role":"user","content":"What products do you have?"},
  {"role":"assistant","content":"We offer..."},
  ...
]
```

This is the short-term memory of the conversation. The orchestrator reads history on every message and writes it back after every response.

### Data Flow Summary

```
channel-adapter:
  XADD msg:inbound {"envelope": "{...JSON...}"}
  SUBSCRIBE response:{session_id}

orchestrator:
  XREADGROUP msg:inbound orchestrator-group orchestrator-1 > BLOCK 5000 COUNT 1
  GET session:{session_id}
  SET session:{session_id} [updated history] EX 86400
  PUBLISH response:{session_id} {"type":"typing"}
  PUBLISH response:{session_id} {"type":"message","text":"..."}
  XACK msg:inbound orchestrator-group {message_id}
```

---

## 8. Complete Message Lifecycle — End-to-End Trace

```
Time →

Browser
  │
  ├─[1]── WS connect: wss://maya.mandalafoods.co/ws?session_id=abc123
  │        Traefik terminates TLS, forwards to channel-adapter:8081
  │
channel-adapter (ServeHTTP goroutine for this connection)
  │
  ├─[2]── Upgrade HTTP → WebSocket
  ├─[3]── Send: {"type":"connected","session_id":"abc123"}
  ├─[4]── ctx, cancel = context.WithCancel(r.Context())
  ├─[5]── SUBSCRIBE response:abc123 (Redis pub/sub)
  ├─[6]── go func() { listen for pub/sub → forward to WS }
  │
  ├─[7]── ReadMessage() blocks...
  │
Browser
  ├─[8]── Send WS frame: {"text":"What is Kiwi Crush?"}
  │
channel-adapter
  ├─[9]── Unmarshal WSIncoming
  ├─[10]─ NormalizeWebMessage → MessageEnvelope{MessageID:"xyz", SessionID:"abc123", ...}
  ├─[11]─ XADD msg:inbound {envelope: "<JSON>"}
  ├─[12]─ ReadMessage() blocks again...
  │
orchestrator (ConsumeLoop goroutine)
  ├─[13]─ XREADGROUP → receives stream message
  ├─[14]─ handleMessage()
  ├─[15]─ PUBLISH response:abc123 {"type":"typing"}
  │
channel-adapter (pub/sub goroutine)
  ├─[16]─ receives "typing" from pub/sub channel
  ├─[17]─ WriteJSON(WSResponse{Type:"typing"}) → WebSocket
  │
Browser
  ├─[18]─ Displays "Maya is typing..."
  │
orchestrator
  ├─[19]─ GET session:abc123 → [] (new session, empty history)
  ├─[20]─ Build ChatRequest{SessionID:"abc123", Message:"What is Kiwi Crush?", History:[]}
  ├─[21]─ HTTP POST http://cognitive-core:8083/chat (blocks 1-5 seconds for LLM)
  │
cognitive-core
  ├─[22]─ build_chain(history=[])
  ├─[23]─ Embed query with Gemini REST API
  ├─[24]─ PGVector similarity search → 4 chunks from mandala_public_kb
  ├─[25]─ LLM call (Claude/Gemini/GPT) with retrieved context
  ├─[26]─ Return {"response":"Kiwi Crush is...", "sources":["..."], "model_used":"..."}
  │
orchestrator
  ├─[27]─ AppendMessages: history now has 2 messages (user + assistant)
  ├─[28]─ SET session:abc123 [updated history] EX 86400
  ├─[29]─ PUBLISH response:abc123 {"type":"message","text":"Kiwi Crush is...","session_id":"abc123"}
  ├─[30]─ XACK msg:inbound orchestrator-group {stream_msg_id}
  │
channel-adapter (pub/sub goroutine)
  ├─[31]─ receives "message" from pub/sub channel
  ├─[32]─ WriteJSON(WSResponse{Type:"message", Text:"Kiwi Crush is..."})
  │
Browser
  ├─[33]─ Displays AI response in chat UI
```

---

## 9. Concurrency Model — Visual Map

```
channel-adapter process
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  main goroutine                                               │
│  └── http.ListenAndServe  (blocks forever)                    │
│                                                               │
│  Per-connection goroutines (spawned by Go's net/http):        │
│  ┌───────────────────────────────────────────────────┐        │
│  │  ServeHTTP goroutine [conn: browser tab 1]        │        │
│  │  ├── blocks on conn.ReadMessage()  (inbound)      │        │
│  │  └── spawns: pub/sub goroutine                    │        │
│  │       └── select on ctx.Done | pubsub.Channel()   │        │
│  └───────────────────────────────────────────────────┘        │
│  ┌───────────────────────────────────────────────────┐        │
│  │  ServeHTTP goroutine [conn: browser tab 2]        │        │
│  │  ├── blocks on conn.ReadMessage()                 │        │
│  │  └── spawns: pub/sub goroutine                    │        │
│  └───────────────────────────────────────────────────┘        │
│  ... one goroutine pair per active WebSocket connection        │
└────────────────────────────────────────────────────────────────┘


orchestrator process
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  main goroutine                                               │
│  └── server.ListenAndServe  (HTTP health check, blocks)       │
│                                                               │
│  ConsumeLoop goroutine                                        │
│  └── XREADGROUP (blocks up to 5s per call)                    │
│       └── handleMessage() (synchronous, one at a time)        │
│            ├── publishResponse (typing)                       │
│            ├── HTTP POST → cognitive-core (blocks 1-5s)       │
│            ├── publishResponse (message)                      │
│            └── XACK                                           │
│                                                               │
│  Signal handler goroutine                                     │
│  └── <-sigCh (blocks until SIGINT/SIGTERM)                    │
└────────────────────────────────────────────────────────────────┘
```

**Key insight**: The orchestrator processes messages **sequentially** (one at a time). This is a deliberate Phase 1 simplification. To handle concurrent messages, you'd increase `XReadGroup.Count` and spawn a goroutine per message with `go r.handleMessage(ctx, msg)`. You'd then need to guard concurrent writes to `conn.WriteJSON` with a mutex (since `*websocket.Conn` is not goroutine-safe) — but for the orchestrator, there's no websocket connection; the serialization concern moves to the pub/sub publish calls, which Redis handles safely.

---

## 10. Error Handling Patterns

### Pattern 1: Fail Fast at Startup

```go
opts, err := redis.ParseURL(redisURL)
if err != nil {
    log.Fatalf("Invalid REDIS_URL: %v", err)  // exits process immediately
}
```

Configuration errors and infrastructure connection failures should crash the process immediately. A container that crashes restarts (via Docker's `restart: always` or Kubernetes). A container that starts but silently fails is much harder to debug.

### Pattern 2: Error Wrapping with `%w`

```go
// Deep in session/manager.go
return nil, fmt.Errorf("failed to load session: %w", err)

// Caller in router/router.go
history, err := r.sessionMgr.LoadHistory(ctx, sessionID)
if err != nil {
    // err.Error() will be: "failed to load session: failed to connect to redis: ..."
    log.Printf("Failed to load history: %v", err)
    history = []models.ConversationMessage{}  // graceful degradation
}
```

Wrapping adds context at each layer without losing the original error. The `%v` format on a wrapped error prints the full chain.

### Pattern 3: Graceful Degradation vs Hard Fail

| Situation | Strategy | Reason |
|---|---|---|
| Redis connection at startup fails | `log.Fatalf` — crash | Process is useless without Redis |
| Session history load fails mid-request | Use empty history, continue | User still gets a response, just without history |
| cognitive-core returns an error | Publish error message to user | User is notified; message is ACKed to prevent requeue |
| JSON parse fails on stream message | ACK + log + skip | Poison pill prevention |
| WebSocket write fails in goroutine | `cancel()` + return | Signal the main loop to exit cleanly |

### Pattern 4: Always ACK, Even on Error

```go
// If we can't process this message, ACK it anyway
// and send an error to the user
r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)
return
```

Not ACKing causes messages to stay in the PEL forever, eventually filling Redis memory and blocking new messages. For this system, it's better to ACK and log than to retry indefinitely without a dead letter queue mechanism.

### Pattern 5: Non-Fatal History Save Failure

```go
if err := r.sessionMgr.AppendMessages(ctx, sessionID, envelope.Content.Text, chatResp.Response); err != nil {
    log.Printf("Failed to save history: %v", err)
    // no return — continue to publish the response
}
```

History save failure is non-fatal. The user gets their answer. The next message will have incomplete history — a minor quality degradation, not a show-stopper. This is a deliberate tradeoff logged for observability.

---

## 11. Infrastructure & Deployment

### Docker Build — Multi-Stage (Both Go Services)

```dockerfile
# Stage 1: Build
FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download              # download dependencies (cached layer)
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /channel-adapter .

# Stage 2: Runtime (tiny image)
FROM alpine:latest
RUN apk --no-cache add ca-certificates  # needed for HTTPS calls
COPY --from=builder /channel-adapter /channel-adapter
EXPOSE 8081
CMD ["/channel-adapter"]
```

**Multi-stage builds**: Stage 1 uses the full Go toolchain (~400MB). Stage 2 uses a minimal Alpine Linux image (~5MB). Only the compiled binary is copied. Final image size: ~15MB vs ~400MB+.

**`CGO_ENABLED=0`**: Disables C Go (cgo) — the bridge between Go and C code. Disabling it produces a fully static binary with no shared library dependencies. This is required for Alpine (which uses musl libc, not glibc).

**`GOOS=linux`**: Cross-compilation target. The binary is built for Linux even if the developer machine runs macOS. Without this, `go build` on macOS produces a macOS binary that won't run in the Alpine container.

**Dependency caching**: `COPY go.mod go.sum ./` and `RUN go mod download` are separate from `COPY . .`. Docker caches layers. If only source files change (not `go.mod`), the `go mod download` layer is reused from cache — much faster rebuilds.

### Traefik Configuration

```yaml
# infra/traefik/traefik.yml

entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https     # All HTTP → HTTPS redirect
  websecure:
    address: ":443"

certificatesResolvers:
  letsencrypt:
    acme:
      email: admin@mandalafoods.co
      storage: /letsencrypt/acme.json
      httpChallenge:
        entryPoint: web   # ACME HTTP-01 challenge on port 80
```

**Docker labels in `docker-compose.prod.yml`** drive Traefik routing dynamically:

```yaml
# channel-adapter gets the WebSocket route
labels:
  - "traefik.http.routers.maya-ws.rule=Host(`maya.mandalafoods.co`)"
  - "traefik.http.routers.maya-ws.tls.certresolver=le"

# cognitive-core only gets /admin/* (for document ingestion)
labels:
  - "traefik.http.routers.maya-admin.rule=Host(`maya.mandalafoods.co`) && PathPrefix(`/admin`)"
```

Traefik automatically handles WebSocket `Upgrade` headers — no special configuration needed.

### Database — Supabase (PostgreSQL + pgvector)

```sql
-- migrations/001_initial.sql

CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector extension

-- Session metadata (not the history — that's in Redis)
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE,
    user_id UUID,        -- NULL = anonymous (Phase 1)
    channel TEXT NOT NULL DEFAULT 'web',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Full message log (for analytics/audit — separate from Redis session cache)
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- LangChain creates these automatically on first ingest:
--   langchain_pg_collection
--   langchain_pg_embedding  (stores vectors + metadata)
```

**Two sources of truth for conversations:**
- **Redis `session:{id}`**: Short-term, fast, for LLM context. 24h TTL, max 10 messages.
- **Postgres `messages` table**: Long-term, for analytics and audit. Populated by cognitive-core (currently) or future analytics layer.

---

## 12. Go Idiom Reference

A quick reference to Go-specific patterns found throughout this codebase:

### Interfaces (Implicit Satisfaction)

```go
// http.Handler interface (from standard library):
type Handler interface {
    ServeHTTP(ResponseWriter, *Request)
}

// WSHandler satisfies it without saying so:
func (h *WSHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) { ... }

// This works because Go uses structural typing:
mux.Handle("/ws", wsHandler)  // *WSHandler is implicitly an http.Handler
```

### Error as Value (Not Exception)

```go
// Go returns errors as the last return value
result, err := someFunction()
if err != nil {
    // handle it here — or wrap and return it
    return nil, fmt.Errorf("context: %w", err)
}
// use result — only reached if err is nil
```

### Goroutines + Channels

```go
ch := make(chan int)      // unbuffered: send blocks until receive
ch := make(chan int, 10)  // buffered: send blocks only when full

go func() { ch <- 42 }() // send in goroutine (non-blocking)
val := <-ch              // receive (blocks until value arrives)
```

### select — Multi-channel Multiplexer

```go
select {
case v := <-ch1:     // if ch1 has a value
    use(v)
case ch2 <- x:       // if ch2 can accept a value
    // sent
case <-time.After(5 * time.Second):
    // timeout
case <-ctx.Done():   // if context is cancelled
    return
default:             // non-blocking: execute if no other case is ready
    // ...
}
```

### Context — Cancellation and Deadlines

```go
// Creation
ctx := context.Background()                    // root, never cancelled
ctx, cancel := context.WithCancel(parent)      // cancel() cancels ctx
ctx, cancel := context.WithTimeout(parent, 5*time.Second) // auto-cancels after 5s
ctx, cancel := context.WithDeadline(parent, time.Now().Add(5*time.Second))

// Always call cancel to free resources (even if ctx expires naturally)
defer cancel()

// Checking
select {
case <-ctx.Done():
    return ctx.Err()  // context.Canceled or context.DeadlineExceeded
}
```

### Struct Tags

```go
type Foo struct {
    Name string `json:"name"`           // serializes as "name"
    Age  int    `json:"age,omitempty"`  // omitted from JSON if 0
    skip bool   // unexported = never serialized
}
```

### Nil Checks

```go
// redis.Nil is a sentinel error, not a real error
if err == redis.Nil {
    return defaultValue, nil  // key not found — not an error
}
if err != nil {
    return nil, err           // real error
}
```

### Defer Order (LIFO)

```go
defer conn.Close()     // runs third (last deferred, last to run → actually first registered)
defer pubsub.Close()   // runs second
defer cancel()         // runs first (last deferred, runs last)
// Defers execute in LIFO (Last In, First Out) order
```

### Package-Level Variables

```go
const streamKey = "msg:inbound"  // immutable, compile-time

var upgrader = websocket.Upgrader{...}  // mutable, package-level (use carefully)
```

Package-level `var` is initialized once when the package is first imported. In the channel-adapter, `upgrader` is package-level but has its `CheckOrigin` field overridden per request (`upgrader.CheckOrigin = h.checkOrigin`). This is a safe pattern here because all requests share the same allowed origins — but note that this is a subtle race condition if multiple goroutines modify `upgrader` simultaneously. In practice this works because the goroutines are all setting it to the same function. A cleaner design would create the upgrader inside `NewWSHandler`.

---

*End of Architecture & Go Deep Dive documentation.*
*Last updated: 2026-06-03*
