# Autonomous Brain foundation (0.6.0)

Version 0.6.0 adds an opt-in event-driven Brain process without adding OpenAI API billing.

## Architecture

- ChatGPT/Codex continues to access the connector through OpenAI Secure MCP Tunnel.
- Autonomous event evaluation uses the already configured Home Assistant `ai_task` provider.
- The default provider is `ai_task.codex_assist_ai_task`, supplied by Codex Assist and authenticated with ChatGPT/Codex subscription access.
- The Brain never treats Secure MCP Tunnel as a reverse event channel. The tunnel remains request/response from supported OpenAI products to the local MCP server.

## Token controls

The Brain does not send every Home Assistant event to AI.

1. It subscribes only to `state_changed`.
2. It ignores domains not in the local allowlist.
3. It ignores attribute-only updates and startup entity creation churn.
4. It keeps only the newest event per entity inside each batch.
5. Small non-priority batches are skipped locally.
6. AI calls have hourly and daily hard caps.
7. Relevant compact events are stored locally in `/data/brain_events.jsonl` for later behavior learning without spending model tokens.

Default limits:

- batch: 45 seconds
- minimum non-priority batch: 2 entity changes
- maximum AI evaluations: 4/hour and 30/day
- event log rotation: 5 MiB

## Decisions

The AI Task returns one structured decision:

- `ignore`: no action
- `suggest`: store a suggestion for later review
- `announce`: speak a short message only when autonomous speech is explicitly enabled

Autonomous physical device control is intentionally not part of 0.6.0.

## Alexa speech safety boundary

Autonomous speech is disabled by default.

When enabled, the Brain only accepts a configured `notify.*` service. It refuses any non-notify service, so the autonomous execution path cannot be repointed to `light.*`, `switch.*`, locks, covers, or generic Home Assistant service calls.

Default service:

`notify.alexa_media_alexa`

Alexa Media Player receives the message with `data.type=tts`.

## MCP inspection tools

- `brain_status()`
- `brain_recent_events(limit=20)`
- `brain_suggestions(limit=10)`

These let ChatGPT inspect what the Brain observed and what it suggested through the existing Secure MCP Tunnel.

## Initial test configuration

Keep these settings first:

```yaml
brain_enabled: true
brain_announce_enabled: false
brain_notify_suggestions: false
brain_max_ai_calls_per_hour: 4
brain_max_ai_calls_per_day: 30
```

This runs observation and AI classification but cannot speak autonomously.

After validating `brain_status`, recent events, and suggestions, enable Alexa speech separately.
