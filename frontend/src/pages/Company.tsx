/* The dashboard: derive → preset → overrides → POST /api/model, debounced.
   Redesign (owner sign-off 2026-08-15): three tabs — Summary (the answer),
   Model (the work), Audit (the evidence) — expanding in place, never
   navigating away. Keep the concepts, hide the conventions: Summary carries
   the verdict, the terminal-growth slider, and the ranked drivers; the full
   density lives one tab (or one toggle) away. Assumption edits recompute
   server-side against cached inputs; the URL carries the canonical share
   code; the workbook download uses the same code, so screen == download. */

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
import { AuditTab } from "../components/AuditTab";
import { Caveats, CheckBand } from "../components/Caveats";
import { Drivers } from "../components/Drivers";
import { GrowthSlider } from "../components/GrowthSlider";
import { Hero } from "../components/Hero";
import { ProfileBand } from "../components/ProfileBand";
import { Sensitivity } from "../components/Sensitivity";
import { reasonDetail, StateCard } from "../components/StateCard";
import { ProjectionsTable } from "../components/Statements";
import { SummaryCaveats } from "../components/SummaryCaveats";
import { fmtPrice } from "../format";
import type { ModelBlocked, ModelOk, PresetInfo } from "../types";

const DEBOUNCE_MS = 400;
const TABS = ["summary", "model", "audit"] as const;
type Tab = (typeof TABS)[number];

type Overrides = Record<string, number | boolean>;

/* localStorage can be absent (test env) or throw (private browsing) —
   the toggle degrades to per-session state */
function storedDetail(): boolean {
  try {
    return window.localStorage.getItem("ttm-detail") === "1";
  } catch {
    return false;
  }
}

function storeDetail(on: boolean): void {
  try {
    window.localStorage.setItem("ttm-detail", on ? "1" : "0");
  } catch {
    /* per-session only */
  }
}

function tabFromUrl(): Tab {
  const t = new URLSearchParams(window.location.search).get("tab");
  return (TABS as readonly string[]).includes(t ?? "") ? (t as Tab) : "summary";
}

function setUrlParam(key: string, value: string | null) {
  const params = new URLSearchParams(window.location.search);
  if (value == null) params.delete(key);
  else params.set(key, value);
  const q = params.toString();
  window.history.replaceState(
    null,
    "",
    window.location.pathname + (q ? `?${q}` : ""),
  );
}

function Header({ ticker, model }: { ticker: string; model: ModelOk | null }) {
  const [t, setT] = useState(ticker);
  const price = model?.market.price;
  const basis = model?.company.filing_basis;
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
        {model && <span>valued {model.valuation_date}</span>}
      </div>
    </div>
  );
}

function TabBar({
  tab,
  onTab,
  model,
  detail,
  onDetail,
}: {
  tab: Tab;
  onTab: (t: Tab) => void;
  model: ModelOk;
  detail: boolean;
  onDetail: (on: boolean) => void;
}) {
  const counts: Record<Tab, string> = {
    summary: "",
    model: `${model.assumptions.filter((a) => a.editable).length} assumptions`,
    audit: `${model.warnings.length} items`,
  };
  return (
    <div className="tabs" role="tablist">
      {TABS.map((t) => (
        <button
          key={t}
          type="button"
          role="tab"
          aria-selected={tab === t}
          className={`tab${tab === t ? " on" : ""}`}
          onClick={() => onTab(t)}
        >
          {t}
          {counts[t] && <span className="n">{counts[t]}</span>}
        </button>
      ))}
      {tab === "summary" && (
        <button
          type="button"
          className="detail-toggle"
          aria-pressed={detail}
          onClick={() => onDetail(!detail)}
        >
          <span>full detail</span>
          <span className={`switch${detail ? " on" : ""}`} />
        </button>
      )}
    </div>
  );
}

