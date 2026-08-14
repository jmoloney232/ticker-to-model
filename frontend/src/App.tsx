/* Three routes, hand-rolled history routing (no router dependency):
   "/" ticker entry · "/company/:ticker" the dashboard · "/methodology". */

import { useEffect, useState } from "react";
import { Company } from "./pages/Company";
import { Home } from "./pages/Home";
import { Methodology } from "./pages/Methodology";

export function navigate(path: string): void {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return path;
}

export function App() {
  const path = usePath();
  const company = path.match(/^\/company\/([A-Za-z.\-]{1,10})\/?$/);
  if (company) return <Company ticker={company[1].toUpperCase()} />;
  if (path === "/methodology") return <Methodology />;
  return <Home />;
}
