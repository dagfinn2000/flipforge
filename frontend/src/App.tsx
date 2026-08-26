import { Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Scanner from "./pages/Scanner";
import ItemPage from "./pages/Item";
import Watchlist from "./pages/Watchlist";
import Portfolio from "./pages/Portfolio";
import Alerts from "./pages/Alerts";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/scanner" element={<Scanner />} />
        <Route path="/item/:id" element={<ItemPage />} />
        <Route path="/watchlist" element={<Watchlist />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="*" element={<Dashboard />} />
      </Routes>
    </Layout>
  );
}
