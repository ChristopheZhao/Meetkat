import {
  startTransition,
  useDeferredValue,
  useEffect,
  useEffectEvent,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useLoaderData, useNavigate } from "react-router";
import {
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { postHumanMessage, postHumanOverride } from "../lib/api";
import decisionScribeAvatar from "../assets/agents/decision-scribe.webp";
import productStrategistAvatar from "../assets/agents/product-strategist.webp";
import riskControllerAvatar from "../assets/agents/risk-controller.webp";
import supervisorAvatar from "../assets/agents/supervisor.webp";
import systemsArchitectAvatar from "../assets/agents/systems-architect.webp";
import {
  buildRoleDirectory,
  formatToken,
  readLatestCentralMasState,
  readProductOperatorContractSections,
  readOperatorContextValue,
  readPlanningText,
  readPlannedSpecialists,
  readPreflightBoolean,
  readPreflightList,
  readPreflightValue,
  readRuntimeExecutorTargets,
  readRuntimePlannerTarget,
} from "../lib/planning-artifacts";
import { createRoomStore } from "../lib/room-store";
import { connectRoomStream, type ConnectionState } from "../lib/transport";
import type { RoomEvent, RoomSnapshot } from "../lib/types";
import styles from "./room.module.css";

function useRoomSession(initialSnapshot: RoomSnapshot) {
  const storeRef = useRef(createRoomStore(initialSnapshot));
  const [connectionState, setConnectionState] =
    useState<ConnectionState>(
      initialSnapshot.status === "ended" ? "disconnected" : "connecting",
    );

  if (storeRef.current.roomId !== initialSnapshot.room_id) {
    storeRef.current = createRoomStore(initialSnapshot);
  }

  const onEvent = useEffectEvent((event: RoomEvent) => {
    startTransition(() => {
      storeRef.current.applyEvent(event);
    });
  });

  const onStateChange = useEffectEvent((state: ConnectionState) => {
    setConnectionState(state);
  });

  useEffect(() => {
    storeRef.current.replace(initialSnapshot);
    if (initialSnapshot.status === "ended") {
      setConnectionState("disconnected");
      return () => undefined;
    }

    setConnectionState("connecting");
    const teardown = connectRoomStream({
      roomId: initialSnapshot.room_id,
      afterSeq: initialSnapshot.last_seq,
      onEvent,
      onStateChange,
    });
    return teardown;
  }, [initialSnapshot.room_id, initialSnapshot.last_seq, initialSnapshot.status]);

  const snapshot = useSyncExternalStore(
    storeRef.current.subscribe,
    storeRef.current.getSnapshot,
    storeRef.current.getSnapshot,
  );

  return { snapshot, connectionState };
}

const roleLabelMap: Record<string, string> = {
  host: "Host",
  human: "Human",
  system: "System",
  synthesis: "Synthesis",
  implementation_specialist: "Systems Architect",
  product_specialist: "Product Strategist",
  risk_specialist: "Risk Controller",
  operations_specialist: "Decision Scribe",
};

const roleAvatarMap: Record<string, string> = {
  host: supervisorAvatar,
  supervisor: supervisorAvatar,
  implementation_specialist: systemsArchitectAvatar,
  systems_architect: systemsArchitectAvatar,
  product_specialist: productStrategistAvatar,
  product_strategist: productStrategistAvatar,
  risk_specialist: riskControllerAvatar,
  risk_controller: riskControllerAvatar,
  operations_specialist: decisionScribeAvatar,
  decision_scribe: decisionScribeAvatar,
  synthesis: decisionScribeAvatar,
};

function buildInitials(value: string): string {
  const parts = value
    .split(/[_\s]+/)
    .filter(Boolean)
    .slice(0, 2);
  if (parts.length === 0) {
    return "??";
  }
  return parts.map((part) => part[0]?.toUpperCase() ?? "").join("");
}

function getRoleVisual(role: string) {
  const normalized = role.trim().toLowerCase();
  return {
    label: roleLabelMap[normalized] || formatToken(normalized) || "Unknown",
    initials: buildInitials(normalized),
    avatarSrc: roleAvatarMap[normalized],
  };
}

type RoleDirectory = Record<string, string>;

function resolveRoleLabel(role: string, roleDirectory: RoleDirectory): string {
  const normalized = role.trim().toLowerCase();
  return roleDirectory[normalized] || getRoleVisual(normalized).label;
}

function RoleAvatar({
  role,
  roleDirectory,
}: {
  role: string;
  roleDirectory: RoleDirectory;
}) {
  const normalized = role.trim().toLowerCase();
  const label = resolveRoleLabel(normalized, roleDirectory);
  const avatarSrc = getRoleVisual(normalized).avatarSrc;
  const initials = buildInitials(label);

  if (avatarSrc) {
    return (
      <span className={styles.avatarFrame}>
        <img
          alt={`${label} avatar`}
          className={styles.avatarImage}
          src={avatarSrc}
        />
      </span>
    );
  }

  return (
    <span className={styles.avatarFallback} data-role={role}>
      {initials}
    </span>
  );
}

export function RoomPage() {
  const initialSnapshot = useLoaderData() as RoomSnapshot;
  const { snapshot, connectionState } = useRoomSession(initialSnapshot);
  const deferredTranscript = useDeferredValue(snapshot.transcript);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [humanMessage, setHumanMessage] = useState("");
  const [overrideText, setOverrideText] = useState("Stop and lock the current draft.");
  const roomEnded = snapshot.status === "ended";
  const planningObjective = readPlanningText(
    snapshot,
    "meeting_objective",
    snapshot.goal,
  );
  const planningInitialFocus = readPlanningText(
    snapshot,
    "initial_focus",
    snapshot.current_focus,
  );
  const plannedSpecialists = useMemo(
    () => readPlannedSpecialists(snapshot),
    [snapshot],
  );
  const roleDirectory = useMemo(
    () => buildRoleDirectory(snapshot, plannedSpecialists),
    [snapshot, plannedSpecialists],
  );
  const conclusionType = formatToken(snapshot.conclusion_type || "pending");
  const conclusionReason =
    snapshot.conclusion_reason || snapshot.ended_reason || snapshot.consensus.reason;
  const entryScope = readOperatorContextValue(snapshot, "entry_scope");
  const preflightReady = readPreflightBoolean(snapshot, "room_start_ready");
  const recommendedSurface = readPreflightValue(snapshot, "recommended_surface");
  const plannerTarget = readRuntimePlannerTarget(snapshot);
  const executorTargets = readRuntimeExecutorTargets(snapshot);
  const operatorContractSections = readProductOperatorContractSections(snapshot);
  const missingOperatorInputs = readPreflightList(snapshot, "missing_operator_inputs");
  const centralMas = readLatestCentralMasState(snapshot);
  const speakers = centralMas?.speakers ?? [];
  const centralTopology = centralMas?.topology ?? "";

  const humanMessageMutation = useMutation({
    mutationFn: (text: string) => postHumanMessage(snapshot.room_id, text),
    onSuccess: async () => {
      setHumanMessage("");
      await queryClient.invalidateQueries({ queryKey: ["rooms"] });
    },
  });

  const humanOverrideMutation = useMutation({
    mutationFn: (text: string) => postHumanOverride(snapshot.room_id, text),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["rooms"] });
    },
  });

  const humanInterventionNotice = roomEnded
    ? snapshot.ended_reason || "This room has ended and no longer accepts writes."
    : humanMessageMutation.error instanceof Error
      ? humanMessageMutation.error.message
      : humanOverrideMutation.error instanceof Error
        ? humanOverrideMutation.error.message
        : "";

  const pendingMessages = useMemo(
    () =>
      Object.entries(snapshot.live_chunks).map(([messageId, text]) => ({
        messageId,
        text,
      })),
    [snapshot.live_chunks],
  );

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p className={styles.statusLine}>
            {snapshot.status} · {snapshot.phase} · round {snapshot.round_index} · brief {snapshot.brief_source}
          </p>
          <h2 className={styles.topic}>{snapshot.topic}</h2>
          <p className={styles.goal}>{snapshot.goal || snapshot.current_focus}</p>
          <p className={styles.requirement}>
            Requirement: {snapshot.requirement || "No original requirement recorded."}
          </p>
        </div>
        <div className={styles.connection}>
          <span className={styles.connectionLabel}>Transport</span>
          <strong>{connectionState}</strong>
        </div>
      </header>

      <div className={styles.grid}>
        <aside className={styles.sidePanel}>
          {snapshot.brief_source !== "agent" ? (
            <section className={styles.warningCard}>
              <h3>Planner status</h3>
              <p className={styles.warningCopy}>
                This room was created without a validated agent-planned brief.
              </p>
              <p className={styles.warningReason}>
                {snapshot.brief_source_reason || "No planner failure reason was recorded."}
              </p>
            </section>
          ) : null}

          <section className={styles.panelCard}>
            <h3>Meeting brief</h3>
            <span className={styles.sectionLabel}>Objective</span>
            <p className={styles.focus}>{planningObjective || "Waiting for planner objective"}</p>
            <span className={styles.sectionLabel}>Current focus</span>
            <p className={styles.secondaryText}>
              {snapshot.current_focus || planningInitialFocus || "Waiting for host focus"}
            </p>
            <ul className={styles.constraintList}>
              {snapshot.constraints.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            {snapshot.open_questions.length > 0 ? (
              <div className={styles.openQuestions}>
                <h4>Open questions</h4>
                <ul className={styles.questionList}>
                  {snapshot.open_questions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <section className={styles.panelCard}>
            <h3>Pre-room contract</h3>
            <div className={styles.contractMeta}>
              {entryScope ? (
                <div>
                  <span className={styles.sectionLabel}>Entry scope</span>
                  <p className={styles.secondaryText}>{formatToken(entryScope)}</p>
                </div>
              ) : null}
              {preflightReady !== null ? (
                <div>
                  <span className={styles.sectionLabel}>Room start gate</span>
                  <p className={styles.secondaryText}>
                    {preflightReady
                      ? "Passed before room creation."
                      : "Room started without clearing the room-start gate."}
                  </p>
                </div>
              ) : null}
              {recommendedSurface ? (
                <div>
                  <span className={styles.sectionLabel}>Recommended surface</span>
                  <p className={styles.secondaryText}>
                    {formatToken(recommendedSurface)}
                  </p>
                </div>
              ) : null}
              {plannerTarget ? (
                <div>
                  <span className={styles.sectionLabel}>Planner target</span>
                  <p className={styles.secondaryText}>{plannerTarget}</p>
                </div>
              ) : null}
              {executorTargets ? (
                <div>
                  <span className={styles.sectionLabel}>Executor targets</span>
                  <p className={styles.secondaryText}>{executorTargets}</p>
                </div>
              ) : null}
            </div>
            {operatorContractSections.length === 0 ? (
              <p className={styles.reason}>
                No explicit entry-scope contract was persisted for this room.
              </p>
            ) : (
              operatorContractSections.map((section) => (
                <div className={styles.contractSection} key={section.title}>
                  <span className={styles.sectionLabel}>{section.title}</span>
                  <ul className={styles.questionList}>
                    {section.items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ))
            )}
            {missingOperatorInputs.length > 0 ? (
              <div className={styles.contractSection}>
                <span className={styles.sectionLabel}>Missing operator inputs</span>
                <ul className={styles.questionList}>
                  {missingOperatorInputs.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <section className={styles.panelCard}>
            <h3>Central MAS</h3>
            <div className={styles.topologyStrip}>
              <RoleAvatar role="host" roleDirectory={roleDirectory} />
              <span />
              <RoleAvatar role="implementation_specialist" roleDirectory={roleDirectory} />
              <RoleAvatar role="product_specialist" roleDirectory={roleDirectory} />
              <RoleAvatar role="risk_specialist" roleDirectory={roleDirectory} />
              <RoleAvatar role="synthesis" roleDirectory={roleDirectory} />
            </div>
            <p className={styles.secondaryText}>
              {centralTopology
                ? "中心化主持 · 子智能体自主表达"
                : "等待主持人发布本轮发言安排…"}
            </p>
            {speakers.length > 0 ? (
              <div className={styles.assignmentList}>
                {speakers.map((slot) => (
                  <article className={styles.assignmentItem} key={slot.agent}>
                    <div className={styles.messageTitleRow}>
                      <strong>{resolveRoleLabel(slot.agent, roleDirectory)}</strong>
                      <span className={styles.eventPill}>第 {slot.order} 位</span>
                    </div>
                    <p className={styles.secondaryText}>
                      {slot.focusAngle
                        ? `角度提示：${slot.focusAngle}`
                        : "由该角色契约自主决定内容"}
                    </p>
                  </article>
                ))}
              </div>
            ) : null}
          </section>

          <section className={styles.panelCard}>
            <h3>Current turn plan</h3>
            {snapshot.current_turns.length === 0 ? (
              <p className={styles.reason}>
                The host has not published a structured turn plan yet.
              </p>
            ) : (
              <div className={styles.turnList}>
                {snapshot.current_turns.map((turn) => (
                  <div className={styles.turnItem} key={`${turn.role}-${turn.task}`}>
                    <div className={styles.turnRole}>
                      {resolveRoleLabel(turn.role, roleDirectory)}
                    </div>
                    <p className={styles.turnTask}>{turn.task}</p>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className={styles.panelCard}>
            <h3>Planned specialists</h3>
            {plannedSpecialists.length === 0 ? (
              <p className={styles.reason}>
                No pre-room specialist roster is available in the snapshot.
              </p>
            ) : (
              <div className={styles.rosterList}>
                {plannedSpecialists.map((specialist) => (
                  <div className={styles.rosterItem} key={specialist.role}>
                    <div className={styles.messageTitleRow}>
                      <strong>{specialist.displayName}</strong>
                      <span className={styles.eventPill}>
                        {getRoleVisual(specialist.role).label}
                      </span>
                    </div>
                    {specialist.capabilityProfile ? (
                      <p className={styles.secondaryText}>
                        {specialist.capabilityProfile}
                      </p>
                    ) : null}
                    {specialist.joinReason ? (
                      <p className={styles.secondaryText}>{specialist.joinReason}</p>
                    ) : null}
                    {specialist.focusAreas.length > 0 ? (
                      <p className={styles.rosterMeta}>
                        Focus: {specialist.focusAreas.join(" · ")}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className={styles.panelCard}>
            <h3>Participants</h3>
            <div className={styles.participantList}>
              {snapshot.participants.map((participant) => (
                <div
                  className={styles.participant}
                  data-role={participant.role}
                  key={participant.participant_id}
                >
                  <div className={styles.participantIdentity}>
                    <RoleAvatar role={participant.role} roleDirectory={roleDirectory} />
                    <div>
                      <span>{participant.display_name}</span>
                      <small>{getRoleVisual(participant.role).label}</small>
                      {participant.capability_profile ? (
                        <small className={styles.participantMeta}>
                          {participant.capability_profile}
                        </small>
                      ) : null}
                    </div>
                  </div>
                  <span className={styles.participantStatus}>
                    {participant.activation || (participant.speaking ? "speaking" : "attached")}
                  </span>
                </div>
              ))}
            </div>
          </section>
        </aside>

        <section className={styles.transcriptPanel}>
          <div className={styles.panelHeader}>
            <h3>Live Transcript</h3>
            {snapshot.status === "ended" ? (
              <button
                className={styles.secondaryButton}
                onClick={() => navigate(`/rooms/${snapshot.room_id}/results`)}
                type="button"
              >
                View results
              </button>
            ) : null}
          </div>

          <div className={styles.transcriptList}>
            {deferredTranscript.map((entry) => (
              <article
                className={styles.transcriptCard}
                data-role={entry.role}
                key={`${entry.seq}-${entry.message_id}`}
              >
                <div className={styles.messageHeader}>
                  <RoleAvatar role={entry.role} roleDirectory={roleDirectory} />
                  <div className={styles.messageIdentity}>
                    <div className={styles.messageTitleRow}>
                      <strong>{resolveRoleLabel(entry.role, roleDirectory)}</strong>
                      <span className={styles.eventPill}>{entry.event_type}</span>
                    </div>
                    {entry.title ? <h4>{entry.title}</h4> : null}
                  </div>
                </div>
                <p className={styles.messageCopy}>{entry.text}</p>
              </article>
            ))}
            {pendingMessages.map((item) => (
              <article className={styles.pendingCard} key={item.messageId}>
                <div className={styles.messageHeader}>
                  <span className={styles.avatarFallback} data-role="system">
                    ST
                  </span>
                  <div className={styles.messageIdentity}>
                    <div className={styles.messageTitleRow}>
                      <strong>Streaming</strong>
                      <span className={styles.eventPill}>message.chunk</span>
                    </div>
                  </div>
                </div>
                <p className={styles.messageCopy}>{item.text}</p>
              </article>
            ))}
          </div>
        </section>

        <aside className={styles.sidePanel}>
          <section className={styles.panelCard}>
            <h3>Conclusion contract</h3>
            <p className={styles.focus}>{conclusionType}</p>
            <p className={styles.reason}>
              {conclusionReason || "The meeting has not published a conclusion contract yet."}
            </p>
          </section>

          <section className={styles.panelCard}>
            <h3>Convergence</h3>
            <dl className={styles.metricGrid}>
              <div>
                <dt>Score</dt>
                <dd>{snapshot.consensus.score.toFixed(2)}</dd>
              </div>
              <div>
                <dt>Support</dt>
                <dd>{snapshot.consensus.support.toFixed(2)}</dd>
              </div>
              <div>
                <dt>Confidence</dt>
                <dd>{snapshot.consensus.confidence.toFixed(2)}</dd>
              </div>
              <div>
                <dt>Disagreement</dt>
                <dd>{snapshot.consensus.disagreement_index.toFixed(2)}</dd>
              </div>
            </dl>
            <p className={styles.reason}>{snapshot.consensus.reason || "Waiting for consensus check"}</p>
          </section>

          <section className={styles.panelCard}>
            <h3>Human intervention</h3>
            {humanInterventionNotice ? (
              <p
                className={
                  roomEnded ? styles.lockedNotice : styles.errorNotice
                }
              >
                {humanInterventionNotice}
              </p>
            ) : null}
            <label className={styles.field}>
              <span>Message</span>
              <textarea
                disabled={roomEnded || humanMessageMutation.isPending}
                rows={4}
                value={humanMessage}
                onChange={(event) => {
                  humanMessageMutation.reset();
                  setHumanMessage(event.target.value);
                }}
                placeholder="Inject a question, constraint, or correction into the room."
              />
            </label>
            <button
              className={styles.primaryButton}
              disabled={
                roomEnded ||
                humanMessageMutation.isPending ||
                !humanMessage.trim()
              }
              onClick={() => humanMessageMutation.mutate(humanMessage)}
              type="button"
            >
              {humanMessageMutation.isPending ? "Sending..." : "Send message"}
            </button>

            <label className={styles.field}>
              <span>Override</span>
              <textarea
                disabled={roomEnded || humanOverrideMutation.isPending}
                rows={3}
                value={overrideText}
                onChange={(event) => {
                  humanOverrideMutation.reset();
                  setOverrideText(event.target.value);
                }}
              />
            </label>
            <button
              className={styles.overrideButton}
              disabled={
                roomEnded ||
                humanOverrideMutation.isPending ||
                !overrideText.trim()
              }
              onClick={() => humanOverrideMutation.mutate(overrideText)}
              type="button"
            >
              {humanOverrideMutation.isPending ? "Applying..." : "Override and end"}
            </button>
          </section>
        </aside>
      </div>
    </div>
  );
}
