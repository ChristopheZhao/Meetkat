# Centralized MAS Delivery Boundary

- Date: 2026-05-05 (initial), 2026-05-26 (amended after architecture review)
- Status: active-delivery-boundary
- Scope: online decision-room MAS delivery path

## 0. Amendment 2026-05-26

The first version of this doc was published alongside commit 5f47149, which shipped a deterministic stub `CentralizedMeetingSupervisor` whose role outputs were 4 hardcoded paragraphs keyed by `role.stance`, no LLM calls. That stub does NOT satisfy this boundary. The active remediation is PLAN-20260513-006: replace the stub with a real `LLMSupervisor` that owns role selection and per-agent assignment contracts via a real LLM call, while specialists reuse `LLMRoomExecutor._generate_argument` machinery and convergence stays gated by `HybridConsensusStrategy`. Until that remediation lands, this boundary is the design intent, not the as-shipped state.

Binding clarifications:

1. Supervisor and specialist outputs must come from LLM calls at delivery. A deterministic offline implementation is allowed only as an explicit governance/test capability, never as a silent default executor mode.
2. The product UI must read the `central_mas` artifact bundle through the same `PRODUCT_OPERATOR_CONTRACT_SECTIONS` allowlist that gates every other product-facing artifact. Direct reads outside the allowlist are forbidden.
3. `DECISION_ROOM_EXECUTOR=centralized` is an opt-in product mode that requires real provider env. Missing env must surface as `UnavailableRoomExecutor`, never as stub output.

## 1. Accepted Direction

Meetkat's usable delivery path is a centralized online MAS:

`Operator requirement -> Central Supervisor -> typed role assignments -> shared room memory -> event-backed communication -> decision record`

The supervisor is the only owner of:

1. role selection
2. per-round assignment contracts
3. convergence recommendation
4. decision-record closure

Specialist agents are typed role nodes. They do not select global topology, mutate runtime control, or create independent memory owners.

## 2. Reference Inputs

This boundary intentionally borrows only transferable patterns:

1. From `short-video-maker`:
   - agent catalog before assignment
   - explicit per-agent assignment contract
   - runtime event/status stream as product communication
   - orchestrator-owned dispatch instead of peer-to-peer free-for-all
2. From `Mellis`:
   - single-supervisor, shared-memory, graph-based multi-role agent system
   - state carries references/projections, not provider clients or long-term memory bodies
   - runtime host, gates, and governance are not semantic owners

Rejected carry-overs:

1. distributed multi-agent runtime
2. product-specific video workflow stages
3. validation harness semantics as product entry defaults
4. framework lock-in before the current runtime boundary is stable

## 3. Layer Boundary

### Memory

`RoomEventJournal` remains the only room/session source of truth. Supervisor state, role outputs, transcript entries, live chunks, consensus, and result views are projections from events.

### Orchestration

`CentralizedMeetingSupervisor` owns semantic routing:

1. builds a shared memory projection
2. selects typed role nodes
3. emits assignment contracts
4. aggregates role work products into a decision candidate

### Role Nodes

Role nodes consume only:

1. current requirement
2. room-start contract outputs
3. shared memory projection
4. supervisor assignment contract

Role nodes return structured work products: claim, evidence, confidence, target claim, and action item. They do not own room lifecycle or transport.

### Control

`RoomControlPolicy` still owns write admission, end publication eligibility, max rounds, override termination, and runtime failure end payloads.

### Runtime / Transport

`RoomRuntime` remains the execution harness. It wires planner, supervisor executor, control, journal, projector, subscribers, HTTP, SSE, and WebSocket. It does not own MAS semantics.

### Governance

Evaluation and smoke harnesses stay explicit governance capabilities. They may reuse the same room path, but they must not add validation-specific entry scopes or default product panels.

## 4. Product Contract

The product must show a working room as the primary experience:

1. create a room from one deep decision requirement
2. show the central supervisor state and assignment contracts
3. show live role work products via WebSocket with SSE fallback
4. allow human message and human override against the actual room runtime
5. show the decision record, risks, action items, and replay-backed conclusion
6. use generated visual assets for the meeting environment and agent roles

## 5. Verification Contract

Completion evidence must include:

1. backend tests covering default centralized runtime bootstrap and central MAS event artifacts
2. frontend contract/build validation
3. browser verification of room creation, live transcript, role visuals, and human intervention controls
4. completion audit mapping every explicit delivery requirement to concrete evidence