/* preset strip — Model tab (owner layout: Summary carries the answer) */
function PresetStrip({
  presets,
  model,
  activePreset,
  onPreset,
}: {
  presets: PresetInfo[];
  model: ModelOk;
  activePreset: string | null;
  onPreset: (name: string | null) => void;
}) {
  const wacc = model.wacc.wacc as number;
  const beta = model.wacc.beta_used as number | null;
  return (
    <div className="preset-band">
      <div className="preset-band-meta">
        <span className="kicker">Assumption presets</span>
        <span className="wacc-note">
          WACC {(wacc * 100).toFixed(2)}%
          {beta != null ? ` · β ${beta.toFixed(2)}` : ""}
        </span>
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

function Actions({ ticker, model }: { ticker: string; model: ModelOk }) {
  return (
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
  );
}

export function Company({ ticker }: { ticker: string }) {
  const [seeded, setSeeded] = useState(false);
  const [preset, setPreset] = useState<string | null>(null);
  const [profileTag, setProfileTag] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Overrides>({});
  const [model, setModel] = useState<ModelOk | null>(null);
  const [blocked, setBlocked] = useState<ModelBlocked | null>(null);
  const [failed, setFailed] = useState<{ status: number; detail: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [presets, setPresets] = useState<PresetInfo[]>([]);
  const [tab, setTabState] = useState<Tab>(tabFromUrl);
  const [detail, setDetailState] = useState(storedDetail);
  const lastGood = useRef<{ preset: string | null; overrides: Overrides }>({
    preset: null,
    overrides: {},
  });
  const firstLoad = useRef(true);
  const [retryTick, setRetryTick] = useState(0);

  const setTab = (t: Tab) => {
    setTabState(t);
    setUrlParam("tab", t === "summary" ? null : t);
  };
  const setDetail = (on: boolean) => {
    setDetailState(on);
    storeDetail(on);
  };

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
    setProfileTag(null);
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
        setProfileTag(d.profile ?? null);
        setOverrides(d.overrides ?? {});
        setSeeded(true);
      })
      .catch(() => {
        /* malformed code: drop it, build derived defaults */
        if (!alive) return;
        setUrlParam("c", null);
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
      fetchModel(ticker, { preset, overrides, profile: profileTag },
                 ctrl.signal)
        .then((resp) => {
          setBusy(false);
          if (resp.status === "ok") {
            setModel(resp);
            setBlocked(null);
            setFailed(null);
            setOverrideError(null);
            lastGood.current = { preset, overrides };
            const hasSet = preset != null || profileTag != null ||
              Object.keys(overrides).length > 0;
            setUrlParam("c", hasSet && resp.code ? resp.code : null);
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
  }, [ticker, seeded, preset, profileTag, overrides, retryTick]);

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
    const { status, detail: d } = failed;
    return (
      <div className="shell">
        {status === 404 ? (
          <StateCard
            kicker="Unknown ticker"
            title={`${ticker} isn’t in the EDGAR ticker file`}
            message={d}
          />
        ) : status === 503 || status === 0 ? (
          <StateCard
            kicker="Data source unavailable"
            title="EDGAR or market data is unreachable"
            message={`${d} Nothing has been cached for this ticker yet, so there is no model to fall back to — try again in a minute.`}
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
            message={d}
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
    /* plain sentence leads (server verdict); the technical reason and
       machine detail stay on the card for the audit-minded */
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
          message={blocked.verdict ?? r.message}
          detail={[
            blocked.verdict ? r.message : null,
            reasonDetail(r),
          ]
            .filter(Boolean)
            .join("\n")}
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

  /* the active preset's authored field notes for the rule inspector */
  const applied = model.preset
    ? presets.find((p) => p.name === model.preset?.name)
    : undefined;
  const presetNotes = applied
    ? Object.fromEntries(
        applied.fields.filter((f) => f.note).map((f) => [f.field, f.note!]),
      )
    : undefined;

  const curve = model.curves["terminal_growth"];
  const gordonReason = !model.valuation.gordon.available
    ? model.valuation.gordon.reason.message
    : null;
  const company = model.company.short_name || model.company.name;

  const modelDensity = (
    <>
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
          <Actions ticker={ticker} model={model} />
        </div>
        <Sensitivity model={model} />
      </div>
    </>
  );

  return (
    <div className="shell">
      <div className="board">
        <Header ticker={ticker} model={model} />
        <TabBar
          tab={tab}
          onTab={setTab}
          model={model}
          detail={detail}
          onDetail={setDetail}
        />
        <div className={`busybar${busy ? " on" : ""}`} />

        {tab === "summary" && (
          <>
            <Hero model={model} />
            <CheckBand checks={model.checks} />
            <div className="verdict">
              <div className="kicker">Verdict</div>
              <p>{model.verdict.text}</p>
            </div>
            {curve ? (
              <GrowthSlider
                curve={curve}
                price={model.market.price.value}
                onCommit={(g) =>
                  onOverride(
                    "terminal_growth",
                    g === curve.landmarks.derived ? null : g,
                  )
                }
              />
            ) : (
              <div className="sliderband">
                <span className="kicker">Long-run growth (terminal g)</span>
                <div className="slider-cap">
                  No slider here: {gordonReason ?? "the perpetuity leg is unavailable."}
                </div>
              </div>
            )}
            {overrideError && (
              <div className="override-err">{overrideError}</div>
            )}
            {detail ? (
              modelDensity
            ) : (
              <>
                <Drivers drivers={model.drivers} company={company} />
                <SummaryCaveats
                  digest={model.warnings_digest}
                  warnings={model.warnings}
                  checks={model.checks}
                />
                <div className="prov">
                  <span className="glyph" aria-hidden>
                    ■
                  </span>
                  <span>
                    Every assumption is derived from {company}&rsquo;s own SEC
                    filings by documented rules — nothing hand-picked
                    {model.provenance_counts.user > 0
                      ? `, ${model.provenance_counts.user} edited by you`
                      : ""}
                    . Edit any of them in Model.
                  </span>
                </div>
                <Actions ticker={ticker} model={model} />
              </>
            )}
          </>
        )}

        {tab === "model" && (
          <>
            <CheckBand checks={model.checks} />
            {model.profile && (
              <ProfileBand
                profile={model.profile}
                requested={profileTag}
                onReassign={(tag) => {
                  setOverrideError(null);
                  setProfileTag(tag);
                }}
              />
            )}
            <PresetStrip
              presets={presets}
              model={model}
              activePreset={preset}
              onPreset={(p) => {
                setOverrideError(null);
                setPreset(p);
              }}
            />
            {modelDensity}
            <ProjectionsTable rows={model.projections} />
          </>
        )}

        {tab === "audit" && (
          <>
            <CheckBand checks={model.checks} />
            <AuditTab model={model} />
          </>
        )}
      </div>
    </div>
  );
}
