import { useLoaderData, Link } from "react-router";
import { useQuery } from "@tanstack/react-query";

import { getRoomSnapshot } from "../lib/api";
import {
  buildDecisionRecordFilename,
  buildDecisionRecordMarkdown,
} from "../lib/decision-record";
import {
  buildRoleDirectory,
  formatToken,
  readProductOperatorContractSections,
  readOperatorContextValue,
  readPlannedSpecialists,
  readPreflightBoolean,
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
  const conclusionType = formatToken(snapshot.conclusion_type || "pending（进行中）");
  const conclusionReason =
    snapshot.conclusion_reason || snapshot.ended_reason || snapshot.consensus.reason;
  const entryScope = readOperatorContextValue(snapshot, "entry_scope");
  const preflightReady = readPreflightBoolean(snapshot, "room_start_ready");
  const recommendedSurface = readPreflightValue(snapshot, "recommended_surface");
  const plannerTarget = readRuntimePlannerTarget(snapshot);
  const executorTargets = readRuntimeExecutorTargets(snapshot);
  const contractSections = readProductOperatorContractSections(snapshot);

  const handleDownloadMarkdown = () => {
    const markdown = buildDecisionRecordMarkdown(snapshot, roleDirectory);
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = buildDecisionRecordFilename(snapshot);
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerTop}>
          <p className={styles.kicker}>会议决策结果 · {conclusionType}</p>
          <button
            className={styles.downloadButton}
            onClick={handleDownloadMarkdown}
            type="button"
          >
            下载 Markdown
          </button>
        </div>
        <h2>{snapshot.topic}</h2>
        <p>{conclusionReason || "会议尚未发布显式结论。"}</p>
        <p className={styles.requirement}>
          原始需求：{snapshot.requirement || "未记录原始需求。"}
        </p>
      </header>

      <section className={styles.resultGrid}>
        <article className={styles.card}>
          <h3>结论契约</h3>
          <p className={styles.decision}>{conclusionType}</p>
          <p className={styles.metaCopy}>
            {conclusionReason || "尚未记录显式结论原因。"}
          </p>
        </article>

        <article className={styles.card}>
          <h3>决策候选</h3>
          <p className={styles.decision}>
            {snapshot.candidate_decision || "尚无候选决策。"}
          </p>
        </article>

        <article className={styles.card}>
          <h3>行动项</h3>
          <ul className={styles.list}>
            {snapshot.action_items.length === 0 ? (
              <li>尚无行动项。</li>
            ) : (
              snapshot.action_items.map((item) => <li key={item}>{item}</li>)
            )}
          </ul>
        </article>

        <article className={styles.card}>
          <h3>开放问题</h3>
          <ul className={styles.list}>
            {snapshot.open_questions.length === 0 ? (
              <li>所有开放问题都已闭环。</li>
            ) : (
              snapshot.open_questions.map((item) => <li key={item}>{item}</li>)
            )}
          </ul>
        </article>

        <article className={styles.card}>
          <h3>会前契约</h3>
          {entryScope ? (
            <p className={styles.metaCopy}>
              入口范围：{formatToken(entryScope)}
            </p>
          ) : null}
          {preflightReady !== null ? (
            <p className={styles.metaCopy}>
              开会前检查：
              {preflightReady
                ? "已通过 preflight。"
                : "未通过 preflight 直接开会（advisory 模式）。"}
            </p>
          ) : null}
          {recommendedSurface ? (
            <p className={styles.metaCopy}>
              建议入口：{formatToken(recommendedSurface)}
            </p>
          ) : null}
          {plannerTarget ? (
            <p className={styles.metaCopy}>规划器路由：{plannerTarget}</p>
          ) : null}
          {executorTargets ? (
            <p className={styles.metaCopy}>执行器路由：{executorTargets}</p>
          ) : null}
          {contractSections.length === 0 ? (
            <p className={styles.metaCopy}>
              本会议未记录显式的入口契约。
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
        </article>
      </section>

      <div className={styles.timeline}>
        <div className={styles.timelineHeader}>
          <h3>事件流回放</h3>
          <Link className={styles.link} to={`/rooms/${snapshot.room_id}`}>
            回到实时房间
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
