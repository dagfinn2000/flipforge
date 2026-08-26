import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { ago, clsx, gp, pct } from "../lib/format";

const formatValue = (metric: string, value: number | null | undefined) => {
  if (value === null || value === undefined) return "--";
  if (metric === "roi") return pct(value, 2, false);
  if (metric === "zscore_24h" || metric === "flip_score") return value.toFixed(2);
  return gp(value);
};

export default function Alerts() {
  const queryClient = useQueryClient();
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: api.alerts });
  const events = useQuery({ queryKey: ["alert-events"], queryFn: () => api.alertEvents(40) });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["alerts"] });
    queryClient.invalidateQueries({ queryKey: ["alert-events"] });
    queryClient.invalidateQueries({ queryKey: ["alert-events-badge"] });
  };

  const remove = useMutation({ mutationFn: api.deleteAlert, onSuccess: invalidate });
  const toggle = useMutation({ mutationFn: api.toggleAlert, onSuccess: invalidate });

  // Opening this page is the acknowledgement; clear the sidebar badge.
  useEffect(() => {
    if (events.data?.results.some((e) => !e.seen)) {
      api.markEventsSeen().then(() =>
        queryClient.invalidateQueries({ queryKey: ["alert-events-badge"] }),
      );
    }
  }, [events.data, queryClient]);

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <h1>Alerts</h1>
        <span className="sub">Fired alerts also pop up live anywhere in the app</span>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <div className="card-head">
            <h2>Active rules</h2>
            <span className="hint">{alerts.data?.results.length ?? 0} configured</span>
          </div>
          {alerts.data?.results.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Condition</th>
                    <th>Now</th>
                    <th>Last fired</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {alerts.data.results.map((a) => (
                    <tr key={a.id} style={{ opacity: a.active ? 1 : 0.45 }}>
                      <td>
                        <Link to={`/item/${a.item_id}`} className="item-cell">
                          {a.icon_url && <img src={a.icon_url} alt="" loading="lazy" />}
                          <span className="name">{a.name}</span>
                        </Link>
                      </td>
                      <td className="num">
                        {a.metric} {a.op} {formatValue(a.metric, a.threshold)}
                      </td>
                      <td className={clsx("num", a.distance != null && (a.op === "above" ? a.distance > 0 : a.distance < 0) && "up")}>
                        {formatValue(a.metric, a.current_value)}
                      </td>
                      <td className="num">{a.last_fired ? ago(a.last_fired) : "never"}</td>
                      <td>
                        <button className="btn small" onClick={() => toggle.mutate(a.id)}>
                          {a.active ? "pause" : "resume"}
                        </button>{" "}
                        <button className="btn ghost small" onClick={() => remove.mutate(a.id)}>
                          delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <h3>No alerts yet</h3>
              <p>Open an item and use "Alert me when" to add one.</p>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <h2>History</h2>
            <span className="hint">most recent first</span>
          </div>
          {events.data?.results.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>When</th><th>Event</th></tr>
                </thead>
                <tbody>
                  {events.data.results.map((e) => (
                    <tr key={e.id}>
                      <td className="num" style={{ textAlign: "left", color: "var(--text-faint)" }}>
                        {ago(e.created_at)}
                      </td>
                      <td style={{ textAlign: "left" }}>
                        <Link to={`/item/${e.item_id}`}>{e.message}</Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <h3>Nothing has fired</h3>
              <p>Alerts are evaluated once a minute against live prices.</p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
