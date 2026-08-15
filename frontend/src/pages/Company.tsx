/* The dashboard: derive → preset → overrides → POST /api/model, debounced.
   Assumption edits recompute server-side against the cached inputs (never a
   refetch upstream — API contract); the URL carries the canonical share code;
   the workbook download uses the same code, so screen == download. */

import { useCallback, useEffect, useRef, useState } from "react";
import { navigate } from "../App";
import {
  ApiError,
  decodeCode,
  fetchModel,
  fetchPresets,
  workbookUrl,
} from "../api";
import { Assumptions } from "../components/Assumptions";
import { Caveats, CheckBand } from "../components/Caveats";
import { Hero } from "../components/Hero";
import { Sensitivity } from "../components/Sensitivity";
import { reasonDetail, StateCard } from "../components/StateCard";
import { fmtPrice } from "../format";
import type { ModelBlocked, ModelOk, PresetInfo } from "../types";

const DEBOUNCE_MS = 400;

type Overrides = Record<string, number | boolean>;

function Header({
  ticker,
  model,
  presets,
  activePreset,
  onPreset,
}: {
  ticker: string;
  model: ModelOk | null;
  presets: PresetInfo[];
  activePreset: string | null;
  onPreset: (name: string | null) => void;
}) {
  const [t, setT] = useState(ticker);
  const price = model?.market.price;
  const basis = model?.company.filing_basis;
  const wacc = model ? (model.wacc.wacc as number) : null;
  const beta = model ? (model.wacc.beta_used as number | null) : null;
  return (
    <div className="hd">
      <div className="hd-id">
        <input
          className="hd-ticker"
          value={t}
          aria-label="Ticker"
          maxLength={10}
          onChange={(e) => setT(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && t.trim())
              navigate(`/company/${t.trim().toUpperCase()}`);
          }}
        />
        <span className="hd-name">{model?.company.name ?? "…"}</span>
      </div>
      <div className="hd-meta">
        {price && (
          <span>
            {fmtPrice(price.value)} last · {price.as_of}
            {price.staleness !== "live" && (
              <span className="chip-stale"> · {price.staleness}</span>
            )}
          </span>
        )}
        {basis && (
          <span>
            FY{basis.fiscal_year} 10-K
            {basis.filed ? ` · filed ${basis.filed}` : ""}
          </span>
        )}
        {wacc != null && (
          <span>
            WACC {(wacc * 100).toFixed(2)}%
            {beta != null ? ` · β ${beta.toFixed(2)}` : ""}
          </span>
        )}
      </div>
      <div className="presets">
        {presets.map((p) => {
          const isDerived = p.name === "derived";
          const on = activePreset === p.name || (isDerived && !activePreset);
          return (
            <button
              key={p.name}
              type="button"
              className={`preset${on ? " on" : ""}`}
              onClick={() => onPreset(isDerived ? null : p.name)}
            >
              <span className="preset-head">
                <span className="preset-glyph">{on ? "●" : "○"}</span>
                <span className="preset-name">{p.title}</span>
              </span>
              <span className="preset-why">{p.rationale}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function Company({ ticker }: { ticker: string }) {
  const [seeded, setSeeded] = useState(false);
  const [preset, setPreset] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Overrides>({});
  const [model, setModel] = useState<ModelOk | null>(null);
  const [blocked, setBlocked] = useState<ModelBlocked | null>(null);
  const [failed, setFailed] = useState<{ status: number; detail: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [presets, setPresets] = useState<PresetInfo[]>([]);
  const lastGood = useRef<{ preset: string | null; overrides: Overrides }>({
    preset: null,
    overrides: {},
  });
  const firstLoad = useRef(true);
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    fetchPresets()
      .then((r) => setPresets(r.presets))
      .catch(() => setPresets([]));
  }, []);

  /* seed preset/overrides from a shared ?c= code, once per ticker */
  useEffect(() => {
    setSeeded(false);
    setModel(null);
    setBlocked(null);
    setFailed(null);
    setPreset(null);
    setOverrides({});
    firstLoad.current = true;
    const code = new URLSearchParams(window.location.search).get("c");
    if (!code) {
      setSeeded(true);
      return;
    }
    let alive = true;
    decodeCode(code)
      .then((d) => {
        if (!alive) return;
        setPreset(d.preset);
        setOverrides(d.overrides ?? {});
        setSeeded(true);
      })
      .catch(() => {
        /* malformed code: drop it, build derived defaults */
        if (!alive) return;
        window.history.replaceState(null, "", window.location.pathname);
        setSeeded(true);
      });
    return () => {
      alive = false;
    };
  }, [ticker]);

  /* the recompute loop: debounced, abortable, never refetching upstream */
  useEffect(() => {
    if (!seeded) return;
    const ctrl = new AbortController();
    const run = () => {
      setBusy(true);
      fetchModel(ticker, { preset, overrides }, ctrl.signal)
        .then((resp) => {
          setBusy(false);
          if (resp.status === "ok") {
            setModel(resp);
            setBlocked(null);
            setFailed(null);
            setOverrideError(null);
            lastGood.current = { preset, overrides };
            const hasSet = preset != null || Object.keys(overrides).length > 0;
            const url =
              window.location.pathname +
              (hasSet && resp.code ? `?c=${encodeURIComponent(resp.code)}` : "");
            window.history.replaceState(null, "", url);
          } else {
            setBlocked(resp);
          }
        })
        .catch((err) => {
          if (ctrl.signal.aborted) return;
          setBusy(false);
          if (err instanceof ApiError && err.status === 400) {
            /* an override the engine's domain table rejected: show the
               constraint, revert to the last good set */
            setOverrideError(err.detail);
            setPreset(lastGood.current.preset);
            setOverrides(lastGood.current.overrides);
            return;
          }
          setFailed(
            err instanceof ApiError
              ? { status: err.status, detail: err.detail }
              : { status: 0, detail: "Network unreachable." },
          );
        });
    };
    const wait = firstLoad.current ? 0 : DEBOUNCE_MS;
    firstLoad.current = false;
    const id = window.setTimeout(run, wait);
    return () => {
      window.clearTimeout(id);
      ctrl.abort();
    };
  }, [ticker, seeded, preset, overrides, retryTick]);

  const onOverride = useCallback(
    (name: string, value: number | boolean | null) => {
      setOverrideError(null);
      setOverrides((prev) => {
        const next = { ...prev };
        if (value == null) delete next[name];
        else next[name] = value;
        return next;
      });
    },
    [],
  );

  /* ── states ─────────────────────────────────────────────────────────── */

  if (failed) {
    const { status, detail } = failed;
    return (
      <div className="shell">
        {status === 404 ? (
          <StateCard
            kicker="Unknown ticker"
            title={`${ticker} isn’t in the EDGAR ticker file`}
            message={detail}
          />
        ) : status === 503 || status === 0 ? (
          <StateCard
            kicker="Data source unavailable"
            title="EDGAR or market data is unreachable"
            message={`${detail} Nothing has been cached for this ticker yet, so there is no model to fall back to — try again in a minute.`}
          >
            <span className="dl">
              <button
                type="button"
                className="dl-btn"
                onClick={() => setRetryTick((n) => n + 1)}
              >
                Retry
              </button>
              <span className="regmark tl" />
              <span className="regmark br" />
            </span>
          </StateCard>
        ) : (
          <StateCard
            kicker={`Error ${status}`}
            title="That request didn’t work"
            message={detail}
          />
        )}
      </div>
    );
  }

  if (blocked) {
    const r = blocked.reason;
    if (blocked.status === "preset_unavailable") {
      return (
        <div className="shell">
          <StateCard
            kicker="Preset unavailable"
            title="This preset can’t be applied here"
            message={r.message}
            detail={reasonDetail(r)}
          >
            <span className="dl">
              <button
                type="button"
                className="dl-btn"
                onClick={() => {
                  setBlocked(null);
                  setPreset(null);
                }}
              >
                Return to derived defaults
              </button>
              <span className="regmark tl" />
              <span className="regmark br" />
            </span>
          </StateCard>
        </div>
      );
    }
    return (
      <div className="shell">
        <StateCard
          kicker={
            blocked.status === "unsupported"
              ? "Out of scope — by design"
              : "Refused — the model would not be trustworthy"
          }
          title={
            blocked.status === "unsupported"
              ? `${ticker} isn’t modeled here`
              : `${ticker} refused: ${r.code.replace(/_/g, " ")}`
          }
          message={r.message}
          detail={reasonDetail(r)}
        />
      </div>
    );
  }

  if (!model) {
    return (
      <div className="shell">
        <div className="statecard">
          <span className="regmark tl" />
          <span className="regmark br" />
          <div className="kicker">Ticker to Model</div>
          <h2>{ticker}</h2>
          <div className="loading-note">
            assembling model — EDGAR filings, market data, derived
            assumptions…
          </div>
        </div>
      </div>
    );
  }

  /* the active preset's authored field notes (fields[].note), for the
     rule inspector — matched on the preset the model actually applied */
  const applied = model.preset
    ? presets.find((p) => p.name === model.preset?.name)
    : undefined;
  const presetNotes = applied
    ? Object.fromEntries(
        applied.fields.filter((f) => f.note).map((f) => [f.field, f.note!]),
      )
    : undefined;

  return (
    <div className="shell">
      <div className="board">
        <Header
          ticker={ticker}
          model={model}
          presets={presets}
          activePreset={preset}
          onPreset={(p) => {
            setOverrideError(null);
            setPreset(p);
          }}
        />
        <div className={`busybar${busy ? " on" : ""}`} />
        <Hero model={model} />
        <CheckBand checks={model.checks} />
        <div className="cols">
          <div className="amain">
            <Assumptions
              rows={model.assumptions}
              overrideCount={Object.keys(overrides).length}
              overrideError={overrideError}
              presetNotes={presetNotes}
              onOverride={onOverride}
              onResetAll={() => {
                setOverrideError(null);
                setOverrides({});
              }}
            />
            <Caveats warnings={model.warnings} checks={model.checks} />
            <div className="actions">
              <span className="dl">
                <a className="dl-btn" href={workbookUrl(ticker, model.code)}>
                  Download workbook · xlsx
                </a>
                <span className="regmark tl" />
                <span className="regmark br" />
              </span>
              <a
                className="meth-link"
                href="/methodology"
                onClick={(e) => {
                  e.preventDefault();
                  navigate("/methodology");
                }}
              >
                Methodology →
              </a>
            </div>
          </div>
          <Sensitivity model={model} />
        </div>
      </div>
    </div>
  );
}
