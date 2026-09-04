import { useEffect, useState } from "react";
import type { Plan } from "./types";
import { Blocked, Unreachable } from "../system/Blocked";
import { blockers, usePreflight } from "../system/usePreflight";
import { useBlueprints } from "./useBlueprints";
import "./plan.css";

/**
 * Choose a paper, and see what it would actually be — before spending anything.
 *
 * The dry run is the thing this screen exists for. Competitors that assemble from
 * a pre-built question bank can only show arithmetic: *you asked for 40 marks and
 * picked types worth 40*. Because our solver runs locally with no model calls, the
 * right-hand panel shows the real allocation against the student's own uploads —
 * which files feed each section, and where their notes are too thin to fill one.
 *
 * Generate is therefore never a leap. You see the skeleton, the cost in calls, and
 * the shortfalls, and only then commit.
 */

type Status = "idle" | "loading" | "ready" | "error";

interface Props {
  /** The plan travels with the id: the generating screen draws its skeleton from
   *  the dry run the student just read, so it must not re-fetch and risk showing
   *  them a different set of numbers than the ones they pressed Generate on. */
  onGenerate?: (blueprintId: string, plan: Plan) => void;
}

export function BlueprintScreen({ onGenerate }: Props) {
  const { presets, loading: listing, error: listError } = useBlueprints();
  // Empty until the server says what it has. Defaulting to a literal id would
  // reintroduce exactly the assumption this screen stopped making.
  const [chosen, setChosen] = useState<string | null>(null);
  const blueprintId = chosen ?? presets[0]?.id ?? null;
  const [plan, setPlan] = useState<Plan | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string>("");
  const preflight = usePreflight();
  const stopped = blockers(preflight.checks, "generate");
  const [attempt, setAttempt] = useState(0);

  // "Check again" has to retry BOTH. An outage fails the preflight and the dry
  // run together, so clearing only the first left the panel blank — no figures,
  // no Generate — until the student changed preset or reloaded, with nothing on
  // screen saying why.
  const retry = () => {
    setAttempt((a) => a + 1);
    preflight.refresh();
  };

  useEffect(() => {
    if (!blueprintId) return;
    let cancelled = false;
    setStatus("loading");
    fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blueprint_id: blueprintId }),
    })
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
        return body as Plan;
      })
      .then((p) => {
        if (cancelled) return;
        setPlan(p);
        setStatus("ready");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e.message || e));
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [blueprintId, attempt]);

  return (
    <div className="bp">
      <header className="bp__head">
        <h1 className="bp__title">Build a practice paper</h1>
        <p className="bp__sub">
          Pick a shape. We&rsquo;ll show you what it would be, drawn from your own
          notes, before anything is written.
        </p>
      </header>

      <div className="bp__cols">
        <section className="bp__form" aria-label="Paper shape">
          <fieldset className="field">
            <legend className="field__legend">
              Paper shape
              <span className="field__why">How long it is and what it is worth.</span>
            </legend>
            <div className="presets" role="radiogroup" aria-label="Paper shape">
              {presets.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  role="radio"
                  aria-checked={blueprintId === p.id}
                  className={`preset${blueprintId === p.id ? " preset--on" : ""}`}
                  onClick={() => setChosen(p.id)}
                >
                  <span className="preset__label">{p.title}</span>
                  <span className="preset__hint">
                    {p.marks !== null
                      ? `${p.marks} marks · ${p.minutes} min`
                      : /* The plan for this one could not be read. The blueprint
                           is still offered, because it exists — but no numbers
                           are shown, rather than invented ones. */
                        p.id}
                  </span>
                </button>
              ))}
            </div>
            {listing && (
              <p className="presets__note">Asking the server what it can build…</p>
            )}
            {listError && (
              <p className="presets__note">
                Could not list the papers this server can build ({listError}).
              </p>
            )}
            {!listing && !listError && presets.length === 0 && (
              <p className="presets__note">This server has no blueprints installed.</p>
            )}
          </fieldset>

          {plan && (
            <fieldset className="field">
              <legend className="field__legend">
                Sections
                <span className="field__why">
                  What each part of the paper asks for.
                </span>
              </legend>
              <ul className="sections">
                {plan.sections.map((s) => (
                  <li key={s.section_id} className="srow">
                    <div className="srow__main">
                      <span className="srow__id">{s.section_id}</span>
                      <span className="srow__title">{s.title}</span>
                    </div>
                    <p className="srow__desc">
                      {s.slots_requested} × {s.item_type} · {s.marks_each} mark
                      {s.marks_each === 1 ? "" : "s"} each
                    </p>
                    {s.from_material ? (
                      s.sources.length > 0 && (
                        <p className="srow__src">
                          <span className="srow__dot" aria-hidden="true" />
                          {s.sources.join(" · ")}
                        </p>
                      )
                    ) : (
                      <p className="srow__synth">
                        Asks you to build something new — not answerable from your
                        notes.
                      </p>
                    )}
                    {s.short_by > 0 && (
                      <p className="srow__short">
                        Your material supports {s.slots_planned} of{" "}
                        {s.slots_requested} here. Upload more on this topic to fill
                        it.
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </fieldset>
          )}
        </section>

        <aside className="bp__panel" aria-label="Dry run">
          <div className="panel">
            <header className="panel__head">
              <h2 className="panel__title">Dry run</h2>
              <span
                className={`panel__state panel__state--${status}`}
                aria-live="polite"
              >
                {status === "loading"
                  ? "working"
                  : status === "ready"
                    ? "free — nothing sent"
                    : status === "error"
                      ? "unavailable"
                      : ""}
              </span>
            </header>

            {/* Order matters. A dead server fails the dry run too, and the
                hint below tells the student to go and upload something — which
                is wrong advice, confidently given, when the real cause is that
                nothing is listening. The outage explains the plan failure, so
                it is reported first and the plan error is suppressed. */}
            {preflight.unreachable ? (
              <Unreachable onRetry={retry} />
            ) : status === "error" ? (
              <p className="panel__error">
                {error}
                <span className="panel__errorhint">
                  The dry run reads your ingested notes. If you have not uploaded
                  anything yet, start there.
                </span>
              </p>
            ) : null}

            {plan && status !== "error" && (
              <>
                <div className="figures">
                  <Figure value={plan.summary.slots_planned} label="questions" />
                  <Figure
                    value={plan.summary.marks_planned}
                    label={`of ${plan.total_marks} marks`}
                  />
                  <Figure
                    value={`${Math.round(plan.summary.fill_ratio * 100)}%`}
                    label="filled"
                  />
                </div>

                <Meter
                  value={plan.summary.marks_planned}
                  total={plan.total_marks}
                  short={plan.summary.short_by_marks}
                />

                <dl className="facts">
                  <div>
                    <dt>Drawn from</dt>
                    <dd>
                      {plan.summary.sources_used.length} of your file
                      {plan.summary.sources_used.length === 1 ? "" : "s"}
                    </dd>
                  </div>
                  <div>
                    <dt>Not from your notes</dt>
                    <dd>
                      {plan.summary.synthesis_items} question
                      {plan.summary.synthesis_items === 1 ? "" : "s"}
                    </dd>
                  </div>
                  <div>
                    <dt>Placed by topic weight</dt>
                    <dd>{Math.round(plan.summary.allocation_fidelity * 100)}%</dd>
                  </div>
                </dl>

                {plan.summary.warnings.length > 0 && (
                  <ul className="panel__warnings">
                    {plan.summary.warnings.slice(0, 3).map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                )}

                {/* The gate, not a disabled button beside an explanation.
                    `memory` failing means the OS kills the process on load -
                    an access violation no handler catches and no screen can
                    report afterwards, so BEFORE the press is the only place it
                    can be prevented. */}
                {preflight.unreachable ? (
                  <Unreachable onRetry={retry} />
                ) : stopped.length > 0 ? (
                  <Blocked
                    blockers={stopped}
                    action="build a paper"
                    onRetry={retry}
                  />
                ) : (
                  <>
                    <button
                      type="button"
                      className="generate"
                      disabled={preflight.loading}
                      onClick={() => onGenerate?.(blueprintId, plan)}
                    >
                      {preflight.loading
                        ? "Checking this machine…"
                        : `Write these ${plan.summary.slots_planned} questions`}
                    </button>
                    <p className="generate__cost">
                      About {plan.summary.estimated_calls} request
                      {plan.summary.estimated_calls === 1 ? "" : "s"} to the model.
                      Takes around a minute the first time.
                    </p>
                  </>
                )}
              </>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

function Figure({ value, label }: { value: number | string; label: string }) {
  return (
    <div className="figure">
      <span className="figure__value">{value}</span>
      <span className="figure__label">{label}</span>
    </div>
  );
}

function Meter({
  value,
  total,
  short,
}: {
  value: number;
  total: number;
  short: number;
}) {
  const pct = total > 0 ? Math.min(100, (value / total) * 100) : 0;
  return (
    <div className="meter">
      <div
        className="meter__track"
        role="progressbar"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label="Marks the material supports"
      >
        <div className="meter__fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="meter__note">
        {short > 0
          ? `${short} marks short — your notes do not cover enough for the full paper.`
          : "Your notes cover the whole paper."}
      </p>
    </div>
  );
}
