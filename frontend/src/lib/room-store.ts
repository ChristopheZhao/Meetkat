import type {
  Participant,
  RoomEvent,
  RoomSnapshot,
  TranscriptEntry,
  TurnPlan,
} from "./types";

type Listener = () => void;

function cloneSnapshot(snapshot: RoomSnapshot): RoomSnapshot {
  return {
    ...snapshot,
    constraints: [...snapshot.constraints],
    current_turns: snapshot.current_turns.map((item) => ({ ...item })),
    planning_artifacts: { ...snapshot.planning_artifacts },
    participants: snapshot.participants.map(cloneParticipant),
    transcript: snapshot.transcript.map((item) => ({
      ...item,
      artifacts: { ...item.artifacts },
    })),
    live_chunks: { ...snapshot.live_chunks },
    consensus: { ...snapshot.consensus },
    action_items: [...snapshot.action_items],
    open_questions: [...snapshot.open_questions],
  };
}

function cloneParticipant(participant: Participant): Participant {
  return {
    ...participant,
    focus_areas: [...participant.focus_areas],
  };
}

function mapStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item)).filter((item) => item.trim().length > 0);
}

function mapTurnPlan(value: unknown): TurnPlan[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const role = String((item as Record<string, unknown>).role ?? "").trim();
    const task = String((item as Record<string, unknown>).task ?? "").trim();
    return role && task ? [{ role, task }] : [];
  });
}

function appendTranscriptEntry(
  snapshot: RoomSnapshot,
  event: RoomEvent,
): TranscriptEntry {
  const payload = event.payload;
  return {
    message_id: String(payload.message_id ?? event.event_id),
    seq: event.room_seq,
    ts_ms: event.ts_ms,
    role: event.role,
    producer_id: event.producer_id,
    event_type: event.event_type,
    text: String(payload.text ?? ""),
    title: String(payload.title ?? ""),
    artifacts:
      payload.artifacts && typeof payload.artifacts === "object"
        ? (payload.artifacts as Record<string, unknown>)
        : {},
  };
}

