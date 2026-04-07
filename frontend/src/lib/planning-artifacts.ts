import type { RoomSnapshot } from "./types";

export type PlanningSpecialistView = {
  role: string;
  displayName: string;
  capabilityProfile: string;
  joinReason: string;
  focusAreas: string[];
};

export type PlanningSectionView = {
  title: string;
  items: string[];
};

type PlanningObject = Record<string, unknown>;

const PRODUCT_OPERATOR_CONTRACT_SECTIONS: Array<{
  title: string;
  key: string;
}> = [
  { title: "Entry contract", key: "entry_contract" },
  { title: "Auto-resolved context", key: "auto_resolved_context" },
  { title: "Operator-required inputs", key: "operator_required_inputs" },
  { title: "Human control surface", key: "human_control_contract" },
];

export function formatToken(value: string): string {
  return value
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

export function readPlanningText(
  snapshot: RoomSnapshot,
  key: string,
  fallback = "",
): string {
  const value = snapshot.planning_artifacts[key];
  return typeof value === "string" && value.trim() ? value : fallback;
}

export function readPlannedSpecialists(
  snapshot: RoomSnapshot,
): PlanningSpecialistView[] {
  const raw = snapshot.planning_artifacts.candidate_specialist_roster;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const payload = item as PlanningObject;
    const role = String(payload.role ?? "").trim();
    if (!role) {
      return [];
    }
    return [
      {
        role,
        displayName: String(payload.display_name ?? formatToken(role)),
        capabilityProfile: String(payload.capability_profile ?? ""),
        joinReason: String(payload.join_reason ?? ""),
        focusAreas: readStringArray(payload.focus_areas),
      },
    ];
  });
}

export function buildRoleDirectory(
  snapshot: RoomSnapshot,
  plannedSpecialists: PlanningSpecialistView[],
): Record<string, string> {
  const directory: Record<string, string> = {};
  for (const participant of snapshot.participants) {
    const role = participant.role.trim().toLowerCase();
    const displayName = participant.display_name.trim();
    if (role && displayName) {
      directory[role] = displayName;
    }
  }
  for (const specialist of plannedSpecialists) {
    const role = specialist.role.trim().toLowerCase();
    const displayName = specialist.displayName.trim();
    if (role && displayName && !directory[role]) {
      directory[role] = displayName;
    }
  }
  return directory;
}

export function readOperatorContextValue(
  snapshot: RoomSnapshot,
  key: string,
): string {
  return readContextString(snapshot, "operator_context", key);
}

export function readOperatorContextList(
  snapshot: RoomSnapshot,
  key: string,
): string[] {
  return readContextList(snapshot, "operator_context", key);
}

export function readProductOperatorContractSectionsFromContext(
  operatorContext: unknown,
): PlanningSectionView[] {
  if (!operatorContext || typeof operatorContext !== "object" || Array.isArray(operatorContext)) {
    return [];
  }
  const payload = operatorContext as PlanningObject;
  return PRODUCT_OPERATOR_CONTRACT_SECTIONS
    .map((section) => ({
      title: section.title,
      items: readStringArray(payload[section.key]),
    }))
    .filter((section) => section.items.length > 0);
}

export function readProductOperatorContractSections(
  snapshot: RoomSnapshot,
): PlanningSectionView[] {
  return readProductOperatorContractSectionsFromContext(
    readPlanningObject(snapshot, "operator_context"),
  );
}

export function readPreflightValue(
  snapshot: RoomSnapshot,
  key: string,
): string {
  return readRoomStartContractString(snapshot, key);
}

export function readPreflightList(
  snapshot: RoomSnapshot,
  key: string,
): string[] {
  return readRoomStartContractList(snapshot, key);
}

export function readPreflightBoolean(
  snapshot: RoomSnapshot,
  key: string,
): boolean | null {
  const value = readRoomStartContractObject(snapshot)[key];
  return typeof value === "boolean" ? value : null;
}

export function readRuntimePlannerTarget(snapshot: RoomSnapshot): string {
  return formatTargetIdentity(readPlanningObject(snapshot, "runtime_context").planner_target);
}

export function readRuntimeExecutorTargets(snapshot: RoomSnapshot): string {
  return formatExecutorTargets(readPlanningObject(snapshot, "runtime_context").executor_targets);
}

export function formatTargetIdentity(target: unknown): string {
  if (!target || typeof target !== "object" || Array.isArray(target)) {
    return "";
  }
  const payload = target as PlanningObject;
  const supplier = String(payload.supplier ?? "").trim();
  const model = String(payload.model ?? "").trim();
  if (supplier && model) {
    return `${supplier}/${model}`;
  }
  return supplier || model;
}

export function formatExecutorTargets(targets: unknown): string {
  if (!targets || typeof targets !== "object" || Array.isArray(targets)) {
    return "";
  }
  const payload = targets as PlanningObject;
  const entries = [
    ["default", formatTargetIdentity(payload.default)],
    ["escalation", formatTargetIdentity(payload.escalation)],
    ["fallback", formatTargetIdentity(payload.fallback)],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));
  return entries
    .map(([label, value]) => `${formatToken(label)} ${value}`)
    .join(" · ");
}

function readContextString(
  snapshot: RoomSnapshot,
  contextKey: string,
  key: string,
): string {
  const value = readPlanningObject(snapshot, contextKey)[key];
  return typeof value === "string" ? value.trim() : "";
}

function readContextList(
  snapshot: RoomSnapshot,
  contextKey: string,
  key: string,
): string[] {
  return readStringArray(readPlanningObject(snapshot, contextKey)[key]);
}

function readRoomStartContractString(snapshot: RoomSnapshot, key: string): string {
  const value = readRoomStartContractObject(snapshot)[key];
  return typeof value === "string" ? value.trim() : "";
}

function readRoomStartContractList(snapshot: RoomSnapshot, key: string): string[] {
  return readStringArray(readRoomStartContractObject(snapshot)[key]);
}

function readRoomStartContractObject(snapshot: RoomSnapshot): PlanningObject {
  const canonical = readPlanningObject(snapshot, "room_start_contract");
  if (Object.keys(canonical).length > 0) {
    return canonical;
  }
  return readPlanningObject(snapshot, "preflight");
}

function readPlanningObject(
  snapshot: RoomSnapshot,
  key: string,
): PlanningObject {
  const value = snapshot.planning_artifacts[key];
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as PlanningObject;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => String(item).trim())
    .filter((item) => item.length > 0);
}
