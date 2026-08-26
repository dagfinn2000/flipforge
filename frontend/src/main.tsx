import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import { LiveProvider } from "./lib/live";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Prices move constantly; keep views fresh without hammering the API.
      refetchInterval: 30_000,
      refetchOnWindowFocus: true,
      staleTime: 10_000,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <LiveProvider>
          <App />
        </LiveProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
