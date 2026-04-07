import type {
  CreateRoomInput,
  PreflightRoomInput,
  RoomPreflightReport,
  RoomSnapshot,
  RoomSummary,
} from "./types";

const runtimeBaseEnv =
  typeof import.meta === "object" &&
  import.meta &&
  "env" in import.meta &&
  import.meta.env &&
  typeof import.meta.env === "object" &&
  typeof import.meta.env.VITE_RUNTIME_BASE_URL === "string"
    ? import.meta.env.VITE_RUNTIME_BASE_URL.trim()
    : "";

const runtimeBase = runtimeBaseEnv || "http://127.0.0.1:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function buildUrl(path: string): string {
  return new URL(path, runtimeBase).toString();
}

function extractErrorMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (
    detail &&
    typeof detail === "object" &&
    "message" in detail &&
    typeof detail.message === "string" &&
    detail.message.trim()
  ) {
    return detail.message;
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const raw = await response.text();
    let message = raw || `Request failed with ${response.status}`;
    let detail: unknown = undefined;
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      detail = typeof parsed === "object" && parsed && "detail" in parsed
        ? parsed.detail
        : parsed;
      const parsedMessage = extractErrorMessage(detail);
      if (parsedMessage) {
        message = parsedMessage;
      }
    } catch {
      // Keep the raw body when the server did not return JSON.
    }
    throw new ApiError(message, response.status, detail);
  }
  return (await response.json()) as T;
}

export function getRuntimeBaseUrl(): string {
  return runtimeBase;
}

export async function listRooms(): Promise<RoomSummary[]> {
  return request<RoomSummary[]>("/api/rooms");
}

export async function createRoom(input: CreateRoomInput): Promise<RoomSnapshot> {
  return request<RoomSnapshot>("/api/rooms", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function preflightRoom(
  input: PreflightRoomInput,
): Promise<RoomPreflightReport> {
  return request<RoomPreflightReport>("/api/rooms/preflight", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getRoomSnapshot(roomId: string): Promise<RoomSnapshot> {
  return request<RoomSnapshot>(`/api/rooms/${roomId}/snapshot`);
}

export async function postHumanMessage(
  roomId: string,
  text: string,
): Promise<RoomSnapshot> {
  return request<RoomSnapshot>(`/api/rooms/${roomId}/human-message`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export async function postHumanOverride(
  roomId: string,
  text: string,
): Promise<RoomSnapshot> {
  return request<RoomSnapshot>(`/api/rooms/${roomId}/human-override`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function roomEventsSseUrl(roomId: string, afterSeq: number): string {
  const url = new URL(`/api/rooms/${roomId}/events`, runtimeBase);
  url.searchParams.set("after_seq", String(afterSeq));
  return url.toString();
}

export function roomEventsWsUrl(roomId: string, afterSeq: number): string {
  const url = new URL(`/ws/rooms/${roomId}`, runtimeBase);
  url.searchParams.set("last_acked_seq", String(afterSeq));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
