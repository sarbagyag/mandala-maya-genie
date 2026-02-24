# Maya WebSocket API Spec

## Endpoint
```
wss://maya.mandalafoods.co/ws
```

Optionally pass a session ID to resume a conversation:
```
wss://maya.mandalafoods.co/ws?session_id=<uuid>
```
If omitted, a new session ID is generated automatically.

---

## Message Flow

### 1. On connect — server sends:
```json
{ "type": "connected", "session_id": "uuid-here" }
```
Store the `session_id` if you want to resume the session later.

### 2. Send a user message:
```json
{ "text": "What products does Mandala Foods offer?" }
```

### 3. Server sends typing indicator first:
```json
{ "type": "typing" }
```
Use this to show a loading/typing animation.

### 4. Server sends the response:
```json
{ "type": "message", "text": "...", "session_id": "uuid-here" }
```
The `text` field is **markdown formatted**.

### 5. On error:
```json
{ "type": "error", "text": "Sorry, I'm having trouble responding right now. Please try again." }
```

---

## Notes
- The connection stays open for the entire chat session
- Session history is stored server-side (last 10 messages, 24hr TTL)
- Reconnecting with the same `session_id` restores context
- Responses are in markdown — render accordingly
