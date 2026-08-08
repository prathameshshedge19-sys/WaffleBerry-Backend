# Phase 10.18 Live Call operations

## Scope and classification

This phase observes the existing Live Call architecture; it does not change WebRTC, SDP,
VAD, audio, interruption, reconnect, rendering, voice selection, memory retrieval, prompts,
or session TTL.

| Finding | Classification | Result |
| --- | --- | --- |
| Engine/bootstrap, memory route/parity, server/client latency logs | SAFE_ALREADY | Preserved |
| `debugLiveCall=1` panel and console diagnostics | SAFE_ALREADY | Developer opt-in only; no conversation content |
| Authenticated aggregate call lifecycle record | LOW_RISK_OBSERVABILITY | Added |
| CPU, RAM, disk, network, uptime dashboards | FUTURE_INFRASTRUCTURE | Not added |
| Media/reconnect/retrieval changes | HIGH_RISK | Not changed |

## Outcome and health taxonomy

Each accepted operational record is `call_started` or `call_ended`. Outcomes are
`started`, `startup_failed`, `completed_normally`, `transport_failed`, `provider_failed`,
`session_expired`, and `user_ended`. Startup/failure categories are a closed allowlist:
session creation, bootstrap, microphone, peer connection, SDP exchange, data channel,
remote audio, external renderer, transport, session expiry, provider rate limit, provider
quota exhaustion, provider transient/unknown failure, and unknown.

The end record contains duration and aggregate counters for turns started/completed/failed/
recovered, recoveries, response failures, external TTS failures, and memory routes supported/
unsupported/error/timeout. Unsupported retrieval is not a failure. These log fields permit
later calculation of startup success, call/turn/provider/memory failure, and recovery success
rates without application-side analytics.

`provider_quota_exhausted` is used only when safe provider metadata contains quota, billing,
or insufficient-quota evidence. An otherwise proven HTTP 429 is
`provider_rate_limited`. Provider bodies are never logged. Users continue to receive generic
safe call-start copy. Bootstrap creates one provider request; the existing optional fallback
creates at most one cascade session and does not retry provider bootstrap.

## Flags and emergency rollback

- `LIVE_CALL_REALTIME_ENABLED=false`: all voices use the existing cascade path. Marin/Cedar
  are not silently replaced; Simran/Shubh retain their selected identity on cascade.
- `LIVE_CALL_EXTERNAL_VOICE_REALTIME_ENABLED=false`: Marin/Cedar may use native Realtime when
  global Realtime is enabled; Simran/Shubh use cascade with their selected voice.
- `LIVE_CALL_REALTIME_STRICT=true`: a failed requested Realtime startup fails safely instead
  of creating a cascade fallback session. It does not enable Realtime by itself.

Emergency procedure: change the relevant environment flag, restart the existing backend
service using the host's normal deployment procedure, and verify `/health`. Disable global
Realtime for a native incident; disable only external-voice Realtime for a Sarvam renderer
incident. Never substitute another voice. Repository code cannot confirm the host's systemd
unit name or restart policy; verify those on the Hetzner host before an incident.

## Health, logging, privacy, and retention

`GET /health` is a free liveness check returning `{"status":"ok"}`. It performs no provider
call and exposes no secrets. The richer API health route already exists; no separate readiness
infrastructure is warranted for the current single-server deployment.

`LIVE_CALL_OPERATIONAL` is emitted at INFO with event, 12-character SHA-256 correlation ID,
engine, renderer, voice identifier, status, failure category, duration, and counters. The
logical UUID is random and non-credential-bearing, but hashing it further avoids exposing the
full internal identifier. Transport tokens, ephemeral credentials, permanent credentials,
SDP, audio, transcript, response text, prompt, names, relationships, memory text, and tool
payloads/results are excluded by schema and logging code.

Production logs go through Python/Uvicorn logging. On a typical systemd deployment this means
journald, but the repository does not configure or prove the production sink. Retention is
therefore the host's journald/external logging policy; inspect `journalctl --disk-usage` and
`journald.conf` on the server rather than changing retention in application code.

Monitor CPU, RAM, disk, network, process uptime/restarts, and log volume externally later.
Aggregate logs are sufficient for V1; do not add Redis, Kafka, Prometheus, or an analytics DB.

## Known deferred baseline

- `MOBILE_FIRST_CALL_INTERMITTENT`
- `MOBILE_FIRST_MEMORY_INTERMITTENT`
- `EXTERNAL_VOICE_BARGE_IN_LATENCY`

These internal labels are not user-facing and this phase does not fix them.

## Manual acceptance

1. Start a local/production Marin call, complete one normal turn, and end it.
2. Start a Simran call, complete one normal turn, and end it.
3. Confirm the audio/UI behavior is unchanged.
4. Confirm each call emits one `call_started` and one `call_ended` operational record with a
   common safe ID, sensible duration/counters, and no conversation content.
