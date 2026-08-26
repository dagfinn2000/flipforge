import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { gpShort, pct, tone } from "../lib/format";

/** Keyboard-first item search. Opens on Cmd/Ctrl-K or "/". */
export default function SearchPalette({ onClose }: { onClose: () => void }) {
  const [term, setTerm] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const { data, isFetching } = useQuery({
    queryKey: ["search", term],
    queryFn: () => api.search(term, 12),
    refetchInterval: false,
  });
  const results = data?.results ?? [];

  useEffect(() => inputRef.current?.focus(), []);
  useEffect(() => setCursor(0), [term]);

  const open = (id: number) => {
    navigate(`/item/${id}`);
    onClose();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") return onClose();
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, results.length - 1));
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    }
    if (e.key === "Enter" && results[cursor]) open(results[cursor].id);
  };

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Search items -- try 'whip', 'rune', 'bond'"
        />
        <div className="palette-results">
          {results.map((item, i) => (
            <div
              key={item.id}
              className={`palette-row${i === cursor ? " cursor" : ""}`}
              onMouseEnter={() => setCursor(i)}
              onClick={() => open(item.id)}
            >
              {item.icon_url && <img src={item.icon_url} alt="" loading="lazy" />}
              <div>
                <div>{item.name}</div>
                <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
                  {item.members ? "members" : "free to play"}
                  {item.buy_limit ? ` · limit ${item.buy_limit.toLocaleString()}` : ""}
                </div>
              </div>
              <div className="right">
                <div>{gpShort(item.high)} gp</div>
                <div className={tone(item.price_change_24h)} style={{ fontSize: 11 }}>
                  {pct(item.price_change_24h)}
                </div>
              </div>
            </div>
          ))}
          {!results.length && (
            <div className="palette-empty">
              {isFetching ? "Searching..." : term ? "No items match that search." : "Start typing an item name."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
