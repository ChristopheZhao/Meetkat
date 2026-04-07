import { useState, type FormEvent } from "react";
import {
  useLoaderData,
  useNavigate,
} from "react-router";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  ApiError,
  createRoom,
  listRooms,
  preflightRoom,
} from "../lib/api";
import type {
  CreateRoomInput,
  RoomPreflightReport,
  RoomSummary,
  ProviderTarget,
  RuntimeReadiness,
} from "../lib/types";
import { readProductOperatorContractSectionsFromContext } from "../lib/planning-artifacts";
import styles from "./home.module.css";

const HOME_ENTRY_SCOPE = "interactive_room_start";

function formatToken(value: string): string {
  return value
    .split(/[_\s,]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function extractPreflightReport(error: unknown): RoomPreflightReport | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || !("preflight" in detail)) {
    return null;
  }
  const payload = detail.preflight;
  if (!payload || typeof payload !== "object" || !("room_start_contract" in payload)) {
    return null;
  }
  return payload as unknown as RoomPreflightReport;
}

function plannerStatus(readiness: RuntimeReadiness): string {
  if (readiness.primary_planner_ready) {
    return "Primary planner ready";
  }
  if (readiness.fallback_planner_ready) {
    return "Fallback planner only";
  }
  return "Planner blocked";
}

function executorStatus(readiness: RuntimeReadiness): string {
  return readiness.executor_ready ? "Executor ready" : "Executor blocked";
}

function formatTargetIdentity(target?: ProviderTarget): string {
  if (!target) {
    return "";
  }
  const supplier = String(target.supplier || "").trim();
  const model = String(target.model || "").trim();
  if (supplier && model) {
    return `${supplier}/${model}`;
  }
  return supplier || model;
}

function formatExecutorTargets(readiness: RuntimeReadiness): string {
  const targets = readiness.executor_targets;
  if (!targets || typeof targets !== "object") {
    return "";
  }
  const entries = [
    ["default", formatTargetIdentity(targets.default)],
    ["escalation", formatTargetIdentity(targets.escalation)],
    ["fallback", formatTargetIdentity(targets.fallback)],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));
  return entries
    .map(([label, value]) => `${formatToken(label)} ${value}`)
    .join(" · ");
}

