import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

export interface LiveEvent {
  event: string;
  data: Record<string, unknown>;
}

interface LiveState {
  connected: boolean;
  lastTick: number | null;
  alerts: LiveEvent[];
  backfill: { done: number; total: number; complete?: boolean } | null;
  dismissAlert: (id: number) => void;
}

const LiveContext = createContext<LiveState>({
  connected: false, lastTick: null, alerts: [], backfill: null, dismissAlert: () => {},
});

/** Keeps one websocket open for the whole app and reconnects with backoff. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [lastTick, setLastTick] = useState<number | null>(null);
  const [alerts, setAlerts] = useState<LiveEvent[]>([]);
  const [backfill, setBackfill] = useState<LiveState["backfill"]>(null);
  const retry = useRef(0);
  const socket = useRef<WebSocket | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;

    const connect = () => {
      if (stopped) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      socket.current = ws;

      ws.onopen = () => { setConnected(true); retry.current = 0; };
      ws.onmessage = (raw) => {
        const msg = JSON.parse(raw.data) as LiveEvent;
        if (msg.event === "latest" || msg.event === "metrics") {
          setLastTick(Date.now());
        } else if (msg.event === "alert") {
          setAlerts((prev) => [msg, ...prev].slice(0, 8));
        } else if (msg.event === "backfill") {
          setBackfill(msg.data as LiveState["backfill"]);
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (stopped) return;
        const wait = Math.min(1000 * 2 ** retry.current++, 15000);
        timer = window.setTimeout(connect, wait);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    // A ping keeps intermediaries from closing an idle tunnel.
    const ping = window.setInterval(() => {
      if (socket.current?.readyState === WebSocket.OPEN) socket.current.send("ping");
    }, 25000);

    return () => {
      stopped = true;
      window.clearInterval(ping);
      if (timer) window.clearTimeout(timer);
      socket.current?.close();
    };
  }, []);

  const value = useMemo<LiveState>(
    () => ({
      connected, lastTick, alerts, backfill,
      dismissAlert: (id: number) =>
        setAlerts((prev) => prev.filter((a) => (a.data as { id?: number }).id !== id)),
    }),
    [connected, lastTick, alerts, backfill],
  );

  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export const useLive = () => useContext(LiveContext);
