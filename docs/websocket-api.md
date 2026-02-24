# WebSocket API Contract

## Connection

```
ws://chat.mandalafoods.co/ws?session_id={uuid}
```

The `session_id` query parameter is optional.

- If omitted: the server generates a new UUID and returns it in the first server message
- If provided: the server resumes the existing session and loads conversation history from Redis

The frontend is responsible for persisting the `session_id` in `localStorage["mandala_session_id"]` and passing it on every subsequent connection.

---

## Client → Server Messages

All messages from the frontend to the backend are JSON:

```json
{
  "text": "What are the nutritional benefits of Seto Chiura?"
}
```

| Field | Type   | Required | Description            |
|-------|--------|----------|------------------------|
| text  | string | yes      | The user's message text |

---

## Server → Client Messages

All messages from the backend to the frontend are JSON. The `type` field determines how the frontend handles the message.

### type: `connected`

Sent immediately on successful connection.

```json
{
  "type": "connected",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

The frontend must store this `session_id` in localStorage if it doesn't already have one.

### type: `typing`

Sent when the AI pipeline has received the message and is processing.

```json
{
  "type": "typing"
}
```

The frontend should show a typing indicator.

### type: `message`

The AI response.

```json
{
  "type": "message",
  "text": "Seto Chiura is a flattened rice product rich in carbohydrates...",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

The frontend should hide the typing indicator and render the message.

### type: `error`

Something went wrong in the pipeline.

```json
{
  "type": "error",
  "text": "Sorry, I'm having trouble responding right now. Please try again."
}
```

The frontend should hide the typing indicator and display the error text.

---

## Session Lifecycle

```
Frontend                          Backend
   │                                 │
   │── GET ws://.../ws ─────────────►│
   │                                 │  Generate session_id if not provided
   │                                 │  Create Redis session key
   │◄── { type: "connected",  ───────│
   │      session_id: "abc-123" }    │
   │                                 │
   │── { "text": "Hello" } ─────────►│
   │                                 │  Publish to Redis Streams
   │◄── { type: "typing" } ──────────│  Orchestrator processing...
   │                                 │  Cognitive Core RAG pipeline...
   │◄── { type: "message",  ─────────│
   │      text: "Hi! How can I..." } │
   │                                 │
   │  [browser closes / navigates]   │
   │                                 │  Session persists in Redis (24hr TTL)
   │                                 │
   │── GET ws://.../ws               │
   │    ?session_id=abc-123 ────────►│  Resume existing session
   │◄── { type: "connected",  ───────│  Load conversation history
   │      session_id: "abc-123" }    │
```

---

## Reconnection Behavior

The backend does not implement reconnection — that is the frontend's responsibility. Recommended strategy: exponential backoff.

- Attempt 1: wait 1 second
- Attempt 2: wait 2 seconds
- Attempt 3: wait 4 seconds
- Attempt 4: wait 8 seconds
- Attempt 5: wait 16 seconds
- After 5 failed attempts: show "Connection lost — refresh to reconnect"

The `session_id` is preserved in localStorage across reconnection attempts so conversation history is not lost.

---

## CORS and Allowed Origins

The channel-adapter accepts WebSocket upgrade requests only from origins listed in the `ALLOWED_ORIGINS` environment variable.

```
ALLOWED_ORIGINS=https://mandalafoods.co,https://www.mandalafoods.co
```