export function HomePage() {
  const initialRooms = useLoaderData() as RoomSummary[];
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [requirement, setRequirement] = useState(
    "We need a runtime-first multi-agent meeting room MVP for engineering reviews. It should turn one requirement into a visible meeting brief, preserve replay, and keep human override available.",
  );
  const [preflightReport, setPreflightReport] = useState<RoomPreflightReport | null>(
    null,
  );

  const roomsQuery = useQuery({
    queryKey: ["rooms"],
    queryFn: listRooms,
    initialData: initialRooms,
    refetchInterval: 6000,
  });

  const preflightMutation = useMutation({
    mutationFn: preflightRoom,
    onSuccess: (report) => {
      setPreflightReport(report);
    },
  });

  const createMutation = useMutation({
    mutationFn: (input: CreateRoomInput) => createRoom(input),
    onSuccess: async (snapshot) => {
      await queryClient.invalidateQueries({ queryKey: ["rooms"] });
      navigate(`/rooms/${snapshot.room_id}`);
    },
  });

  const handleRequirementChange = (nextRequirement: string) => {
    setRequirement(nextRequirement);
    setPreflightReport(null);
    preflightMutation.reset();
    createMutation.reset();
  };

  const runPreflight = async (): Promise<RoomPreflightReport> => {
    createMutation.reset();
    preflightMutation.reset();
    setPreflightReport(null);
    return preflightMutation.mutateAsync({
      requirement,
      allow_planner_fallback: false,
      entry_scope: HOME_ENTRY_SCOPE,
    });
  };

  const handleCheckPreflight = async () => {
    try {
      await runPreflight();
    } catch {
      // Surface errors through the mutation state.
    }
  };

  const handleCreateRoom = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      const report = await runPreflight();
      if (!report.room_start_contract.room_start_ready) {
        return;
      }
      await createMutation.mutateAsync({
        requirement,
        mode: "agent_first",
        require_preflight_ready: true,
        entry_scope: HOME_ENTRY_SCOPE,
      });
    } catch (error) {
      const report = extractPreflightReport(error);
      if (report) {
        setPreflightReport(report);
      }
    }
  };

  const rooms = roomsQuery.data ?? [];
  const activeError = createMutation.error ?? preflightMutation.error;
  const isSubmitting = createMutation.isPending || preflightMutation.isPending;
  const plannerTarget = preflightReport
    ? formatTargetIdentity(preflightReport.runtime_readiness.planner_target)
    : "";
  const executorTargets = preflightReport
    ? formatExecutorTargets(preflightReport.runtime_readiness)
    : "";
  const entryScope = String(preflightReport?.operator_context?.entry_scope || "").trim();
  const productOperatorContractSections = readProductOperatorContractSectionsFromContext(
    preflightReport?.operator_context,
  );
  const roomStartContract = preflightReport?.room_start_contract;

  return (
    <div className={styles.layout}>
      <section className={styles.hero}>
        <p className={styles.kicker}>Runtime-first P1 baseline</p>
        <h2 className={styles.heading}>
          Start a room from the same event protocol the meeting page will replay.
        </h2>
        <p className={styles.copy}>
          Enter one requirement. The room opens only after the primary planner
          successfully produces a meeting brief and clears the room-start
          contract. Planner and room-start failures surface directly
          instead of silently degrading into local rules.
        </p>
      </section>

      <section className={styles.grid}>
        <form className={styles.card} onSubmit={handleCreateRoom}>
          <div className={styles.cardHeader}>
            <h3>Create A Room</h3>
            <span className={styles.badge}>Agent-led</span>
          </div>
          <label className={styles.field}>
            <span>Requirement</span>
            <textarea
              rows={5}
              value={requirement}
              onChange={(event) => handleRequirementChange(event.target.value)}
              placeholder="Describe the need, desired outcome, and any hard boundaries you already know."
            />
          </label>
          <p className={styles.helper}>
            This input now goes through planner + room-start contract assessment
            before room start. Missing operator inputs stay outside the room
            instead of getting rediscovered mid-meeting as late blocked outcomes.
          </p>
          <div className={styles.buttonRow}>
            <button
              className={styles.secondaryButton}
              type="button"
              onClick={() => void handleCheckPreflight()}
              disabled={isSubmitting}
            >
              {preflightMutation.isPending ? "Checking room start..." : "Check room start"}
            </button>
            <button
              className={styles.primaryButton}
              type="submit"
              disabled={isSubmitting}
            >
              {preflightMutation.isPending
                  ? "Checking room start..."
                  : createMutation.isPending
                    ? "Creating room..."
                    : "Open room"}
            </button>
          </div>
          {activeError ? (
            <p className={styles.error}>
              {String(activeError.message)}
            </p>
          ) : null}
          {preflightReport ? (
            <section className={styles.preflightPanel}>
                <div className={styles.preflightHeader}>
                  <div>
                  <p className={styles.preflightKicker}>Room-start contract</p>
                  <h4>Room start gate</h4>
                  </div>
                <span
                  className={
                    roomStartContract?.room_start_ready
                      ? styles.preflightReady
                      : styles.preflightBlocked
                  }
                >
                  {roomStartContract?.room_start_ready
                    ? "Ready to open"
                    : "Blocked before room start"}
                </span>
              </div>
              <p className={styles.preflightSummary}>
                {roomStartContract?.root_cause_hypothesis ||
                  "No external dependency blockers were detected before room start."}
              </p>
              <dl className={styles.preflightMeta}>
                <div>
                  <dt>Meeting objective</dt>
                  <dd>{preflightReport.meeting_objective}</dd>
                </div>
                <div>
                  <dt>Initial focus</dt>
                  <dd>{preflightReport.initial_focus}</dd>
                </div>
                <div>
                  <dt>Recommended surface</dt>
                  <dd>{formatToken(roomStartContract?.recommended_surface || "")}</dd>
                </div>
                {entryScope ? (
                  <div>
                    <dt>Entry scope</dt>
                    <dd>{formatToken(entryScope)}</dd>
                  </div>
                ) : null}
                <div>
                  <dt>Runtime readiness</dt>
                  <dd>
                    {plannerStatus(preflightReport.runtime_readiness)} ·{" "}
                    {executorStatus(preflightReport.runtime_readiness)}
                  </dd>
                </div>
                {plannerTarget ? (
                  <div>
                    <dt>Planner target</dt>
                    <dd>{plannerTarget}</dd>
                  </div>
                ) : null}
                {executorTargets ? (
                  <div>
                    <dt>Executor targets</dt>
                    <dd>{executorTargets}</dd>
                  </div>
                ) : null}
              </dl>
              {productOperatorContractSections.map((section) => (
                <div key={section.title}>
                  <p className={styles.listTitle}>{section.title}</p>
                  <ul className={styles.checklist}>
                    {section.items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ))}
              {roomStartContract && roomStartContract.missing_operator_inputs.length > 0 ? (
                <div>
                  <p className={styles.listTitle}>Missing operator inputs</p>
                  <ul className={styles.checklist}>
                    {roomStartContract.missing_operator_inputs.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div className={styles.preflightLists}>
                <div>
                  <p className={styles.listTitle}>System blockers</p>
                  {roomStartContract && roomStartContract.system_blockers.length === 0 ? (
                    <p className={styles.emptyState}>
                      None detected. Runtime/bootstrap is not blocking room start.
                    </p>
                  ) : (
                    <ul className={styles.checklist}>
                      {(roomStartContract?.system_blockers || []).map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <p className={styles.listTitle}>Contextual questions</p>
                  {roomStartContract &&
                  roomStartContract.contextual_open_questions.length === 0 ? (
                    <p className={styles.emptyState}>
                      No contextual open questions were surfaced during planning.
                    </p>
                  ) : (
                    <ul className={styles.checklist}>
                      {(roomStartContract?.contextual_open_questions || []).map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
              {roomStartContract && roomStartContract.known_context.length > 0 ? (
                <div>
                  <p className={styles.listTitle}>Known context</p>
                  <ul className={styles.checklist}>
                    {roomStartContract.known_context.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {preflightReport.candidate_specialist_roster.length > 0 ? (
                <div>
                  <p className={styles.listTitle}>Planned specialists</p>
                  <div className={styles.specialistList}>
                    {preflightReport.candidate_specialist_roster.map((specialist) => (
                      <article className={styles.specialistCard} key={specialist.role}>
                        <p className={styles.specialistName}>
                          {specialist.display_name}
                        </p>
                        <p className={styles.specialistMeta}>
                          {formatToken(specialist.role)} · {specialist.capability_profile}
                        </p>
                        <p className={styles.specialistReason}>
                          {specialist.join_reason}
                        </p>
                      </article>
                    ))}
                  </div>
                </div>
              ) : null}
            </section>
          ) : null}
        </form>

        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <h3>Recent Rooms</h3>
            <span className={styles.badgeMuted}>{rooms.length}</span>
          </div>
          <div className={styles.roomList}>
            {rooms.length === 0 ? (
              <p className={styles.emptyState}>
                No rooms yet. Create the first one to start the runtime.
              </p>
            ) : (
              rooms.map((room) => (
                <button
                  key={room.room_id}
                  className={styles.roomCard}
                  onClick={() => navigate(`/rooms/${room.room_id}`)}
                  type="button"
                >
                  <div>
                    <p className={styles.roomTopic}>{room.topic}</p>
                    <p className={styles.roomMeta}>
                      {room.status === "ended"
                        ? `${formatToken(room.conclusion_type || room.status)} · closed`
                        : `${room.phase} · round ${room.round_index} · ${room.status}`}{" "}
                      · {room.brief_source}
                    </p>
                  </div>
                  <span className={styles.roomDecision}>
                    {room.candidate_decision || room.current_focus || room.requirement}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
