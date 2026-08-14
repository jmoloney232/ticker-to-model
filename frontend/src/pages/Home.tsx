import { useState } from "react";
import { navigate } from "../App";

const EXAMPLES = ["MSFT", "KO", "COST", "KHC", "MCD"];

export function Home() {
  const [t, setT] = useState("");
  const go = (ticker: string) => {
    const clean = ticker.trim().toUpperCase();
    if (clean) navigate(`/company/${clean}`);
  };
  return (
    <div className="shell">
      <div className="home">
        <span className="regmark tl" />
        <span className="regmark br" />
        <div className="kicker">Ticker to Model</div>
        <h1>DCF from the filings up</h1>
        <p className="pitch">
          Enter a US ticker. The model is built from the company&rsquo;s own
          10-Ks — every assumption derived by a documented rule, every one
          editable, and the whole thing downloadable as an Excel workbook with
          live formulas. Banks, insurers, and REITs are declined honestly
          rather than modeled badly.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            go(t);
          }}
        >
          <input
            value={t}
            onChange={(e) => setT(e.target.value)}
            placeholder="MSFT"
            aria-label="Ticker"
            maxLength={10}
          />
          <span className="dl">
            <button type="submit" className="dl-btn">
              Build the model
            </button>
            <span className="regmark tl" />
            <span className="regmark br" />
          </span>
        </form>
        <div className="examples">
          <span>Try:</span>
          {EXAMPLES.map((e) => (
            <button key={e} type="button" onClick={() => go(e)}>
              {e}
            </button>
          ))}
        </div>
      </div>
      <nav className="foot-nav narrow">
        <a
          href="/methodology"
          onClick={(e) => {
            e.preventDefault();
            navigate("/methodology");
          }}
        >
          Methodology →
        </a>
      </nav>
    </div>
  );
}
