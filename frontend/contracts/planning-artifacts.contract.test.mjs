import test from "node:test";
import assert from "node:assert/strict";

import {
  readLatestCentralMasState,
  readProductOperatorContractSections,
  readProductOperatorContractSectionsFromContext,
} from "../.contract-dist/lib/planning-artifacts.js";

function makeSnapshot(operatorContext = {}) {
  return {
    room_id: "room_contract",
    requirement: "Need product contract guards",
    topic: "Contract guards",
    goal: "Keep product surfaces bound to canonical room-start facts.",
    brief_source: "agent",
    brief_source_reason: "",
    mode: "agent_first",
    status: "running",
    phase: "explore",
    round_index: 1,
    current_focus: "check product contract sections",
    current_turns: [],
    constraints: [],
    planning_artifacts: {
      operator_context: operatorContext,
    },
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
  };
}

test("product operator contract sections exclude validation-specific acceptance panels", () => {
  const operatorContext = {
    entry_contract: ["Room start must stay operator-visible."],
    operator_required_inputs: ["Confirm evidence source before room start."],
    human_control_contract: ["Human override remains available during the room."],
    validation_scenario: ["Should not render on product surfaces."],
    binding_readiness_contract: ["Should not render on product surfaces."],
    transport_contract: ["Should not render on product surfaces."],
    projection_contract: ["Should not render on product surfaces."],
    evidence_contract: ["Should not render on product surfaces."],
    conclusion_contract: ["Should not render on product surfaces."],
  };
  const snapshot = makeSnapshot(operatorContext);

  const sections = readProductOperatorContractSections(snapshot);
  const preflightSections = readProductOperatorContractSectionsFromContext(operatorContext);
  const titles = sections.map((section) => section.title);
  const flattenedItems = sections.flatMap((section) => section.items);

  assert.deepEqual(titles, [
    "Entry contract",
    "Operator-required inputs",
    "Human control surface",
  ]);
  assert.deepEqual(flattenedItems, [
    "Room start must stay operator-visible.",
    "Confirm evidence source before room start.",
    "Human override remains available during the room.",
  ]);
  assert.equal(
    flattenedItems.includes("Should not render on product surfaces."),
    false,
  );
  assert.deepEqual(preflightSections, sections);
});

function makeSnapshotWithTranscript(transcript) {
  const snapshot = makeSnapshot();
  snapshot.transcript = transcript;
  return snapshot;
}

test("readLatestCentralMasState gates on supervisor topology and drops unknown fields", () => {
  const validBundle = {
    topology: "single_supervisor_shared_memory",
    decision_focus: "converge on supervisor direction",
    reason: "supervisor selected specialists",
    role_catalog: [
      { role: "implementation_specialist", display_name: "Implementation Specialist", mission: "feasibility" },
      { role: "leak_role", display_name: "Should be allowed only because parser does not blacklist roles here", mission: "x" },
    ],
    assignment_contracts: [
      {
        agent: "implementation_specialist",
        run: true,
        mission: "drive feasibility",
        deliverable: "readout",
        unknown_field: "should-not-appear",
      },
      {
        agent: "",
        run: true,
        mission: "missing agent should drop",
        deliverable: "x",
      },
      {
        agent: "risk_specialist",
        run: false,
        mission: "not running this round",
        deliverable: "x",
      },
    ],
    supervisor_state: { foo: "bar", unknown: 1 },
    extra_unknown: "ignored",
  };
  const snapshot = makeSnapshotWithTranscript([
    {
      event_id: "evt1",
      seq: 1,
      role: "host",
      title: "older",
      text: "older",
      event_type: "agent.message",
      artifacts: { central_mas: { topology: "free_discussion" } },
    },
    {
      event_id: "evt2",
      seq: 2,
      role: "host",
      title: "latest",
      text: "latest",
      event_type: "agent.message",
      artifacts: { central_mas: validBundle },
    },
  ]);
  const state = readLatestCentralMasState(snapshot);
  assert.ok(state, "expected a parsed CentralMasStateView");
  assert.equal(state.topology, "single_supervisor_shared_memory");
  assert.equal(state.decisionFocus, "converge on supervisor direction");
  assert.equal(state.reason, "supervisor selected specialists");
  assert.deepEqual(
    state.assignmentContracts.map((c) => c.agent),
    ["implementation_specialist"],
  );
  assert.equal("unknown_field" in state.assignmentContracts[0], false);
  assert.equal(state.roleCatalog.length, 2);
});

test("readLatestCentralMasState rejects payloads with unknown topology", () => {
  const snapshot = makeSnapshotWithTranscript([
    {
      event_id: "evt1",
      seq: 1,
      role: "host",
      title: "rogue",
      text: "rogue",
      event_type: "agent.message",
      artifacts: {
        central_mas: {
          topology: "validation_acceptance_panel",
          assignment_contracts: [
            { agent: "implementation_specialist", run: true, mission: "x", deliverable: "x" },
          ],
        },
      },
    },
  ]);
  assert.equal(readLatestCentralMasState(snapshot), null);
});

test("readLatestCentralMasState returns null when no valid supervisor artifact exists", () => {
  const snapshot = makeSnapshotWithTranscript([
    {
      event_id: "evt1",
      seq: 1,
      role: "host",
      title: "no bundle",
      text: "no bundle",
      event_type: "agent.message",
      artifacts: {},
    },
  ]);
  assert.equal(readLatestCentralMasState(snapshot), null);
});
