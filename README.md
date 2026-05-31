# Meetkat

> A native-agent decision room: autonomous specialists, hub-and-spoke supervision, persistent memory, exportable records.

Meetkat is a multi-agent runtime where a group of specialist agents debate a real decision in a virtual room. A supervisor agent conducts the order of speaking, but never the content — each specialist plans, retrieves context from memory, and decides what to say on its own. The room exports an auditable Markdown decision record when consensus is reached.

This is an early-stage research project. APIs, schemas, and the on-disk decision-record format may change without notice.

---

## What's in the box

- **Hub-and-spoke MAS runtime** — central supervisor sequences `WHO speaks WHEN`; specialist agents own `WHAT` they say.
- **LLM-based role planner** — supervisor uses an LLM (not a hand-written rule pipeline) to pick the next speaker and focus angle each round.
- **Memory substrate** — short-term room memory (conversation + facts) plus long-term per-role lessons that accumulate across meetings.
- **Decision record export** — pure projection of a room snapshot into structured Chinese Markdown (decision, action items, role positions, supervisor reasoning, full transcript).
- **Demo frontend** — React/Vite room view with per-role procedurally generated avatars and a one-click record download.

## Architecture in one paragraph

The room is a directed-message bus. Each round, the supervisor receives the latest snapshot, calls its LLM, and emits a `supervisor.plan` event nominating one or more specialists plus an optional focus angle. Each nominated specialist receives the snapshot, queries memory, runs its own LLM with its role blueprint, and emits an `agent.message`. The room loop drives this until the supervisor returns `ended`. The runtime is deliberately schema-first: every event in the journal is fully replayable, and the decision record renderer is a pure function of `(snapshot, room_memory, role_lessons)`.

See `docs/prd/` for the full PRD and `docs/decisions/` for two end-to-end examples (microservice split + AI coding assistant rollout).

## Quickstart

Requirements: Python 3.12+, Node 20+, `uv` (for backend deps).

```bash
# 1. Backend (FastAPI + WebSocket room runtime on :8012)
uv sync
uv run python scripts/run_room_runtime.py

# 2. Frontend (Vite dev server on :5174) — separate terminal
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5174`, create a room, watch the specialists discuss in real time.

### End-to-end CLI run (no frontend)

```bash
uv run python scripts/run_meeting_and_export.py \
  --topic "Split an 18-person team into 2-3 microservices" \
  --rounds 3
```

This drives a real multi-round meeting and writes `docs/decisions/YYYY-MM-DD-<topic-slug>.md`.

### Backend tests

```bash
uv run pytest
```

121 backend tests + 10 frontend contract tests at the time of writing.

## Configuration

LLM provider config lives in `.env` (see `.env.example`). The default profile assumes an OpenAI-compatible endpoint; alternative providers can be wired through the same adapter interface.

## Project status

- ✅ Real multi-round LLM meetings with role-distinct positions
- ✅ Decision record export with full audit trail
- ✅ Demo frontend with real-time agent timeline
- ✅ Specialist ReAct loop with autonomous challenge / refusal under role contracts
- ✅ Tool registry + MCP adapter wired into the agent loop
- ✅ Measured convergence signals (claim clustering, support / confidence / disagreement scores)
- ✅ Native-agent clarification: requirement ambiguity is resolved inside the meeting via the supervisor's clarification protocol and the room's human-message channel; preflight is now infra-only (provider/env/transport) and never gates room start on operator inputs
- 🚧 Cross-meeting decision index (`docs/decisions/INDEX.md` auto-generation)
- 🚧 Tool extension SDK — contributor-facing docs and examples for third-party tool authors

## Repository layout

```
src/decision_room/        Python runtime (rooms, roles, supervisor, memory, providers)
scripts/                  CLI entry points (run_room_runtime, run_meeting_and_export, ...)
frontend/                 React/Vite demo UI
docs/prd/                 Product requirements documents
docs/decisions/           Exported decision records (real LLM runs)
docs/plans/               Architectural / sprint plans (SDD format)
docs/adr/                 Architecture decision records
tests/                    pytest suite
```

## Contributing

Open issues and PRs are welcome. The architectural rule of thumb: **specialist autonomy is non-negotiable**. If a change makes the supervisor decide *what* a specialist says (vs. *when* and *about what*), it's the wrong shape — discuss in an issue first.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
