import test from "node:test";
import assert from "node:assert/strict";

const timerHandles = [];

globalThis.window = {
  setTimeout(callback) {
    timerHandles.push(callback);
    return timerHandles.length;
  },
  clearTimeout() {},
};

function flushTimers() {
  while (timerHandles.length > 0) {
    const callback = timerHandles.shift();
    callback();
  }
}

test("websocket failover starts SSE from the last acknowledged sequence", async () => {
  const wsInstances = [];
  const sseInstances = [];
  const states = [];
  const seenEvents = [];

  class MockWebSocket {
    constructor(url) {
      this.url = url;
      this.onopen = null;
      this.onmessage = null;
      this.onerror = null;
      this.onclose = null;
      wsInstances.push(this);
    }

    close() {
      if (this.onclose) {
        this.onclose();
      }
    }
  }

  class MockEventSource {
    constructor(url) {
      this.url = url;
      this.onopen = null;
      this.onmessage = null;
      this.onerror = null;
      this.listeners = new Map();
      sseInstances.push(this);
    }

    addEventListener(type, handler) {
      this.listeners.set(type, handler);
    }

    close() {}
  }

  globalThis.WebSocket = MockWebSocket;
  globalThis.EventSource = MockEventSource;
  globalThis.fetch = async () => {
    throw new Error("fetch should not be called in transport contract tests");
  };

  const { connectRoomStream } = await import(
    "../.contract-dist/lib/transport.js"
  );

  const cleanup = connectRoomStream({
    roomId: "room_transport",
    afterSeq: 4,
    onEvent: (event) => seenEvents.push(event),
    onStateChange: (state) => states.push(state),
  });
  flushTimers();

  assert.equal(wsInstances.length, 1);
  assert.match(wsInstances[0].url, /last_acked_seq=4/);

  wsInstances[0].onopen?.();
  wsInstances[0].onmessage?.({
    data: JSON.stringify({
      schema_version: "1.0",
      event_id: "evt_9",
      room_id: "room_transport",
      room_seq: 9,
      producer_id: "agent.host.1",
      role: "host",
      event_type: "agent.message",
      ts_ms: 9,
      payload: {},
    }),
  });
  wsInstances[0].onerror?.(new Error("ws down"));

  assert.equal(sseInstances.length, 1);
  assert.match(sseInstances[0].url, /after_seq=9/);
  assert.equal(seenEvents.at(-1)?.room_seq, 9);
  assert.deepEqual(states, ["connecting", "live_ws"]);

  cleanup();
});

test("cleanup prevents websocket failure from starting SSE fallback", async () => {
  const wsInstances = [];
  const sseInstances = [];

  class MockWebSocket {
    constructor(url) {
      this.url = url;
      this.onopen = null;
      this.onmessage = null;
      this.onerror = null;
      this.onclose = null;
      wsInstances.push(this);
    }

    close() {}
  }

  class MockEventSource {
    constructor(url) {
      this.url = url;
      sseInstances.push(this);
    }

    addEventListener() {}

    close() {}
  }

  globalThis.WebSocket = MockWebSocket;
  globalThis.EventSource = MockEventSource;
  globalThis.fetch = async () => {
    throw new Error("fetch should not be called in transport contract tests");
  };

  const { connectRoomStream } = await import(
    "../.contract-dist/lib/transport.js"
  );

  const cleanup = connectRoomStream({
    roomId: "room_transport",
    afterSeq: 0,
    onEvent: () => {},
    onStateChange: () => {},
  });
  flushTimers();

  assert.equal(wsInstances.length, 1);
  cleanup();
  wsInstances[0].onerror?.(new Error("ignored after cleanup"));

  assert.equal(sseInstances.length, 0);
});
