import { useLoaderData, Link } from "react-router";
import { useQuery } from "@tanstack/react-query";

import { getRoomSnapshot } from "../lib/api";
import {
  buildRoleDirectory,
  formatToken,
  readProductOperatorContractSections,
  readOperatorContextValue,
  readPlannedSpecialists,
  readPreflightBoolean,
  readPreflightList,
  readPreflightValue,
  readRuntimeExecutorTargets,
  readRuntimePlannerTarget,
} from "../lib/planning-artifacts";
import type { RoomSnapshot } from "../lib/types";
import styles from "./results.module.css";

export function ResultsPage() {
  const initialSnapshot = useLoaderData() as RoomSnapshot;
  const roomQuery = useQuery({
    queryKey: ["room", initialSnapshot.room_id, "results"],
    queryFn: () => getRoomSnapshot(initialSnapshot.room_id),
    initialData: initialSnapshot,
    refetchInterval: (query) =>
      (query.state.data as RoomSnapshot | undefined)?.status === "ended" ? false : 2500,
  });
  const snapshot = roomQuery.data;
  const plannedSpecialists = readPlannedSpecialists(snapshot);
  const roleDirectory = buildRoleDirectory(snapshot, plannedSpecialists);
  const conclusionType = formatToken(snapshot.conclusion_type || "pending");
  const conclusionReason =
    snapshot.conclusion_reason || snapshot.ended_reason || snapshot.consensus.reason;
  const entryScope = readOperatorContextValue(snapshot, "entry_scope");
  const preflightReady = readPreflightBoolean(snapshot, "room_start_ready");
  const recommendedSurface = readPreflightValue(snapshot, "recommended_surface");
  const plannerTarget = readRuntimePlannerTarget(snapshot);
  const executorTargets = readRuntimeExecutorTargets(snapshot);
  const contractSections = readProductOperatorContractSections(snapshot);
  const missingOperatorInputs = readPreflightList(snapshot, "missing_operator_inputs");

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <p className={styles.kicker}>Meeting result · {conclusionType}</p>
        <h2>{snapshot.topic}</h2>
        <p>{conclusionReason || "The meeting has not published an explicit conclusion yet."}</p>
        <p className={styles.requirement}>
          Requirement: {snapshot.requirement || "No original requirement recorded."}
        </p>
      </header>

      <section className={styles.resultGrid}>
        <article className={styles.card}>
          <h3>Conclusion Contract</h3>
          <p className={styles.decision}>{conclusionType}</p>
          <p className={styles.metaCopy}>
            {conclusionReason || "No explicit conclusion reason recorded yet."}
          </p>
        </article>

        <article className={styles.card}>
          <h3>Decision Candidate</h3>
          <p className={styles.decision}>
            {snapshot.candidate_decision || "No candidate decision recorded yet."}
          </p>
        </article>

        <article className={styles.card}>
          <h3>Action Items</h3>
          <ul className={styles.list}>
            {snapshot.action_items.length === 0 ? (
              <li>No action items yet.</li>
            ) : (
              snapshot.action_items.map((item) => <li key={item}>{item}</li>)
            )}
          </ul>
        </article>

        <article className={styles.card}>
          <h3>Open Questions</h3>
          <ul className={styles.list}>
            {snapshot.open_questions.length === 0 ? (
              <li>No open questions remain.</li>
            ) : (
              snapshot.open_questions.map((item) => <li key={item}>{item}</li>)
            )}
          </ul>
        </article>

        <article className={styles.card}>
          <h3>Pre-room Contract</h3>
          {entryScope ? (
            <p className={styles.metaCopy}>
              Entry scope: {formatToken(entryScope)}
            </p>
          ) : null}
          {preflightReady !== null ? (
            <p className={styles.metaCopy}>
              Room start gate:{" "}
              {preflightReady
                ? "passed before room creation."
                : "room opened without clearing the room-start gate."}
            </p>
          ) : null}
          {recommendedSurface ? (
            <p className={styles.metaCopy}>
              Recommended surface: {formatToken(recommendedSurface)}
            </p>
          ) : null}
          {plannerTarget ? (
            <p className={styles.metaCopy}>Planner target: {plannerTarget}</p>
          ) : null}
          {executorTargets ? (
            <p className={styles.metaCopy}>Executor targets: {executorTargets}</p>
          ) : null}
          {contractSections.length === 0 ? (
            <p className={styles.metaCopy}>
              No explicit entry-scope contract was persisted for this room.
            </p>
          ) : (
            <div className={styles.contractSections}>
              {contractSections.map((section) => (
                <div className={styles.contractSection} key={section.title}>
                  <p className={styles.sectionLabel}>{section.title}</p>
                  <ul className={styles.list}>
                    {section.items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
          {missingOperatorInputs.length > 0 ? (
            <div className={styles.contractSection}>
              <p className={styles.sectionLabel}>Missing operator inputs</p>
              <ul className={styles.list}>
                {missingOperatorInputs.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </article>
      </section>

      <div className={styles.timeline}>
        <div className={styles.timelineHeader}>
          <h3>Replay Timeline</h3>
          <Link className={styles.link} to={`/rooms/${snapshot.room_id}`}>
            Return to live room
          </Link>
        </div>
        {snapshot.transcript.map((entry) => (
          <article className={styles.timelineEntry} key={`${entry.seq}-${entry.message_id}`}>
            <div className={styles.timelineMeta}>
              <span>
                {roleDirectory[entry.role.trim().toLowerCase()] || formatToken(entry.role)}
              </span>
              <span>{entry.event_type}</span>
            </div>
            {entry.title ? <h4>{entry.title}</h4> : null}
            <p>{entry.text}</p>
          </article>
        ))}
      </div>
    </div>
  );
}
