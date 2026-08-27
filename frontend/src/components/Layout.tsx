import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { api } from "../api";
import { useLive } from "../lib/live";
import { ago } from "../lib/format";
import SearchPalette from "./SearchPalette";

const NAV = [
  { to: "/", label: "Dashboard", icon: "◫", end: true },
  { to: "/scanner", label: "Flip scanner", icon: "◳" },
  { to: "/allocator", label: "Slot allocator", icon: "▦" },
  { to: "/watchlist", label: "Watchlist", icon: "★" },
  { to: "/portfolio", label: "Portfolio", icon: "◑" },
  { to: "/alerts", label: "Alerts", icon: "◉" },
  { to: "/validation", label: "Score check", icon: "◈" },
  { to: "/system", label: "System", icon: "⚙" },
];

export default function Layout({ children }: { children: ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { connected, lastTick, alerts, dismissAlert, backfill } = useLive();

  const { data: summary } = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const { data: events } = useQuery({
    queryKey: ["alert-events-badge"],
    queryFn: () => api.alertEvents(20),
    refetchInterval: 60_000,
  });
  const unseen = events?.results.filter((e) => !e.seen).length ?? 0;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
      if (e.key === "/" && !(e.target as HTMLElement)?.closest("input, textarea")) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const backfilling = backfill && !backfill.complete && backfill.total > 1;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">{"⚒"}</span>
          <span>
            FlipForge
            <small>OSRS market intel</small>
          </span>
        </div>

        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
          >
            <span aria-hidden>{item.icon}</span>
            {item.label}
            {item.to === "/alerts" && unseen > 0 && <span className="badge">{unseen}</span>}
          </NavLink>
        ))}

        <button className="nav-link" onClick={() => setPaletteOpen(true)}>
          <span aria-hidden>{"⌕"}</span>
          Search
          <kbd style={{ marginLeft: "auto" }}>{"⌘"}K</kbd>
        </button>

        <div className="sidebar-foot">
          <div className="status">
            <span className={`dot ${connected ? "live" : "dead"}`} />
            {connected ? "live" : "reconnecting"}
          </div>
          <div className="status" style={{ marginTop: 4 }}>
            {summary ? `${summary.tracked.toLocaleString()} items` : "loading"}
          </div>
          <div className="status" style={{ marginTop: 4 }}>
            tick {lastTick ? ago(Math.floor(lastTick / 1000)) : ago(summary?.last_poll)}
          </div>
          {summary && (
            <div
              className="status"
              style={{ marginTop: 4 }}
              title={summary.tax_policy.note}
            >
              tax {(summary.tax_policy.rate * 100).toFixed(0)}% · free under{" "}
              {summary.tax_policy.free_below}gp
            </div>
          )}
        </div>
      </aside>

      <div className="main">
        {backfilling && (
          <div className="banner" style={{ margin: "12px 22px 0" }}>
            Building price history: {backfill!.done} / {backfill!.total} windows. Charts and
            24h statistics fill in as this completes.
          </div>
        )}
        <div className="content">{children}</div>
      </div>

      {paletteOpen && <SearchPalette onClose={() => setPaletteOpen(false)} />}

      <div className="toasts">
        {alerts.map((a) => {
          const data = a.data as { id: number; item_name: string; message: string };
          return (
            <div className="toast" key={data.id} onClick={() => dismissAlert(data.id)}>
              <span aria-hidden>{"◉"}</span>
              <div>
                <div className="t-title">Alert: {data.item_name}</div>
                <div className="t-body">{data.message}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
