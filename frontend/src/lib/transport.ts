import { roomEventsSseUrl, roomEventsWsUrl } from "./api";
import type { RoomEvent } from "./types";

export type ConnectionState =
  | "connecting"
  | "live_ws"
  | "live_sse"
  | "disconnected";

type StreamOptions = {
  roomId: string;
  afterSeq: number;
  onEvent: (event: RoomEvent) => void;
  onStateChange: (state: ConnectionState) => void;
};

const SSE_EVENT_TYPES = [
  "planning.completed",
  "room.started",
  "agent.joined",
  "message.chunk",
  "message.commit",
  "agent.message",
  "human.message",
  "human.override",
  "agent.summary",
  "consensus.check",
  "agent.handoff",
  "agent.challenge",
  "meeting.ended",
] as const;

export function connectRoomStream(options: StreamOptions): () => void {
  let currentSeq = options.afterSeq;
  let closed = false;
  let fallbackStarted = false;
  let startHandle: number | undefined;
  let retryHandle: number | undefined;
  let cleanup = () => undefined;

  const handleEvent = (event: RoomEvent) => {
    currentSeq = Math.max(currentSeq, event.room_seq);
    options.onEvent(event);
  };

  const handleSerializedEvent = (raw: string) => {
    const event = JSON.parse(raw) as RoomEvent;
    handleEvent(event);
  };

  const openSse = () => {
    if (closed || fallbackStarted) {
      return;
    }
    fallbackStarted = true;
    const source = new EventSource(roomEventsSseUrl(options.roomId, currentSeq));
    const onMessage = (message: MessageEvent<string>) => {
      handleSerializedEvent(message.data);
    };

    source.onopen = () => {
      if (!closed) {
        options.onStateChange("live_sse");
      }
    };
    source.onmessage = onMessage;
    for (const eventType of SSE_EVENT_TYPES) {
      source.addEventListener(eventType, onMessage as EventListener);
    }
    source.onerror = () => {
      if (closed) {
        return;
      }
      options.onStateChange("disconnected");
      source.close();
      if (!closed) {
        fallbackStarted = false;
        retryHandle = window.setTimeout(openSse, 1200);
      }
    };
    cleanup = () => {
      closed = true;
      if (retryHandle !== undefined) {
        window.clearTimeout(retryHandle);
      }
      source.close();
    };
  };

  const closeAll = () => {
    closed = true;
    if (startHandle !== undefined) {
      window.clearTimeout(startHandle);
    }
    if (retryHandle !== undefined) {
      window.clearTimeout(retryHandle);
    }
  };

  const openWebSocket = () => {
    if (closed) {
      return;
    }
    const socket = new WebSocket(roomEventsWsUrl(options.roomId, currentSeq));
    let failoverTriggered = false;

    const failover = () => {
      if (closed || failoverTriggered) {
        return;
      }
      failoverTriggered = true;
      try {
        socket.close();
      } catch {
        // Ignore close errors on failover.
      }
      openSse();
    };

    socket.onopen = () => options.onStateChange("live_ws");
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as RoomEvent;
      handleEvent(event);
    };
    socket.onerror = failover;
    socket.onclose = () => {
      if (closed) {
        return;
      }
      failover();
    };

    cleanup = () => {
      closeAll();
      try {
        socket.close();
      } catch {
        // Ignore close errors during teardown.
      }
    };
  };

  options.onStateChange("connecting");
  startHandle = window.setTimeout(() => {
    startHandle = undefined;
    if (typeof WebSocket === "undefined") {
      openSse();
      return;
    }
    openWebSocket();
  }, 0);

  cleanup = () => {
    closeAll();
  };
  return () => {
    cleanup();
  };
}