export function applyRoomEvent(
  current: RoomSnapshot,
  event: RoomEvent,
): RoomSnapshot {
  const snapshot = cloneSnapshot(current);
  snapshot.last_seq = Math.max(snapshot.last_seq, event.room_seq);
  snapshot.updated_at_ms = event.ts_ms;

  switch (event.event_type) {
    case "planning.completed": {
      snapshot.planning_artifacts =
        event.payload && typeof event.payload === "object"
          ? { ...(event.payload as Record<string, unknown>) }
          : {};
      return snapshot;
    }
    case "room.started": {
      snapshot.requirement = String(
        event.payload.requirement ?? snapshot.requirement,
      );
      snapshot.topic = String(event.payload.topic ?? snapshot.topic);
      snapshot.goal = String(event.payload.goal ?? snapshot.goal);
      snapshot.mode = String(event.payload.mode ?? snapshot.mode);
      snapshot.phase = String(event.payload.phase ?? snapshot.phase);
      snapshot.status = String(event.payload.status ?? "running");
      snapshot.current_focus = String(
        event.payload.current_focus ?? snapshot.current_focus,
      );
      snapshot.resume_token = String(
        event.payload.resume_token ?? snapshot.resume_token,
      );
      if (Array.isArray(event.payload.constraints)) {
        snapshot.constraints = mapStringList(event.payload.constraints);
      }
      if (Array.isArray(event.payload.open_questions)) {
        snapshot.open_questions = mapStringList(event.payload.open_questions);
      }
      return snapshot;
    }
    case "agent.joined": {
      const participantId = String(
        event.payload.participant_id ?? event.producer_id,
      );
      if (!snapshot.participants.find((item) => item.participant_id === participantId)) {
        snapshot.participants = [
          ...snapshot.participants,
          {
            participant_id: participantId,
            role: event.role,
            identity: String(event.payload.identity ?? "agent"),
            display_name: String(event.payload.display_name ?? event.role),
            activation: String(event.payload.activation ?? ""),
            speaking: Boolean(event.payload.speaking ?? true),
            capability_profile: String(event.payload.capability_profile ?? ""),
            join_reason: String(event.payload.join_reason ?? ""),
            focus_areas: mapStringList(event.payload.focus_areas),
          },
        ];
      }
      return snapshot;
    }
    case "message.chunk": {
      const messageId = String(event.payload.message_id ?? event.event_id);
      const chunk = String(event.payload.text_chunk ?? "");
      snapshot.live_chunks = {
        ...snapshot.live_chunks,
        [messageId]: `${snapshot.live_chunks[messageId] ?? ""}${chunk}`,
      };
      return snapshot;
    }
    case "message.commit": {
      const messageId = String(event.payload.message_id ?? event.event_id);
      const text = String(event.payload.text ?? "");
      snapshot.live_chunks = {
        ...snapshot.live_chunks,
        [messageId]: text,
      };
      return snapshot;
    }
    case "agent.message":
    case "human.message":
    case "human.override": {
      const entry = appendTranscriptEntry(snapshot, event);
      const messageId = entry.message_id;
      const liveChunks = { ...snapshot.live_chunks };
      delete liveChunks[messageId];
      snapshot.live_chunks = liveChunks;
      snapshot.transcript = [...snapshot.transcript, entry];
      if (event.event_type === "agent.message") {
        snapshot.round_index = Number(
          event.payload.round_index ?? snapshot.round_index,
        );
        snapshot.phase = String(event.payload.phase ?? snapshot.phase);
        snapshot.current_focus = String(
          event.payload.current_focus ?? snapshot.current_focus,
        );
        if (event.role === "host") {
          const artifacts = event.payload.artifacts as
            | Record<string, unknown>
            | undefined;
          if (artifacts && Object.hasOwn(artifacts, "turns")) {
            snapshot.current_turns = mapTurnPlan(artifacts.turns);
          }
        }
      }
      if (event.event_type === "human.message") {
        snapshot.last_human_message = entry.text;
      }
      if (event.event_type === "human.override") {
        snapshot.last_override = entry.text;
      }
      return snapshot;
    }
    case "agent.summary": {
      snapshot.candidate_decision = String(
        event.payload.decision_candidate ?? snapshot.candidate_decision,
      );
      if (Array.isArray(event.payload.action_items)) {
        snapshot.action_items = mapStringList(event.payload.action_items);
      }
      if (Array.isArray(event.payload.open_questions)) {
        snapshot.open_questions = mapStringList(event.payload.open_questions);
      }
      snapshot.conclusion_type = String(
        event.payload.conclusion_type ?? snapshot.conclusion_type,
      );
      snapshot.conclusion_reason = String(
        event.payload.conclusion_reason ?? snapshot.conclusion_reason,
      );
      return snapshot;
    }
    case "consensus.check": {
      snapshot.consensus = {
        score: Number(event.payload.score ?? 0),
        should_end: Boolean(event.payload.should_end),
        reason: String(event.payload.reason ?? ""),
        support: Number(event.payload.support ?? 0),
        confidence: Number(event.payload.confidence ?? 0),
        disagreement_index: Number(event.payload.disagreement_index ?? 0),
        margin_top1_top2: Number(event.payload.margin_top1_top2 ?? 0),
      };
      return snapshot;
    }
    case "agent.handoff":
    case "agent.challenge": {
      snapshot.transcript = [
        ...snapshot.transcript,
        {
          message_id: String(event.payload.message_id ?? event.event_id),
          seq: event.room_seq,
          ts_ms: event.ts_ms,
          role: event.role,
          producer_id: event.producer_id,
          event_type: event.event_type,
          text:
            event.event_type === "agent.handoff"
              ? `Handoff to ${String(event.payload.to_role ?? "next role")}: ${String(event.payload.reason ?? "")}`
              : `Challenge raised against ${String(event.payload.target_role ?? "proposal")}: ${String(event.payload.reason ?? "")}`,
          title: "",
          artifacts: { ...event.payload },
        },
      ];
      return snapshot;
    }
    case "meeting.ended": {
      snapshot.status = "ended";
      snapshot.ended_reason = String(event.payload.reason ?? "meeting ended");
      snapshot.control_reason = String(
        event.payload.control_reason ?? snapshot.control_reason,
      );
      snapshot.orchestration_end_reason = String(
        event.payload.orchestration_end_reason ??
          snapshot.orchestration_end_reason,
      );
      if (Array.isArray(event.payload.action_items)) {
        snapshot.action_items = mapStringList(event.payload.action_items);
      }
      if (Array.isArray(event.payload.open_questions)) {
        snapshot.open_questions = mapStringList(event.payload.open_questions);
      }
      if (event.payload.decision_candidate) {
        snapshot.candidate_decision = String(event.payload.decision_candidate);
      }
      snapshot.conclusion_type = String(
        event.payload.conclusion_type ?? snapshot.conclusion_type,
      );
      snapshot.conclusion_reason = String(
        event.payload.conclusion_reason ?? snapshot.conclusion_reason,
      );
      return snapshot;
    }
    default:
      return snapshot;
  }
}

export function createRoomStore(initialSnapshot: RoomSnapshot) {
  let snapshot = cloneSnapshot(initialSnapshot);
  const listeners = new Set<Listener>();

  return {
    roomId: snapshot.room_id,
    getSnapshot: () => snapshot,
    subscribe(listener: Listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    replace(nextSnapshot: RoomSnapshot) {
      snapshot = cloneSnapshot(nextSnapshot);
      listeners.forEach((listener) => listener());
    },
    applyEvent(event: RoomEvent) {
      snapshot = applyRoomEvent(snapshot, event);
      listeners.forEach((listener) => listener());
    },
  };
}
