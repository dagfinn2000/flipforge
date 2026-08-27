import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import ItemTable from "../components/ItemTable";

export default function Watchlist() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["watchlist"], queryFn: api.watchlist });

  const remove = useMutation({
    mutationFn: (itemId: number) => api.unwatch(itemId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["watchlist"] }),
  });

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <h1>Watchlist</h1>
        <span className="sub">Items you are tracking, ranked by current flip score</span>
      </div>

      <div className="card">
        <ItemTable
          rows={data?.results ?? []}
          columns={["score", "month", "buy", "sell", "margin", "breakeven", "roi", "vol24", "change24h", "age"]}
          emptyTitle={isLoading ? "Loading..." : "Your watchlist is empty"}
          emptyBody="Open any item and press Watch, or hit ⌘K to search."
          action={(row) => (
            <button className="btn ghost small" onClick={() => remove.mutate(row.id)}>
              remove
            </button>
          )}
        />
      </div>
    </>
  );
}
