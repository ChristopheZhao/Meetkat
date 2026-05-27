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
    return "主规划器就绪";
  }
  if (readiness.fallback_planner_ready) {
    return "仅 fallback 规划器";
  }
  return "规划器不可用";
}

function executorStatus(readiness: RuntimeReadiness): string {
  return readiness.executor_ready ? "执行器就绪" : "执行器不可用";
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
    "Should we adopt a centralized MAS architecture for an online engineering decision room that can discuss deep delivery tradeoffs, expose agent assignments, preserve real-time communication, and keep governance boundaries clear?",
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

  const handlePreviewBrief = async () => {
    createMutation.reset();
    preflightMutation.reset();
    try {
      await preflightMutation.mutateAsync({
        requirement,
        allow_planner_fallback: false,
        entry_scope: HOME_ENTRY_SCOPE,
      });
    } catch {
      // Surface errors through the mutation state.
    }
  };

  const handleCreateRoom = async (event: FormEvent<HTMLFormElement>) => {
    // Open Room always opens. Clarifications happen inside the meeting via
    // the supervisor / specialist agents asking the operator naturally
    // through the room human-message channel — not as pre-room slot filling.
    event.preventDefault();
    createMutation.reset();
    try {
      await createMutation.mutateAsync({
        requirement,
        mode: "agent_first",
        require_preflight_ready: false,
        allow_planner_fallback: true,
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
  const planningRolePlannerDegraded = Boolean(
    preflightReport?.runtime_readiness?.role_planner_degraded,
  );
  const planningRolePlannerKind = String(
    preflightReport?.runtime_readiness?.role_planner_kind ?? "",
  ).trim();

  return (
    <div className={styles.layout}>
      <section className={styles.hero}>
        <p className={styles.kicker}>中心化 Supervisor · 子智能体自主</p>
        <h2 className={styles.heading}>
          实时打开一个有主持人 + 角色智能体 + 可重放通信的决策会议室
        </h2>
        <p className={styles.copy}>
          输入一个深度决策问题。中心化主持人负责选角与发言顺序；每个角色智能体自主决定要说什么、给出哪些证据、用多大信心；
          短期记忆贯穿全会，长期 lessons 跨会议累积。会议结束输出一份显式的决策记录。
        </p>
        {planningRolePlannerDegraded ? (
          <p className={styles.warningBanner}>
            ⚠ 当前角色选取走的是关键词匹配兜底（role_planner_kind=
            {planningRolePlannerKind || "heuristic"}）。配置 MODEL_DEFAULT_SUPPLIER /
            MODEL_DEFAULT_MODEL 后会自动切到 LLM 选角。
          </p>
        ) : null}
      </section>

      <section className={styles.grid}>
        <form className={styles.card} onSubmit={handleCreateRoom}>
          <div className={styles.cardHeader}>
            <h3>新建会议室</h3>
            <span className={styles.badge}>Central MAS</span>
          </div>
          <label className={styles.field}>
            <span>讨论问题</span>
            <textarea
              rows={5}
              value={requirement}
              onChange={(event) => handleRequirementChange(event.target.value)}
              placeholder="描述需要决策的问题、期望产出、以及任何已知的硬性边界。"
            />
          </label>
          <p className={styles.helper}>
            「打开会议室」会直接创建并开会。主持人和角色智能体如果需要更多上下文，会通过会议室的人类消息通道直接问你 ——
            不会用前置表单卡住你。「预览简报」是可选的，只展示规划器在开会前看到的视角，不会阻断开会。
          </p>
          <div className={styles.buttonRow}>
            <button
              className={styles.secondaryButton}
              type="button"
              onClick={() => void handlePreviewBrief()}
              disabled={isSubmitting}
            >
              {preflightMutation.isPending ? "正在预览…" : "预览简报"}
            </button>
            <button
              className={styles.primaryButton}
              type="submit"
              disabled={isSubmitting}
            >
              {createMutation.isPending ? "正在创建…" : "打开会议室"}
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
                  <p className={styles.preflightKicker}>简报预览</p>
                  <h4>规划器看到的视角</h4>
                  </div>
                <span className={styles.preflightReady}>
                  仅供预览 · 「打开会议室」不会被这里的任何提示卡住，智能体会在会中主动澄清
                </span>
              </div>
              <p className={styles.preflightSummary}>
                {roomStartContract?.root_cause_hypothesis ||
                  "未检测到任何外部依赖阻塞，可放心开会。"}
              </p>
              <dl className={styles.preflightMeta}>
                <div>
                  <dt>会议目标</dt>
                  <dd>{preflightReport.meeting_objective}</dd>
                </div>
                <div>
                  <dt>首轮焦点</dt>
                  <dd>{preflightReport.initial_focus}</dd>
                </div>
                <div>
                  <dt>建议入口</dt>
                  <dd>{formatToken(roomStartContract?.recommended_surface || "")}</dd>
                </div>
                {entryScope ? (
                  <div>
                    <dt>入口范围</dt>
                    <dd>{formatToken(entryScope)}</dd>
                  </div>
                ) : null}
                <div>
                  <dt>运行时就绪</dt>
                  <dd>
                    {plannerStatus(preflightReport.runtime_readiness)} ·{" "}
                    {executorStatus(preflightReport.runtime_readiness)}
                  </dd>
                </div>
                {plannerTarget ? (
                  <div>
                    <dt>规划器路由</dt>
                    <dd>{plannerTarget}</dd>
                  </div>
                ) : null}
                {executorTargets ? (
                  <div>
                    <dt>执行器路由</dt>
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
              <div className={styles.preflightLists}>
                <div>
                  <p className={styles.listTitle}>系统阻塞项</p>
                  {roomStartContract && roomStartContract.system_blockers.length === 0 ? (
                    <p className={styles.emptyState}>
                      未检测到任何运行时 / 基础设施阻塞。
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
                  <p className={styles.listTitle}>开放问题（智能体会在会中追问）</p>
                  {roomStartContract &&
                  roomStartContract.contextual_open_questions.length === 0 ? (
                    <p className={styles.emptyState}>
                      规划阶段没有发现需要进一步澄清的问题。
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
                  <p className={styles.listTitle}>已知上下文</p>
                  <ul className={styles.checklist}>
                    {roomStartContract.known_context.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {preflightReport.candidate_specialist_roster.length > 0 ? (
                <div>
                  <p className={styles.listTitle}>候选角色智能体</p>
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
            <h3>最近的会议室</h3>
            <span className={styles.badgeMuted}>{rooms.length}</span>
          </div>
          <div className={styles.roomList}>
            {rooms.length === 0 ? (
              <p className={styles.emptyState}>
                还没有会议室，创建第一个就开始运行了。
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
                        ? `${formatToken(room.conclusion_type || room.status)} · 已结束`
                        : `${room.phase} · 第 ${room.round_index} 轮 · ${room.status}`}{" "}
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
