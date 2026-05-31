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
import decisionScribeAvatar from "../assets/agents/decision-scribe-codex.png";
import operationsSpecialistAvatar from "../assets/agents/operations-specialist-codex.png";
import productStrategistAvatar from "../assets/agents/product-strategist-codex.png";
import riskControllerAvatar from "../assets/agents/risk-controller-codex.png";
import supervisorAvatar from "../assets/agents/supervisor-codex.png";
import systemsArchitectAvatar from "../assets/agents/systems-architect-codex.png";
import {
  buildRoleDirectory,
  formatToken,
  readLatestCentralMasState,
  readProductOperatorContractSections,
  readOperatorContextValue,
  readPlanningText,
  readPlannedSpecialists,
  readPreflightBoolean,
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
  host: "主持人",
  human: "你（操作员）",
  system: "系统",
  synthesis: "决策记录员",
  pro: "正方顾问",
  con: "反方顾问",
  recorder: "记录员",
  implementation_specialist: "系统架构师",
  product_specialist: "产品策略师",
  risk_specialist: "风险控制师",
  operations_specialist: "运营观察员",
};

const roleAvatarMap: Record<string, string> = {
  host: supervisorAvatar,
  supervisor: supervisorAvatar,
  pro: productStrategistAvatar,
  con: riskControllerAvatar,
  recorder: decisionScribeAvatar,
  implementation_specialist: systemsArchitectAvatar,
  systems_architect: systemsArchitectAvatar,
  product_specialist: productStrategistAvatar,
  product_strategist: productStrategistAvatar,
  risk_specialist: riskControllerAvatar,
  risk_controller: riskControllerAvatar,
  operations_specialist: operationsSpecialistAvatar,
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
    label: roleLabelMap[normalized] || formatToken(normalized) || "未知角色",
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
  const [overrideText, setOverrideText] = useState("立即停止讨论并锁定当前候选决策。");
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
  const conclusionType = formatToken(snapshot.conclusion_type || "pending（进行中）");
  const conclusionReason =
    snapshot.conclusion_reason || snapshot.ended_reason || snapshot.consensus.reason;
  const entryScope = readOperatorContextValue(snapshot, "entry_scope");
  const preflightReady = readPreflightBoolean(snapshot, "room_start_ready");
  const recommendedSurface = readPreflightValue(snapshot, "recommended_surface");
  const plannerTarget = readRuntimePlannerTarget(snapshot);
  const executorTargets = readRuntimeExecutorTargets(snapshot);
  const operatorContractSections = readProductOperatorContractSections(snapshot);
  const centralMas = readLatestCentralMasState(snapshot);
  const speakers = centralMas?.speakers ?? [];
  const centralTopology = centralMas?.topology ?? "";
  const recommendedNextPhase = String(snapshot.recommended_next_phase ?? "").trim();
  const recommendedNextAction = String(snapshot.recommended_next_action ?? "").trim();

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
    ? snapshot.ended_reason || "本会议室已结束，不再接受新的输入。"
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
            状态 {snapshot.status} · 阶段 {snapshot.phase} · 第 {snapshot.round_index} 轮 · 简报来源 {snapshot.brief_source}
          </p>
          <h2 className={styles.topic}>{snapshot.topic}</h2>
          <p className={styles.goal}>{snapshot.goal || snapshot.current_focus}</p>
          <p className={styles.requirement}>
            原始需求：{snapshot.requirement || "未记录原始需求。"}
          </p>
        </div>
        <div className={styles.connection}>
          <span className={styles.connectionLabel}>实时传输</span>
          <strong>{connectionState}</strong>
        </div>
      </header>

      <div className={styles.grid}>
        <aside className={styles.sidePanel}>
          {snapshot.brief_source !== "agent" ? (
            <section className={styles.warningCard}>
              <h3>规划器状态</h3>
              <p className={styles.warningCopy}>
                本会议没有走通真 LLM 规划器，当前简报是 fallback。
              </p>
              <p className={styles.warningReason}>
                {snapshot.brief_source_reason || "未记录规划器失败原因。"}
              </p>
            </section>
          ) : null}

          <section className={styles.panelCard}>
            <h3>会议简报</h3>
            <span className={styles.sectionLabel}>目标</span>
            <p className={styles.focus}>{planningObjective || "等待规划器输出目标…"}</p>
            <span className={styles.sectionLabel}>当前焦点</span>
            <p className={styles.secondaryText}>
              {snapshot.current_focus || planningInitialFocus || "等待主持人选定焦点…"}
            </p>
            <ul className={styles.constraintList}>
              {snapshot.constraints.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            {snapshot.open_questions.length > 0 ? (
              <div className={styles.openQuestions}>
                <h4>开放问题（智能体会在会中追问）</h4>
                <ul className={styles.questionList}>
                  {snapshot.open_questions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <section className={styles.panelCard}>
            <h3>会前契约</h3>
            <div className={styles.contractMeta}>
              {entryScope ? (
                <div>
                  <span className={styles.sectionLabel}>入口范围</span>
                  <p className={styles.secondaryText}>{formatToken(entryScope)}</p>
                </div>
              ) : null}
              {preflightReady !== null ? (
                <div>
                  <span className={styles.sectionLabel}>开会前检查</span>
                  <p className={styles.secondaryText}>
                    {preflightReady
                      ? "已通过 preflight。"
                      : "未通过 preflight 直接开会（advisory 模式）。"}
                  </p>
                </div>
              ) : null}
              {recommendedSurface ? (
                <div>
                  <span className={styles.sectionLabel}>建议入口</span>
                  <p className={styles.secondaryText}>
                    {formatToken(recommendedSurface)}
                  </p>
                </div>
              ) : null}
              {plannerTarget ? (
                <div>
                  <span className={styles.sectionLabel}>规划器路由</span>
                  <p className={styles.secondaryText}>{plannerTarget}</p>
                </div>
              ) : null}
              {executorTargets ? (
                <div>
                  <span className={styles.sectionLabel}>执行器路由</span>
                  <p className={styles.secondaryText}>{executorTargets}</p>
                </div>
              ) : null}
            </div>
            {operatorContractSections.length === 0 ? (
              <p className={styles.reason}>
                本会议未记录显式的入口契约。
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
          </section>

          <section className={styles.panelCard}>
            <h3>中心化 MAS</h3>
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
            {recommendedNextPhase || recommendedNextAction ? (
              <div className={styles.assignmentList}>
                <p className={styles.sectionLabel}>下一轮 LLM 建议</p>
                {recommendedNextPhase ? (
                  <p className={styles.secondaryText}>
                    阶段建议：{formatToken(recommendedNextPhase)}
                  </p>
                ) : null}
                {recommendedNextAction ? (
                  <p className={styles.secondaryText}>
                    动作建议：{formatToken(recommendedNextAction)}
                  </p>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className={styles.panelCard}>
            <h3>本轮发言安排</h3>
            {snapshot.current_turns.length === 0 ? (
              <p className={styles.reason}>
                主持人尚未发布结构化的发言计划。
              </p>
            ) : (
              <div className={styles.turnList}>
                {snapshot.current_turns.map((turn) => (
                  <div className={styles.turnItem} key={`${turn.role}-${turn.task}`}>
                    <div className={styles.turnRole}>
                      {resolveRoleLabel(turn.role, roleDirectory)}
                    </div>
                    <p className={styles.turnTask}>{turn.task || "由该角色契约自主决定内容"}</p>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className={styles.panelCard}>
            <h3>候选角色智能体</h3>
            {plannedSpecialists.length === 0 ? (
              <p className={styles.reason}>
                本会议快照里没有预登记的角色名册。
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
                        关注：{specialist.focusAreas.join(" · ")}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className={styles.panelCard}>
            <h3>参与者</h3>
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
                    {participant.activation || (participant.speaking ? "发言中" : "已加入")}
                  </span>
                </div>
              ))}
            </div>
          </section>
        </aside>

        <section className={styles.transcriptPanel}>
          <div className={styles.panelHeader}>
            <h3>实时讨论</h3>
            {snapshot.status === "ended" ? (
              <button
                className={styles.secondaryButton}
                onClick={() => navigate(`/rooms/${snapshot.room_id}/results`)}
                type="button"
              >
                查看决策结果
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
                    ··
                  </span>
                  <div className={styles.messageIdentity}>
                    <div className={styles.messageTitleRow}>
                      <strong>正在输出…</strong>
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
            <h3>结论契约</h3>
            <p className={styles.focus}>{conclusionType}</p>
            <p className={styles.reason}>
              {conclusionReason || "会议尚未给出最终结论契约。"}
            </p>
          </section>

          <section className={styles.panelCard}>
            <h3>收敛信号</h3>
            <dl className={styles.metricGrid}>
              <div>
                <dt>综合分</dt>
                <dd>{snapshot.consensus.score.toFixed(2)}</dd>
              </div>
              <div>
                <dt>支持度</dt>
                <dd>{snapshot.consensus.support.toFixed(2)}</dd>
              </div>
              <div>
                <dt>信心</dt>
                <dd>{snapshot.consensus.confidence.toFixed(2)}</dd>
              </div>
              <div>
                <dt>分歧度</dt>
                <dd>{snapshot.consensus.disagreement_index.toFixed(2)}</dd>
              </div>
            </dl>
            <p className={styles.reason}>{snapshot.consensus.reason || "等待收敛检测…"}</p>
          </section>

          <section className={styles.panelCard}>
            <h3>人类介入</h3>
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
              <span>对会议室说</span>
              <textarea
                disabled={roomEnded || humanMessageMutation.isPending}
                rows={4}
                value={humanMessage}
                onChange={(event) => {
                  humanMessageMutation.reset();
                  setHumanMessage(event.target.value);
                }}
                placeholder="补充上下文、回答智能体的提问、追加约束条件…"
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
              {humanMessageMutation.isPending ? "发送中…" : "发送给会议室"}
            </button>

            <label className={styles.field}>
              <span>强制结束理由</span>
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
              {humanOverrideMutation.isPending ? "正在结束…" : "强制结束会议"}
            </button>
          </section>
        </aside>
      </div>
    </div>
  );
}
