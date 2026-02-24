# Maya Genie — Code Walkthrough

A beginner-friendly deep dive into every service, the Go and Python concepts behind them, and how Redis glues everything together.

---

## Table of Contents

1. [The Big Picture — How a Message Flows](#1-the-big-picture)
2. [Go Crash Course — Concepts Used in This Project](#2-go-crash-course)
3. [Redis Crash Course — Streams and Pub/Sub](#3-redis-crash-course)
4. [Service 1: channel-adapter (Go)](#4-channel-adapter)
5. [Service 2: orchestrator (Go)](#5-orchestrator)
6. [Service 3: cognitive-core (Python)](#6-cognitive-core)
7. [How the MessageEnvelope Connects Everything](#7-messageenvelope)

---

## 1. The Big Picture

When a user sends "What is Seto Chiura?" from the website, here is exactly what happens, step by step:

```
User's Browser
     |
     | 1. WebSocket connection to ws://host:8081/ws
     v
channel-adapter (Go, port 8081)
     |
     | 2. Wraps the raw text into a MessageEnvelope (JSON)
     | 3. Publishes the envelope to Redis Streams (key: "msg:inbound")
     | 4. Subscribes to Redis Pub/Sub channel "response:{session_id}"
     |    and waits for the answer
     v
  Redis (Streams + Pub/Sub)
     |
     | 5. The orchestrator is continuously reading from the stream
     v
orchestrator (Go, port 8082)
     |
     | 6. Loads conversation history from Redis (key: "session:{session_id}")
     | 7. Sends an HTTP POST to cognitive-core with the message + history
     v
cognitive-core (Python/FastAPI, port 8083)
     |
     | 8. Searches pgvector (Supabase) for relevant document chunks
     | 9. Sends the chunks + question to the LLM (Claude/GPT/Gemini)
     | 10. Returns the AI's answer + source references
     v
orchestrator
     |
     | 11. Saves the new messages to Redis session history
     | 12. Publishes the answer to Redis Pub/Sub channel "response:{session_id}"
     v
channel-adapter
     |
     | 13. Receives the pub/sub message
     | 14. Sends it back to the user over WebSocket
     v
User's Browser shows the answer
```

Key insight: the channel-adapter never talks to the orchestrator directly. They communicate through Redis. This means you could swap out the channel-adapter for a WhatsApp adapter, and the orchestrator wouldn't even know.

---

## 2. Go Crash Course

If you're new to Go, here are the specific concepts used throughout this project, explained with real examples from our code.

### 2.1 Packages and Imports

Every Go file starts with a `package` declaration. Files in the same directory must share the same package name.

```go
package main    // This file is the entry point (has func main())

package models  // This file defines data structures
package handlers // This file handles WebSocket connections
```

Imports bring in other packages. Standard library packages have short names, third-party packages use full URLs:

```go
import (
    "fmt"           // standard library — string formatting
    "log"           // standard library — logging
    "net/http"      // standard library — HTTP server
    "os"            // standard library — environment variables

    "github.com/redis/go-redis/v9"  // third-party — Redis client

    "channel-adapter/handlers"       // our own package (local)
)
```

### 2.2 Structs and JSON Tags

Go doesn't have classes. Instead, it has **structs** — collections of named fields. JSON tags tell Go how to convert between Go structs and JSON.

```go
type MessageContent struct {
    Type string `json:"type"`    // Go field "Type" <-> JSON field "type"
    Text string `json:"text"`    // Go field "Text" <-> JSON field "text"
}
```

The backtick syntax `` `json:"type"` `` is called a **struct tag**. When you convert this struct to JSON (called "marshaling"), Go uses the tag names:

```go
msg := MessageContent{Type: "text", Text: "Hello"}
jsonBytes, _ := json.Marshal(msg)
// Result: {"type":"text","text":"Hello"}
```

The `omitempty` tag means "don't include this field in JSON if it's empty":

```go
type WSResponse struct {
    Type      string `json:"type"`
    Text      string `json:"text,omitempty"`      // omitted from JSON if Text == ""
    SessionID string `json:"session_id,omitempty"` // omitted from JSON if SessionID == ""
}
```

So `WSResponse{Type: "typing"}` becomes `{"type":"typing"}` — no empty `text` or `session_id` fields.

### 2.3 Pointers and the `&` / `*` Operators

A pointer holds the **memory address** of a value, not the value itself. This is important because Go passes copies of values to functions by default.

```go
// Without pointer — the function gets a COPY
func changeValue(x int) {
    x = 42  // only changes the copy, original is unchanged
}

// With pointer — the function gets the ADDRESS, so it can modify the original
func changeValue(x *int) {
    *x = 42  // changes the original value
}
```

In our code, you see `&` (address-of) and `*` (pointer type) frequently:

```go
// & creates a pointer to a value
rdb := redis.NewClient(opts)     // rdb is of type *redis.Client (a pointer)
server := &http.Server{...}     // & means "give me a pointer to this Server"

// * in a type declaration means "this is a pointer to..."
type Manager struct {
    rdb *redis.Client   // rdb is a pointer to a redis.Client
}
```

**Why pointers?** Two reasons:

1. **Efficiency:** Passing a pointer (8 bytes) is cheaper than copying a large struct
2. **Shared access:** Multiple parts of your code can access and modify the same object

### 2.4 Methods (Receiver Functions)

Go doesn't have classes, but you can attach functions to structs using a **receiver**:

```go
type Manager struct {
    rdb *redis.Client
}

// (m *Manager) is the "receiver" — this makes LoadHistory a method on Manager
func (m *Manager) LoadHistory(ctx context.Context, sessionID string) ([]ConversationMessage, error) {
    // m.rdb accesses the Manager's redis client
    data, err := m.rdb.Get(ctx, key).Bytes()
    ...
}
```

You call it like: `manager.LoadHistory(ctx, "session-123")` — just like calling a method on an object in other languages.

The `*` in `(m *Manager)` means the method receives a pointer to the Manager, so it can access the actual Manager object rather than a copy.

### 2.5 Error Handling

Go doesn't have try/catch. Instead, functions return errors as their last return value:

```go
// This function returns TWO values: the result and an error
data, err := m.rdb.Get(ctx, key).Bytes()

if err == redis.Nil {
    // redis.Nil is a special error meaning "key not found"
    // This is NOT a real error — it just means no data exists yet
    return []ConversationMessage{}, nil  // nil means "no error"
}
if err != nil {
    // An actual error occurred
    return nil, fmt.Errorf("failed to load session: %w", err)
    // %w wraps the original error so callers can inspect it
}
// If we get here, data is valid — no error occurred
```

The pattern `if err != nil { return ..., err }` appears everywhere in Go. It's verbose but explicit — you always know exactly where errors are handled.

`fmt.Errorf("message: %w", err)` creates a new error message while preserving the original error inside it (wrapping). This is like adding context: "failed to load session" wraps the underlying Redis error.

### 2.6 Goroutines (`go` keyword)

A goroutine is a lightweight thread. You start one by putting `go` before a function call:

```go
// This runs ConsumeLoop in the background — main() doesn't wait for it
go r.ConsumeLoop(ctx)

// This runs an anonymous function in the background
go func() {
    ch := pubsub.Channel()
    for msg := range ch {
        // process messages...
    }
}()   // () at the end immediately invokes the anonymous function
```

Goroutines are incredibly cheap (a few KB of memory each). Our project uses them to:

- Run the Redis Streams consumer loop while also serving HTTP (orchestrator)
- Listen for Redis pub/sub responses while also reading WebSocket messages (channel-adapter)
- Listen for shutdown signals (orchestrator)

### 2.7 Channels and `select`

**Channels** are Go's way of communicating between goroutines. Think of them as typed pipes.

```go
// make(chan os.Signal, 1) creates a channel that can hold 1 Signal value
sigCh := make(chan os.Signal, 1)

// signal.Notify sends OS signals (Ctrl+C, kill) INTO this channel
signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

// <-sigCh BLOCKS (waits) until a value arrives on the channel
<-sigCh  // program pauses here until Ctrl+C is pressed
```

The **`select`** statement lets you wait on multiple channels at once — whichever one receives a value first "wins":

```go
select {
case <-ctx.Done():
    // The context was cancelled (shutdown signal)
    return
case msg, ok := <-ch:
    // A message arrived from Redis pub/sub
    if !ok {
        return  // channel was closed
    }
    // process msg...
}
```

`select` is like a `switch` statement, but for channels. It blocks until one of the cases is ready.

### 2.8 Context (`context.Context`)

Context is Go's way of managing cancellation and timeouts across goroutines. Almost every function that does I/O takes a `ctx` as its first parameter.

```go
// Create a cancellable context
ctx, cancel := context.WithCancel(context.Background())
defer cancel()  // ensure cancel is called when the function exits
```

When you call `cancel()`:

- `ctx.Done()` returns a closed channel (so `<-ctx.Done()` unblocks)
- Every function using this ctx knows to stop what it's doing
- This cascades — child contexts are also cancelled

This is how the orchestrator shuts down cleanly: calling `cancel()` signals the consumer loop, the HTTP server, and everything else to stop.

### 2.9 `defer`

`defer` schedules a function call to run when the surrounding function returns. It's used for cleanup:

```go
func (h *WSHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    conn, err := upgrader.Upgrade(w, r, nil)
    defer conn.Close()  // this runs when ServeHTTP returns, no matter what

    pubsub := h.rdb.Subscribe(ctx, responseCh)
    defer pubsub.Close()  // this also runs on return

    // ... rest of the function
    // When this function returns (for any reason), both Close() calls happen
    // They run in LIFO order: pubsub.Close() first, then conn.Close()
}
```

### 2.10 Interfaces

The `http.Handler` interface is why our `WSHandler` struct can be used as an HTTP route handler:

```go
// Go's net/http package defines this interface:
type Handler interface {
    ServeHTTP(ResponseWriter, *Request)
}

// Our WSHandler implements ServeHTTP, so it automatically satisfies the interface
func (h *WSHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) { ... }

// This is why we can do:
mux.Handle("/ws", wsHandler)  // wsHandler is treated as an http.Handler
```

In Go, you don't explicitly say "implements Handler". If your struct has the right methods, it satisfies the interface automatically. This is called **structural typing** (or "duck typing").

Compare with `HandleFunc` which takes a plain function instead:

```go
mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
    // This is just a function, not a struct with a method
})
```

### 2.11 Maps

Maps are Go's hash tables / dictionaries:

```go
// Create an empty map: string keys, bool values
origins := make(map[string]bool)

// Add entries
origins["http://localhost:3000"] = true

// Look up a value — returns the value and whether the key exists
allowed := origins["http://localhost:3000"]  // true
allowed := origins["http://evil.com"]        // false (zero value for bool)

// map[string]interface{} means "string keys, any type for values"
// This is Go's equivalent of Python's Dict[str, Any]
Values: map[string]interface{}{
    "envelope": string(envelopeJSON),
}
```

### 2.12 Slices

Slices are Go's dynamic arrays:

```go
// Declare a nil slice
var allowedOrigins []string

// Create from splitting a string
allowedOrigins = strings.Split("http://a.com,http://b.com", ",")
// Result: ["http://a.com", "http://b.com"]

// Append adds elements
history = append(history,
    ConversationMessage{Role: "user", Content: userMsg},
    ConversationMessage{Role: "assistant", Content: assistantMsg},
)

// Slice expression to get the last N elements
if len(history) > maxMessages {
    history = history[len(history)-maxMessages:]
    // If history has 15 items and maxMessages is 10:
    // history[15-10:] = history[5:] = items 5 through 14 (last 10)
}
```

---

## 3. Redis Crash Course

Redis is an in-memory data store. Our project uses it for three distinct purposes:

### 3.1 Redis as a Key-Value Store (Session Storage)

The simplest use. Store and retrieve data by a key, with an expiration time.

```
SET "session:abc-123" '[{"role":"user","content":"hi"},{"role":"assistant","content":"hello"}]'
    ^key                  ^value (JSON string)

GET "session:abc-123"  →  returns the JSON string
```

In Go (`session/manager.go`):

```go
// Save — SET with a 24-hour TTL (time to live)
m.rdb.Set(ctx, "session:abc-123", jsonData, 24*time.Hour)

// Load — GET
data, err := m.rdb.Get(ctx, "session:abc-123").Bytes()

// If the key doesn't exist, err == redis.Nil
// After 24 hours, Redis automatically deletes the key
```

**Why Redis for sessions instead of a database?**
Sessions are temporary (24hr), frequently read/written, and don't need to survive a server restart. Redis handles this much faster than PostgreSQL because it keeps everything in RAM.

### 3.2 Redis Streams (Message Queue)

Redis Streams is a log-based message queue. Think of it as an append-only list where multiple consumers can read messages independently.

```
Stream: "msg:inbound"
┌─────────┬───────────────────────────────────────────┐
│ ID      │ Data                                       │
├─────────┼───────────────────────────────────────────┤
│ 1-0     │ envelope: '{"session_id":"abc","text":..}' │
│ 2-0     │ envelope: '{"session_id":"def","text":..}' │
│ 3-0     │ envelope: '{"session_id":"abc","text":..}' │
└─────────┴───────────────────────────────────────────┘
```

**Producer** (channel-adapter publishes messages):

```go
h.rdb.XAdd(ctx, &redis.XAddArgs{
    Stream: "msg:inbound",
    Values: map[string]interface{}{
        "envelope": string(envelopeJSON),  // the field name and value
    },
})
```

**Consumer Group** (orchestrator reads messages):

```go
// Create a consumer group — this tells Redis "we want to track
// which messages this group has already processed"
r.rdb.XGroupCreateMkStream(ctx, "msg:inbound", "orchestrator-group", "0")

// Read the next unprocessed message
streams, err := r.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
    Group:    "orchestrator-group",
    Consumer: "orchestrator-1",
    Streams:  []string{"msg:inbound", ">"},  // ">" means "only new messages"
    Count:    1,                              // read 1 message at a time
    Block:    5 * time.Second,                // wait up to 5s for new messages
})

// After processing, acknowledge the message
r.rdb.XAck(ctx, "msg:inbound", "orchestrator-group", msg.ID)
```

**Why Streams instead of a simple list?**

- **Consumer groups** ensure each message is processed exactly once, even if you run multiple orchestrator instances
- **Acknowledgment** means if a consumer crashes mid-processing, the message isn't lost — another consumer can retry it
- **Persistence** — messages survive Redis restarts (unlike pub/sub)

### 3.3 Redis Pub/Sub (Real-time Notifications)

Pub/Sub is fire-and-forget messaging. A publisher sends a message to a channel, and all current subscribers receive it instantly. If nobody is listening, the message is lost.

```
Publisher (orchestrator):
    PUBLISH "response:abc-123" '{"type":"message","text":"Seto Chiura is..."}'

Subscriber (channel-adapter):
    SUBSCRIBE "response:abc-123"
    → receives: '{"type":"message","text":"Seto Chiura is..."}'
```

In Go:

```go
// Subscribe (channel-adapter)
pubsub := h.rdb.Subscribe(ctx, "response:abc-123")
ch := pubsub.Channel()  // returns a Go channel that receives messages
for msg := range ch {
    // msg.Payload contains the JSON string
}

// Publish (orchestrator)
r.rdb.Publish(ctx, "response:abc-123", jsonString)
```

**Why Pub/Sub for responses instead of Streams?**
Responses need to reach a specific WebSocket connection immediately. The channel-adapter subscribes to `response:{session_id}` before the user sends a message, so when the orchestrator publishes the response, it arrives instantly. We don't need persistence or acknowledgment here — if the WebSocket is disconnected, there's nobody to receive the answer anyway.

### 3.4 Streams vs Pub/Sub — When to Use Which

| Feature          | Streams                             | Pub/Sub                         |
| ---------------- | ----------------------------------- | ------------------------------- |
| Persistence      | Messages are stored                 | Fire and forget                 |
| Multiple readers | Each message processed once         | All subscribers get it          |
| Use case here    | Inbound messages (must not be lost) | Responses (targeted, real-time) |
| Analogy          | A to-do list                        | A loudspeaker                   |

---

## 4. Channel Adapter

**Purpose:** The public-facing gateway. Accepts WebSocket connections, converts raw messages into the standard MessageEnvelope format, and bridges between WebSocket and Redis.

### 4.1 File: `models/envelope.go`

Defines the data structures for all messages flowing through this service.

```go
// What the browser sends us (simple — just text)
type WSIncoming struct {
    Text string `json:"text"`
}

// What we send back to the browser
type WSResponse struct {
    Type      string `json:"type"`               // "connected", "typing", "message", "error"
    Text      string `json:"text,omitempty"`      // the actual message content
    SessionID string `json:"session_id,omitempty"` // included in "connected" and "message"
}

// The standardized format used between ALL services internally
type MessageEnvelope struct {
    MessageID string          `json:"message_id"`  // unique ID for this message
    SessionID string          `json:"session_id"`  // identifies the conversation
    Channel   string          `json:"channel"`     // "web" (future: "whatsapp", "viber")
    UserID    string          `json:"user_id"`     // "anonymous" in Phase 1
    Timestamp time.Time       `json:"timestamp"`   // when the message was sent
    Content   MessageContent  `json:"content"`     // the actual text
    Metadata  MessageMetadata `json:"metadata"`    // language, platform-specific data
}
```

### 4.2 File: `adapters/web.go`

This is the **normalization layer**. It takes a raw text message from the web widget and wraps it in a MessageEnvelope.

```go
func NormalizeWebMessage(sessionID, text string) models.MessageEnvelope {
    return models.MessageEnvelope{
        MessageID: uuid.New().String(),        // generate a unique ID
        SessionID: sessionID,                  // which conversation this belongs to
        Channel:   "web",                      // hardcoded — this is the WEB adapter
        UserID:    "anonymous",                // no auth in Phase 1
        Timestamp: time.Now().UTC(),           // current time
        Content: models.MessageContent{
            Type: "text",
            Text: text,                        // the user's actual message
        },
        Metadata: models.MessageMetadata{
            Language:     "en",
            PlatformData: map[string]interface{}{},
        },
    }
}
```

**Why does this exist as a separate file?**
When you add WhatsApp support in Phase 2, you'll create `adapters/whatsapp.go` with a `NormalizeWhatsAppMessage()` function. It will receive a WhatsApp webhook payload (which has a completely different format) and produce the exact same `MessageEnvelope`. The downstream services won't need any changes.

### 4.3 File: `handlers/websocket.go` — The Heart of the Service

This is where the WebSocket connection lifecycle happens. Let's walk through it section by section.

**Setting up the upgrader:**

```go
var upgrader = websocket.Upgrader{
    CheckOrigin: func(r *http.Request) bool {
        return true  // placeholder — overridden per-connection
    },
}
```

HTTP and WebSocket use the same port, but WebSocket needs a protocol "upgrade". The `Upgrader` handles the HTTP → WebSocket handshake. `CheckOrigin` is a security function that controls which websites can connect.

**The handler struct and constructor:**

```go
type WSHandler struct {
    rdb            *redis.Client        // Redis connection
    allowedOrigins map[string]bool      // set of allowed origins for CORS
}

func NewWSHandler(rdb *redis.Client, allowedOrigins []string) *WSHandler {
    // Convert slice to map for O(1) lookups
    origins := make(map[string]bool)
    for _, o := range allowedOrigins {
        origins[o] = true
    }
    return &WSHandler{rdb: rdb, allowedOrigins: origins}
}
```

**The ServeHTTP method — what happens when a user connects:**

```go
func (h *WSHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
```

Step 1 — Upgrade HTTP to WebSocket:

```go
    conn, err := upgrader.Upgrade(w, r, nil)
    defer conn.Close()  // clean up when this function returns
```

Step 2 — Determine or generate session ID:

```go
    sessionID := r.URL.Query().Get("session_id")  // check URL: /ws?session_id=abc
    if sessionID == "" {
        sessionID = uuid.New().String()  // first-time visitor — generate new ID
    }
```

Step 3 — Send "connected" confirmation:

```go
    conn.WriteJSON(models.WSResponse{
        Type:      "connected",
        SessionID: sessionID,  // browser stores this in localStorage
    })
```

Step 4 — Subscribe to the response channel (so we can receive answers later):

```go
    responseCh := fmt.Sprintf("response:%s", sessionID)  // e.g., "response:abc-123"
    pubsub := h.rdb.Subscribe(ctx, responseCh)
    defer pubsub.Close()
```

Step 5 — Start a goroutine that forwards Redis pub/sub messages to the WebSocket:

```go
    go func() {
        ch := pubsub.Channel()  // Go channel that receives Redis pub/sub messages
        for {
            select {
            case <-ctx.Done():     // connection closed? stop.
                return
            case msg, ok := <-ch:  // new response from orchestrator?
                if !ok { return }
                var resp models.WSResponse
                json.Unmarshal([]byte(msg.Payload), &resp)
                conn.WriteJSON(resp)  // send to browser
            }
        }
    }()
```

Step 6 — Main loop: read messages from the WebSocket and publish to Redis Streams:

```go
    for {
        _, message, err := conn.ReadMessage()  // blocks until browser sends something
        if err != nil {
            return  // WebSocket closed — exit (defer will clean up)
        }

        var incoming models.WSIncoming
        json.Unmarshal(message, &incoming)   // parse {"text": "..."}

        envelope := adapters.NormalizeWebMessage(sessionID, incoming.Text)
        envelopeJSON, _ := json.Marshal(envelope)

        // Publish to Redis Streams — the orchestrator will pick this up
        h.rdb.XAdd(ctx, &redis.XAddArgs{
            Stream: "msg:inbound",
            Values: map[string]interface{}{
                "envelope": string(envelopeJSON),
            },
        })
    }
```

**The two concurrent loops visualized:**

```
        ┌─────────────────────────────────────┐
        │  Goroutine (runs in background)     │
        │                                     │
        │  Redis Pub/Sub ──→ WebSocket out    │
        │  (receives answers, sends to user)  │
        └─────────────────────────────────────┘

        ┌─────────────────────────────────────┐
        │  Main loop                          │
        │                                     │
        │  WebSocket in ──→ Redis Streams     │
        │  (receives questions, sends to      │
        │   orchestrator via stream)          │
        └─────────────────────────────────────┘
```

### 4.4 File: `main.go`

The entry point. Sets up everything and starts the HTTP server.

```go
func main() {
    // 1. Read config from environment variables (with defaults)
    redisURL := os.Getenv("REDIS_URL")
    port := os.Getenv("PORT")
    allowedOriginsStr := os.Getenv("ALLOWED_ORIGINS")

    // 2. Connect to Redis
    opts, _ := redis.ParseURL(redisURL)
    rdb := redis.NewClient(opts)

    // 3. Create WebSocket handler
    wsHandler := handlers.NewWSHandler(rdb, allowedOrigins)

    // 4. Set up HTTP routes
    mux := http.NewServeMux()    // a "multiplexer" — maps URL paths to handlers
    mux.Handle("/ws", wsHandler)  // WebSocket endpoint
    mux.HandleFunc("/health", ...) // health check

    // 5. Start listening
    http.ListenAndServe(":8081", mux)  // blocks forever
}
```

**`http.NewServeMux()`** is Go's built-in HTTP router. It's like Express.js's `app` or Flask's `app` — it maps URL paths to handler functions.

---

## 5. Orchestrator

**Purpose:** The "brain" of the routing layer. Consumes messages from Redis Streams, manages conversation sessions, calls the cognitive-core AI service, and sends responses back.

### 5.1 File: `models/envelope.go`

Same `MessageEnvelope` as channel-adapter, plus two extra structs for talking to cognitive-core:

```go
// What we send TO cognitive-core
type ChatRequest struct {
    SessionID           string                `json:"session_id"`
    Message             string                `json:"message"`
    ConversationHistory []ConversationMessage `json:"conversation_history"`
    Channel             string                `json:"channel"`
    Language            string                `json:"language"`
}

// What we receive FROM cognitive-core
type ChatResponse struct {
    SessionID string   `json:"session_id"`
    Response  string   `json:"response"`
    Sources   []string `json:"sources"`
    ModelUsed string   `json:"model_used"`
}
```

### 5.2 File: `session/manager.go` — Conversation Memory

This manages the conversation history stored in Redis. Each session has a key like `session:abc-123` containing a JSON array of messages.

```go
const (
    sessionTTL    = 24 * time.Hour  // sessions expire after 24 hours of inactivity
    maxMessages   = 10              // keep only the last 10 messages
    sessionPrefix = "session:"      // Redis key prefix
)
```

**LoadHistory** — fetch conversation history from Redis:

```go
func (m *Manager) LoadHistory(ctx context.Context, sessionID string) ([]ConversationMessage, error) {
    key := fmt.Sprintf("%s%s", sessionPrefix, sessionID)  // "session:abc-123"
    data, err := m.rdb.Get(ctx, key).Bytes()

    if err == redis.Nil {
        // Key doesn't exist — this is a new session, return empty history
        return []ConversationMessage{}, nil
    }
    if err != nil {
        return nil, fmt.Errorf("failed to load session: %w", err)
    }

    var history []ConversationMessage
    json.Unmarshal(data, &history)  // parse JSON into Go structs
    return history, nil
}
```

**SaveHistory** — write conversation history back to Redis:

```go
func (m *Manager) SaveHistory(ctx context.Context, sessionID string, history []ConversationMessage) error {
    // Trim to last 10 messages — prevents unlimited memory growth
    if len(history) > maxMessages {
        history = history[len(history)-maxMessages:]
    }

    data, _ := json.Marshal(history)  // convert Go structs to JSON
    key := fmt.Sprintf("%s%s", sessionPrefix, sessionID)

    // SET with TTL — Redis will auto-delete this key after 24 hours
    m.rdb.Set(ctx, key, data, sessionTTL)
    return nil
}
```

**AppendMessages** — convenience method to add a user+assistant message pair:

```go
func (m *Manager) AppendMessages(ctx context.Context, sessionID string, userMsg, assistantMsg string) error {
    history, _ := m.LoadHistory(ctx, sessionID)  // load existing

    history = append(history,                     // add new messages
        ConversationMessage{Role: "user", Content: userMsg},
        ConversationMessage{Role: "assistant", Content: assistantMsg},
    )

    return m.SaveHistory(ctx, sessionID, history)  // save back (trims to 10)
}
```

### 5.3 File: `router/router.go` — The Consumer Loop

This is the core processing engine.

**Constants define the "plumbing":**

```go
const (
    streamKey      = "msg:inbound"       // Redis Stream to read from
    consumerGroup  = "orchestrator-group" // consumer group name
    consumerName   = "orchestrator-1"     // this specific consumer's name
    responsePrefix = "response:"          // pub/sub channel prefix for responses
    httpTimeout    = 60 * time.Second     // max wait for cognitive-core
)
```

**EnsureConsumerGroup** — creates the consumer group if it doesn't exist:

```go
func (r *Router) EnsureConsumerGroup(ctx context.Context) error {
    err := r.rdb.XGroupCreateMkStream(ctx, streamKey, consumerGroup, "0").Err()
    // XGroupCreateMkStream does two things:
    //   1. Creates the stream "msg:inbound" if it doesn't exist
    //   2. Creates the consumer group "orchestrator-group" starting from message "0"
    //      (meaning it will process all existing messages)

    // If the group already exists, Redis returns an error — we ignore it
    if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
        return err
    }
    return nil
}
```

**ConsumeLoop** — runs forever, reading messages from the stream:

```go
func (r *Router) ConsumeLoop(ctx context.Context) {
    for {
        // Check if we should stop (context cancelled = shutdown signal)
        select {
        case <-ctx.Done():
            return
        default:
            // continue processing
        }

        // Read next unprocessed message from the stream
        streams, err := r.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
            Group:    consumerGroup,
            Consumer: consumerName,
            Streams:  []string{streamKey, ">"},  // ">" = only new, undelivered messages
            Count:    1,                          // process one at a time
            Block:    5 * time.Second,            // wait up to 5s if no messages
        }).Result()

        // No new messages? Loop back and try again.
        if err == redis.Nil {
            continue
        }

        // Process each message
        for _, stream := range streams {
            for _, msg := range stream.Messages {
                r.handleMessage(ctx, msg)
            }
        }
    }
}
```

**handleMessage** — processes a single message end-to-end:

```go
func (r *Router) handleMessage(ctx context.Context, msg redis.XMessage) {
    // 1. Extract and parse the envelope from the stream message
    envelopeJSON := msg.Values["envelope"].(string)
    var envelope models.MessageEnvelope
    json.Unmarshal([]byte(envelopeJSON), &envelope)

    // 2. Send "typing" indicator to the user immediately
    r.publishResponse(ctx, sessionID, models.WSResponse{Type: "typing"})

    // 3. Load conversation history from Redis
    history, _ := r.sessionMgr.LoadHistory(ctx, sessionID)

    // 4. Build the request for cognitive-core
    chatReq := models.ChatRequest{
        SessionID:           sessionID,
        Message:             envelope.Content.Text,   // "What is Seto Chiura?"
        ConversationHistory: history,                  // last 10 messages
        Channel:             envelope.Channel,         // "web"
        Language:            envelope.Metadata.Language, // "en"
    }

    // 5. Call cognitive-core via HTTP POST
    chatResp, err := r.callCognitiveCore(ctx, chatReq)
    if err != nil {
        // Send error message to user
        r.publishResponse(ctx, sessionID, models.WSResponse{
            Type: "error",
            Text: "Sorry, I'm having trouble responding right now. Please try again.",
        })
        r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)  // acknowledge anyway
        return
    }

    // 6. Save the conversation (user question + AI answer) to session history
    r.sessionMgr.AppendMessages(ctx, sessionID, envelope.Content.Text, chatResp.Response)

    // 7. Send the AI's response back to the user
    r.publishResponse(ctx, sessionID, models.WSResponse{
        Type:      "message",
        Text:      chatResp.Response,
        SessionID: sessionID,
    })

    // 8. Acknowledge the stream message (tells Redis "we're done with this one")
    r.rdb.XAck(ctx, streamKey, consumerGroup, msg.ID)
}
```

**callCognitiveCore** — makes an HTTP POST to the Python service:

```go
func (r *Router) callCognitiveCore(ctx context.Context, req models.ChatRequest) (*models.ChatResponse, error) {
    body, _ := json.Marshal(req)  // convert Go struct → JSON bytes

    // Create an HTTP request with context (so it can be cancelled/timed out)
    httpReq, _ := http.NewRequestWithContext(ctx, http.MethodPost,
        r.cognitiveURL+"/chat",     // "http://cognitive-core:8083/chat"
        bytes.NewReader(body))
    httpReq.Header.Set("Content-Type", "application/json")

    resp, err := r.httpClient.Do(httpReq)  // send the request (60s timeout)
    defer resp.Body.Close()

    respBody, _ := io.ReadAll(resp.Body)  // read the full response body

    var chatResp models.ChatResponse
    json.Unmarshal(respBody, &chatResp)   // parse JSON → Go struct
    return &chatResp, nil
}
```

### 5.4 File: `main.go` — Startup and Graceful Shutdown

```go
func main() {
    // 1. Read config
    // 2. Connect to Redis (with ping to verify)
    // 3. Create session manager and router

    // 4. Start consumer loop as a goroutine (background)
    go r.ConsumeLoop(ctx)

    // 5. Set up HTTP server (just /health endpoint)
    server := &http.Server{Addr: ":8082", Handler: mux}

    // 6. Graceful shutdown listener (another goroutine)
    go func() {
        sigCh := make(chan os.Signal, 1)
        signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
        <-sigCh             // blocks until Ctrl+C or kill signal
        cancel()            // cancels the context → stops ConsumeLoop
        server.Close()      // stops the HTTP server
    }()

    // 7. Start HTTP server (blocks until server.Close() is called)
    server.ListenAndServe()
}
```

The orchestrator runs **three concurrent activities**:

1. The consumer loop (goroutine) — reads from Redis Streams
2. The HTTP server (main thread) — serves `/health`
3. The signal listener (goroutine) — waits for shutdown signals

---

## 6. Cognitive Core (Python)

**Purpose:** The AI brain. Receives a question + conversation history, searches the knowledge base for relevant documents, sends everything to an LLM, and returns a grounded answer.

### 6.1 File: `schemas/message.py` — Pydantic Models

Pydantic validates incoming JSON automatically. If someone sends invalid data, FastAPI returns a 422 error with details — you don't write any validation code yourself.

```python
class ChatRequest(BaseModel):
    session_id: str
    message: str
    conversation_history: list[ConversationMessage] = []  # default: empty list
    channel: str = "web"                                   # default: "web"
    language: str = "en"                                   # default: "en"
```

When FastAPI receives a POST to `/chat`, it automatically:

1. Parses the JSON body
2. Validates every field matches the types above
3. Creates a `ChatRequest` object you can use in your code

### 6.2 File: `llm/client.py` — The LLM Factory

The **factory pattern** — one function that creates different objects based on configuration:

```python
def get_llm() -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "anthropic")  # read from env var

    if provider == "anthropic":
        return ChatAnthropic(model="claude-sonnet-4-20250514", ...)
    elif provider == "openai":
        return ChatOpenAI(model="gpt-4o-mini", ...)
    elif provider == "gemini":
        return ChatGoogleGenerativeAI(model="gemini-2.0-flash", ...)
```

**Why this matters:** This is the ONLY place in the entire codebase that knows about specific LLM providers. To switch from Claude to GPT, you change one environment variable. No code changes needed.

The `from langchain_anthropic import ChatAnthropic` inside the `if` block is a **lazy import** — it only loads the Anthropic library if you're actually using Anthropic. This means you don't need all three LLM libraries installed, just the one you're using.

### 6.3 File: `rag/retriever.py` — Vector Search

This creates a connection to the pgvector database for semantic search.

```python
def get_retriever(k: int = 4):
    # OpenAI turns text into "embeddings" — arrays of 1536 numbers
    # that represent the meaning of the text
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    # Connect to PostgreSQL where our document chunks are stored
    vectorstore = PGVector(
        connection_string=os.getenv("DATABASE_URL"),
        embedding_function=embeddings,
        collection_name="mandala_public_kb",  # which set of documents to search
    )

    # Return a retriever that finds the top 4 most similar chunks
    return vectorstore.as_retriever(search_kwargs={"k": k})
```

**How vector search works (simplified):**

1. The user asks "What nutrition does Seto Chiura have?"
2. OpenAI converts this question into 1536 numbers (an "embedding")
3. pgvector compares these numbers against the embeddings of every document chunk
4. The 4 chunks whose embeddings are most similar to the question are returned
5. "Similar" is measured by cosine similarity — how much two vectors point in the same direction

### 6.4 File: `rag/pipeline.py` — The RAG Chain

**RAG** = Retrieval-Augmented Generation. Instead of asking the LLM a question directly (and risking hallucination), we first retrieve relevant documents, then ask the LLM to answer based on those documents.

```python
SYSTEM_PROMPT = """You are Maya, a helpful nutrition assistant for Mandala Foods Nepal.
Answer questions only about Mandala Foods products, their nutritional content,
ingredients, and benefits. If a question is outside this scope, politely redirect
the user..."""
```

**Building the chain:**

```python
def build_chain(conversation_history=None):
    llm = get_llm()          # the AI model (Claude, GPT, etc.)
    retriever = get_retriever(k=4)  # the vector search

    # Memory stores the last 10 conversation turns
    memory = ConversationBufferWindowMemory(
        k=10,                        # remember last 10 exchanges
        memory_key="chat_history",   # LangChain uses this key internally
        return_messages=True,        # return as Message objects, not strings
        output_key="answer",         # which chain output to store in memory
    )

    # Pre-fill memory with the conversation history from the request
    if conversation_history:
        for msg in conversation_history:
            if msg["role"] == "user":
                memory.chat_memory.add_message(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                memory.chat_memory.add_message(AIMessage(content=msg["content"]))

    # Build the chain — this wires everything together
    chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        memory=memory,
        return_source_documents=True,  # include the chunks that were used
        combine_docs_chain_kwargs={"prompt": _build_prompt()},
    )
    return chain
```

**What happens when the chain runs:**

```
User asks: "What nutrition does Seto Chiura have?"
                    │
                    ▼
    ┌───────────────────────────┐
    │  1. Condense question     │  If there's chat history, rephrase the
    │     (if needed)           │  question to be standalone.
    │                           │  e.g., "What about protein?" → "What
    │                           │  protein does Seto Chiura have?"
    └────────────┬──────────────┘
                 ▼
    ┌───────────────────────────┐
    │  2. Retrieve documents    │  Search pgvector for top 4 relevant
    │     from pgvector         │  document chunks
    └────────────┬──────────────┘
                 ▼
    ┌───────────────────────────┐
    │  3. Build prompt          │  Combine: system prompt + retrieved
    │                           │  chunks + question
    └────────────┬──────────────┘
                 ▼
    ┌───────────────────────────┐
    │  4. Call LLM              │  Send the full prompt to Claude/GPT
    │                           │  Get the answer
    └────────────┬──────────────┘
                 ▼
    Return: answer + source documents
```

**The prompt template:**

```python
template = f"""{SYSTEM_PROMPT}

Context from knowledge base:
{context}          ← the 4 retrieved document chunks are pasted here

Question: {question}   ← the user's question goes here

Answer:"""
```

### 6.5 File: `rag/ingestion.py` — Loading Documents

This is how PDFs get into the vector database so they can be searched.

```python
def ingest_file(file_path: str) -> int:
    # 1. LOAD the document
    if file_path.endswith(".pdf"):
        loader = PyPDFLoader(file_path)      # extracts text from PDF pages
    else:
        loader = TextLoader(file_path)       # reads plain text

    documents = loader.load()  # each page becomes a Document object

    # 2. SPLIT into chunks (512 characters each, 64 char overlap)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=512,      # each chunk is ~512 characters
        chunk_overlap=64,    # chunks overlap by 64 chars to preserve context
    )
    chunks = splitter.split_documents(documents)

    # 3. EMBED and STORE — OpenAI turns each chunk into a vector,
    #    then pgvector stores both the text and its vector
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    PGVector.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name="mandala_public_kb",
        connection_string=os.getenv("DATABASE_URL"),
        pre_delete_collection=False,  # don't delete existing data
    )
```

**Why chunk_size=512 and chunk_overlap=64?**

- **512 characters** is roughly a paragraph. Small enough to be specific, large enough to have context.
- **64 character overlap** means the end of one chunk overlaps with the start of the next. This prevents a sentence that spans two chunks from being lost.

```
Document: "Seto Chiura is a flattened rice product. It contains 350 calories per
           100g serving. The product is rich in carbohydrates and provides energy..."

Chunk 1: "Seto Chiura is a flattened rice product. It contains 350 calories per
           100g serving. The product is"
                                    ↑ overlap starts here
Chunk 2: "100g serving. The product is rich in carbohydrates and provides energy..."
```

### 6.6 File: `api/routes.py` — HTTP Endpoints

**`POST /chat`** — the main endpoint the orchestrator calls:

```python
@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # Convert Pydantic models to plain dicts for the pipeline
    history = [msg.model_dump() for msg in request.conversation_history]

    # Run the RAG pipeline (retrieve docs → call LLM → get answer)
    result = await run_pipeline(
        message=request.message,
        conversation_history=history,
    )

    return ChatResponse(
        session_id=request.session_id,
        response=result["response"],     # the AI's answer
        sources=result["sources"],       # which documents were used
        model_used=str(model_name),      # "claude-sonnet-4-20250514" etc.
    )
```

**`POST /admin/ingest`** — upload a PDF to add to the knowledge base:

```python
@router.post("/admin/ingest")
async def admin_ingest(
    background_tasks: BackgroundTasks,     # FastAPI's background task runner
    file: UploadFile = File(...),          # the uploaded file
    authorization: str = Header(...),      # the Authorization header
):
    # Check bearer token
    if authorization != f"Bearer {os.getenv('ADMIN_TOKEN')}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Save to temp file, then ingest in the background
    # (so the HTTP response returns immediately)
    background_tasks.add_task(_ingest, tmp.name)

    return {"status": "ingestion_started", "filename": file.filename}
```

`BackgroundTasks` is a FastAPI feature. The HTTP response is sent immediately with `"ingestion_started"`, and the actual PDF processing happens in the background. This prevents the request from timing out on large files.

### 6.7 File: `main.py` — Entry Point

```python
app = FastAPI(title="Maya Genie - Cognitive Core")
app.include_router(router)  # attach all routes from api/routes.py
```

`include_router` is how FastAPI organizes routes. The `router` object in `routes.py` defines all the endpoints, and `main.py` attaches them to the app. This separation keeps the code modular.

---

## 7. How the MessageEnvelope Connects Everything

The `MessageEnvelope` is defined three times — once in each service — and they must stay in sync:

| Service         | File                   | Language | Format   |
| --------------- | ---------------------- | -------- | -------- |
| channel-adapter | `models/envelope.go` | Go       | Struct   |
| orchestrator    | `models/envelope.go` | Go       | Struct   |
| cognitive-core  | `schemas/message.py` | Python   | Pydantic |

The JSON representation is the **contract** between services. As long as the JSON shape matches, the services can be in any language.

```json
{
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "session_id": "660f9500-f30c-52e5-b827-557766551111",
  "channel": "web",
  "user_id": "anonymous",
  "timestamp": "2026-02-19T10:00:00Z",
  "content": {
    "type": "text",
    "text": "What is Seto Chiura?"
  },
  "metadata": {
    "language": "en",
    "platform_data": {}
  }
}
```

**Why is this "sacred"?** Because when you add WhatsApp support:

- You add `adapters/whatsapp.go` to channel-adapter
- It receives WhatsApp's webhook format and converts it to this same envelope
- The orchestrator and cognitive-core don't change at all
- The `channel` field changes from `"web"` to `"whatsapp"`, but nothing reads that field for routing decisions in Phase 1

This is the **adapter pattern** — different inputs, same output format, zero downstream changes.
