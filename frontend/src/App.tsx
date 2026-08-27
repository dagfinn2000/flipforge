import { Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Scanner from "./pages/Scanner";
import ItemPage from "./pages/Item";
import Watchlist from "./pages/Watchlist";
import Portfolio from "./pages/Portfolio";
import Alerts from "./pages/Alerts";
import Allocator from "./pages/Allocator";
import Validation from "./pages/Validation";
import System from "./pages/System";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/scanner" element={<Scanner />} />
        <Route path="/item/:id" element={<ItemPage />} />
        <Route path="/allocator" element={<Allocator />} />
        <Route path="/validation" element={<Validation />} />
        <Route path="/system" element={<System />} />
        <Route path="/watchlist" element={<Watchlist />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="*" element={<Dashboard />} />
      </Routes>
    </Layout>
  );
}
