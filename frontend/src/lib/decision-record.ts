import type { RoomSnapshot, TranscriptEntry } from "./types";

function quoteBlock(text: string): string {
  return text
    .split(/\r?\n/)
    .map((line) => (line ? `> ${line}` : ">"))
    .join("\n");
}

function listItems(items: string[], fallback: string): string {
  if (items.length === 0) {
    return `- ${fallback}`;
  }
  return items.map((item) => `- ${item}`).join("\n");
}

function checkboxItems(items: string[], fallback: string): string {
  if (items.length === 0) {
    return `- [ ] ${fallback}`;
  }
  return items.map((item) => `- [ ] ${item}`).join("\n");
}

function resolveRoleLabel(
  role: string,
  roleDirectory: Record<string, string>,
): string {
  const normalized = role.trim().toLowerCase();
  return roleDirectory[normalized] || normalized || "未知角色";
}

function collectRoleClaims(
  transcript: TranscriptEntry[],
  roleDirectory: Record<string, string>,
): string {
  const latestByRole = new Map<string, TranscriptEntry>();
  for (const entry of transcript) {
    const role = entry.role.trim().toLowerCase();
    if (
      !role ||
      entry.event_type !== "agent.message" ||
      ["host", "supervisor", "synthesis", "system", "human"].includes(role)
    ) {
      continue;
    }
    const claim = String(entry.artifacts.claim ?? "").trim();
    if (claim) {
      latestByRole.set(role, entry);
    }
  }
  if (latestByRole.size === 0) {
    return "- 尚未形成可导出的角色主张。";
  }

  return [...latestByRole.entries()]
    .map(([role, entry]) => {
      const claim = String(entry.artifacts.claim ?? "").trim();
      const confidence = Number(entry.artifacts.confidence);
      const evidenceRaw = entry.artifacts.evidence;
      const evidence = Array.isArray(evidenceRaw)
        ? evidenceRaw.map((item) => String(item).trim()).filter(Boolean)
        : [];
      const lines = [
        `### ${resolveRoleLabel(role, roleDirectory)} · \`${role}\``,
        "",
        `- **核心主张**：${claim}`,
      ];
      if (Number.isFinite(confidence) && confidence > 0) {
        lines.push(`- **信心**：${confidence.toFixed(2)}`);
      }
      if (evidence.length > 0) {
        lines.push("- **证据**：");
        for (const piece of evidence) {
          lines.push(`  - ${piece}`);
        }
      }
      return lines.join("\n");
    })
    .join("\n\n");
}

function transcriptMarkdown(
  transcript: TranscriptEntry[],
  roleDirectory: Record<string, string>,
): string {
  const entries = transcript.filter((entry) =>
    ["agent.message", "human.message", "human.override"].includes(entry.event_type),
  );
  if (entries.length === 0) {
    return "_尚无可导出的对话记录。_";
  }
  return entries
    .map((entry) => {
      const role = entry.role.trim().toLowerCase();
      const title = entry.title ? ` · ${entry.title}` : "";
      const lines = [
        `### ${resolveRoleLabel(role, roleDirectory)}${title}`,
        "",
        entry.text || "（空消息）",
      ];
      const claim = String(entry.artifacts.claim ?? "").trim();
      if (claim) {
        lines.push("", `_主张：${claim}_`);
      }
      return lines.join("\n");
    })
    .join("\n\n---\n\n");
}

export function buildDecisionRecordMarkdown(
  snapshot: RoomSnapshot,
  roleDirectory: Record<string, string>,
): string {
  const conclusionReason =
    snapshot.conclusion_reason || snapshot.ended_reason || snapshot.consensus.reason;
  const generatedAt = new Date().toISOString();
  const sections = [
    `# 决策会议记录 · ${snapshot.topic || "未命名讨论"}`,
    [
      `- 会议 ID：\`${snapshot.room_id}\``,
      `- 状态：${snapshot.status}（第 ${snapshot.round_index} 轮）`,
      `- 决策类型：${snapshot.conclusion_type || "未给出"}`,
      `- 生成时间：${generatedAt}`,
      `- 最后事件序号：${snapshot.last_seq}`,
    ].join("\n"),
    `## 原始需求\n\n${quoteBlock(snapshot.requirement || "未记录原始需求。")}`,
    `## 会议目标\n\n${snapshot.goal || snapshot.current_focus || "未记录会议目标。"}`,
    [
      "## 决策结论",
      "",
      `**候选决策**：${snapshot.candidate_decision || "本会议尚未形成候选决策。"}`,
      "",
      `**结论原因**：${conclusionReason || "尚未记录显式结论原因。"}`,
    ].join("\n"),
    `## 行动项\n\n${checkboxItems(snapshot.action_items, "尚无行动项。")}`,
    `## 开放问题\n\n${listItems(snapshot.open_questions, "所有开放问题都已闭环。")}`,
    `## 各角色立场摘要\n\n${collectRoleClaims(snapshot.transcript, roleDirectory)}`,
    `## 完整对话记录\n\n${transcriptMarkdown(snapshot.transcript, roleDirectory)}`,
    [
      "_本文件由前端结果页从当前 RoomSnapshot 投影生成。",
      "权威数据源仍是 RoomEventJournal / RoomSnapshot；如需包含短期记忆与长期 lesson，请使用后端导出脚本。_",
    ].join("\n"),
  ];
  return `${sections.join("\n\n")}\n`;
}

export function buildDecisionRecordFilename(snapshot: RoomSnapshot): string {
  const topicSlug =
    snapshot.topic
      .trim()
      .toLowerCase()
      .replace(/[`~!@#$%^&*()+=[\]{};:'"\\|,.<>/?\s]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80) || snapshot.room_id;
  return `${topicSlug}-decision-record.md`;
}
