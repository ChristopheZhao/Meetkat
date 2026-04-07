import test from "node:test";
import assert from "node:assert/strict";

import { applyRoomEvent } from "../.contract-dist/lib/room-store.js";

function makeSnapshot(overrides = {}) {
  return {
    room_id: "room_contract",
    requirement: "Need contract guards",
    topic: "Contract guards",
    goal: "Keep the frontend bound to authoritative room facts.",
    brief_source: "agent",
    brief_source_reason: "",
    mode: "agent_first",
    status: "running",
    phase: "explore",
    round_index: 1,
    current_focus: "check guards",
    current_turns: [
      { role: "implementation_specialist", task: "Keep the current turn plan." },
    ],
    constraints: [],
    planning_artifacts: {},
    participants: [],
    transcript: [],
    live_chunks: {},
    consensus: {
      score: 0,
      should_end: false,
      reason: "",
      support: 0,
      confidence: 0,
      disagreement_index: 1,
      margin_top1_top2: 0,
    },
    candidate_decision: "",
    action_items: [],
    open_questions: [],
    last_human_message: "",
    last_override: "",
    conclusion_type: "candidate_ready",
    conclusion_reason: "Authoritative conclusion already present.",
    ended_reason: "",
    control_reason: "",
    orchestration_end_reason: "",
    resume_token: "room_contract",
    created_at_ms: 1,
    updated_at_ms: 1,
    last_seq: 1,
    ...overrides,
  };
}

function makeEvent(overrides = {}) {
  return {
    schema_version: "1.0",
    event_id: "evt_contract",
    room_id: "room_contract",
    room_seq: 2,
    producer_id: "agent.host.1",
    role: "host",
    event_type: "agent.message",
    ts_ms: 2,
    payload: {
      round_index: 2,
      phase: "synthesize",
      current_focus: "keep the authoritative values",
      title: "Round focus",
      text: "Host update",
      artifacts: {},
    },
    ...overrides,
  };
}

test("host events without turns do not erase an authoritative turn plan", () => {
  const snapshot = makeSnapshot();
  const next = applyRoomEvent(snapshot, makeEvent());

  assert.deepEqual(next.current_turns, snapshot.current_turns);
});

test("non-host events cannot overwrite the current turn plan even if they carry turns", () => {
  const snapshot = makeSnapshot();
  const next = applyRoomEvent(
    snapshot,
    makeEvent({
      producer_id: "agent.risk_specialist.1",
      role: "risk_specialist",
      payload: {
        round_index: 2,
        phase: "synthesize",
        current_focus: "risk response",
        title: "Risk readout",
        text: "Do not trust specialist-supplied turn plans.",
        artifacts: {
          turns: [{ role: "risk_specialist", task: "bad overwrite attempt" }],
        },
      },
    }),
  );

  assert.deepEqual(next.current_turns, snapshot.current_turns);
});

test("summary events without explicit conclusion fields preserve the current conclusion contract", () => {
  const snapshot = makeSnapshot();
  const next = applyRoomEvent(snapshot, {
    schema_version: "1.0",
    event_id: "evt_summary",
    room_id: "room_contract",
    room_seq: 3,
    producer_id: "capability.synthesis.1",
    role: "synthesis",
    event_type: "agent.summary",
    ts_ms: 3,
    payload: {
      summary: "Synthesis should not erase an existing conclusion contract.",
      decision_candidate: "Keep the existing conclusion until a new one is explicit.",
    },
  });

  assert.equal(next.conclusion_type, "candidate_ready");
  assert.equal(
    next.conclusion_reason,
    "Authoritative conclusion already present.",
  );
});
