import test from "node:test";
import assert from "node:assert/strict";

import {
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
