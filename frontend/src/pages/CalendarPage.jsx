/**
 * CalendarPage.jsx — rules-driven review calendar + daily prediction history.
 *
 * Month grid of review reminders (Phase Framework milestones, T1/T2 hits,
 * earnings) and each session's model calls with their grade. Clicking a day
 * shows reminder details (mark done / dismiss / reopen) and that day's calls
 * with the post-close reflection. Below: upcoming reviews and the lessons the
 * prediction loop has learned, which can be retired or restored.
 *
 * Data: GET /calendar/events, PATCH /calendar/reminders/:id,
 *       GET /calendar/lessons, PATCH /calendar/lessons/:id
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import api from "../api/client";

const ORANGE = "#f59e0b";
const PHASE_KINDS = new Set(["grace_end", "bep_decision", "extension_end", "phase2_review"]);
const KIND_LABEL = {
  grace_end: "GRACE OVER",
  bep_decision: "BEP DECISION",
  extension_end: "EXTENSION END",
  phase2_review: "PHASE 2 REVIEW",
  earnings: "EARNINGS",
  t1_hit: "T1 HIT",
  t2_hit: "T2 HIT",
};
const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
const ARROW = { UP: "↑", DOWN: "↓", FLAT: "→" };

const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseIso = (s) => {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
};
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const fmtPct = (v) => {
  if (v == null) return "—";
  if (Math.abs(v) < 0.05) return "0.0%";
  return `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%`;
};
const rate = (c, t) => (t ? `${c}/${t} · ${Math.round((100 * c) / t)}%` : "no graded calls");

function kindColor(kind) {
  if (PHASE_KINDS.has(kind)) return ORANGE;
  if (kind === "earnings") return "var(--cyan)";
  return "var(--green)";
}

/** Monday-first 6-week grid covering the given month. */
function monthGrid(monthStart) {
  const offset = (monthStart.getDay() + 6) % 7;
  const first = addDays(monthStart, -offset);
  return Array.from({ length: 42 }, (_, i) => addDays(first, i));
}

const S = {
  page: { padding: "18px 22px", fontFamily: "var(--font-mono)", color: "var(--text)" },
  panel: { background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 4, padding: 14 },
  h: { fontSize: 11, letterSpacing: ".12em", color: "var(--mid)", margin: "0 0 10px" },
  btn: {
    background: "none", border: "1px solid var(--border)", color: "var(--text)", borderRadius: 3,
    fontFamily: "var(--font-mono)", fontSize: 11, padding: "3px 9px", cursor: "pointer",
  },
  pill: {
    border: "1px solid var(--border)", borderRadius: 3, padding: "3px 8px", fontSize: 11, color: "var(--mid)",
  },
  chip: (color, muted) => ({
    fontSize: 10, lineHeight: "14px", padding: "0 4px", borderRadius: 2, marginTop: 2, whiteSpace: "nowrap",
    overflow: "hidden", textOverflow: "ellipsis", color: muted ? "var(--muted)" : color,
    borderLeft: `2px solid ${muted ? "var(--muted)" : color}`, background: "rgba(255,255,255,0.03)",
    textDecoration: muted ? "line-through" : "none",
  }),
};

function ReminderCard({ r, onStatus }) {
  const closed = r.status !== "pending";
  return (
    <div style={{ borderLeft: `3px solid ${kindColor(r.kind)}`, padding: "6px 10px", marginBottom: 8, opacity: closed ? 0.55 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 12, color: "var(--bright, var(--text))" }}>{r.title}</span>
        <span style={{ fontSize: 10, color: kindColor(r.kind) }}>{KIND_LABEL[r.kind] || r.kind.toUpperCase()}</span>
      </div>
      {r.detail && <div style={{ fontSize: 11, color: "var(--mid)", margin: "4px 0 6px", lineHeight: 1.45 }}>{r.detail}</div>}
      <div style={{ display: "flex", gap: 6 }}>
        {closed ? (
          <>
            <span style={{ fontSize: 10, color: "var(--muted)", alignSelf: "center" }}>{r.status.toUpperCase()}</span>
            <button style={S.btn} onClick={() => onStatus(r, "pending")}>REOPEN</button>
          </>
        ) : (
          <>
            <button style={{ ...S.btn, borderColor: "var(--green)", color: "var(--green)" }} onClick={() => onStatus(r, "done")}>DONE</button>
            <button style={S.btn} onClick={() => onStatus(r, "dismissed")}>DISMISS</button>
          </>
        )}
      </div>
    </div>
  );
}

function PredictionRow({ p }) {
  const graded = p.outcome === "CORRECT" || p.outcome === "WRONG";
  const color = p.outcome === "CORRECT" ? "var(--green)" : p.outcome === "WRONG" ? "var(--red)" : "var(--mid)";
  return (
    <div style={{ padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
        <span>
          <b>{p.ticker}</b> {ARROW[p.direction]} {p.direction} <span style={{ color: "var(--mid)" }}>{p.confidence}%</span>
          <span style={{ color: "var(--mid)" }}> · exp {fmtPct(p.expected_move_pct)}</span>
        </span>
        <span style={{ color }}>
          {graded ? `${fmtPct(p.actual_change_pct)} ${p.outcome === "CORRECT" ? "✓" : "✗"}` : p.outcome || "PENDING"}
        </span>
      </div>
      {p.rationale && <div style={{ fontSize: 11, color: "var(--mid)", marginTop: 3 }}>{p.rationale}</div>}
      {p.action && <div style={{ fontSize: 11, color: ORANGE, marginTop: 2 }}>▸ {p.action}</div>}
      {p.reflection && (
        <div style={{ fontSize: 11, marginTop: 4, fontStyle: "italic", color: "var(--text)" }}>
          Why: {p.reflection}
          {p.market_change_pct != null && <span style={{ color: "var(--muted)" }}> (SPY {fmtPct(p.market_change_pct)})</span>}
        </div>
      )}
    </div>
  );
}

export default function CalendarPage({ token }) {
  const now = new Date();
  const [month, setMonth] = useState(new Date(now.getFullYear(), now.getMonth(), 1));
  const [data, setData] = useState(null);
  const [lessons, setLessons] = useState([]);
  // null until the user picks a day — then defaults to the server's (ET) today.
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const grid = useMemo(() => monthGrid(month), [month]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Grid range plus two weeks ahead so "upcoming" works at month end.
      const end = addDays(grid[41], 14);
      const [events, lessonRows] = await Promise.all([
        api.getCalendarEvents(iso(grid[0]), iso(end), token),
        api.getPredictionLessons(token),
      ]);
      setData(events);
      setLessons(lessonRows);
    } catch (e) {
      setError(e.message || "Failed to load calendar");
    } finally {
      setLoading(false);
    }
  }, [grid, token]);

  useEffect(() => { load(); }, [load]);

  const byDay = useMemo(() => {
    const m = {};
    for (const r of data?.reminders || []) (m[r.due_date] ||= { reminders: [], preds: [] }).reminders.push(r);
    for (const p of data?.predictions || []) (m[p.trade_date] ||= { reminders: [], preds: [] }).preds.push(p);
    return m;
  }, [data]);

  const setReminderStatus = async (r, status) => {
    try {
      const updated = await api.updateReminderStatus(r.reminder_id, status, token);
      setData((d) => ({ ...d, reminders: d.reminders.map((x) => (x.reminder_id === r.reminder_id ? updated : x)) }));
    } catch (e) {
      setError(e.message);
    }
  };

  const toggleLesson = async (l) => {
    try {
      const updated = await api.setLessonActive(l.lesson_id, !l.active, token);
      setLessons((ls) => ls.map((x) => (x.lesson_id === l.lesson_id ? updated : x)));
    } catch (e) {
      setError(e.message);
    }
  };

  const today = data?.today || iso(now);
  const tr = data?.track_record;
  const openReviews = (data?.reminders || []).filter((r) => r.status === "pending" && r.due_date <= today).length;
  const upcoming = (data?.reminders || [])
    .filter((r) => r.status === "pending" && r.due_date > today && r.due_date <= iso(addDays(parseIso(today), 14)))
    .slice(0, 12);
  const sel = selected ?? today;
  const day = byDay[sel] || { reminders: [], preds: [] };
  const dayGraded = day.preds.filter((p) => p.outcome === "CORRECT" || p.outcome === "WRONG");

  return (
    <div style={S.page}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <h2 style={{ margin: 0, fontSize: 14, letterSpacing: ".14em" }}>REVIEW CALENDAR</h2>
        <button style={S.btn} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>◀</button>
        <span style={{ minWidth: 120, textAlign: "center", fontSize: 12 }}>
          {month.toLocaleString("en-US", { month: "long", year: "numeric" }).toUpperCase()}
        </span>
        <button style={S.btn} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>▶</button>
        <button style={S.btn} onClick={() => { setMonth(new Date(now.getFullYear(), now.getMonth(), 1)); setSelected(today); }}>TODAY</button>
        {loading && <span style={{ fontSize: 11, color: "var(--muted)" }}>loading…</span>}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span style={S.pill}>OPEN REVIEWS <b style={{ color: openReviews ? ORANGE : "var(--mid)" }}>{openReviews}</b></span>
          <span style={S.pill}>CALLS 30D <b style={{ color: "var(--text)" }}>{tr ? rate(tr.correct, tr.total) : "—"}</b></span>
          <span style={S.pill}>HIGH-CONF <b style={{ color: "var(--text)" }}>{tr ? rate(tr.high_correct, tr.high_total) : "—"}</b></span>
        </div>
      </div>

      {error && <div style={{ ...S.panel, borderColor: "var(--red)", color: "var(--red)", marginBottom: 12, fontSize: 12 }}>{error}</div>}

      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div style={{ ...S.panel, flex: "1 1 620px", minWidth: 0, padding: 8 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 4 }}>
            {WEEKDAYS.map((w) => (
              <div key={w} style={{ fontSize: 10, color: "var(--muted)", textAlign: "center", padding: 4 }}>{w}</div>
            ))}
            {grid.map((d) => {
              const key = iso(d);
              const cell = byDay[key];
              const inMonth = d.getMonth() === month.getMonth();
              const graded = (cell?.preds || []).filter((p) => p.outcome === "CORRECT" || p.outcome === "WRONG");
              const correct = graded.filter((p) => p.outcome === "CORRECT").length;
              const reminders = cell?.reminders || [];
              return (
                <div
                  key={key}
                  onClick={() => setSelected(key)}
                  style={{
                    minHeight: 86, padding: 5, borderRadius: 3, cursor: "pointer", overflow: "hidden",
                    background: key === sel ? "rgba(255,255,255,0.06)" : "rgba(255,255,255,0.015)",
                    border: `1px solid ${key === today ? ORANGE : key === sel ? "var(--mid)" : "var(--border)"}`,
                    opacity: inMonth ? 1 : 0.4,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
                    <span style={{ color: key === today ? ORANGE : "var(--mid)" }}>{d.getDate()}</span>
                    {cell?.preds?.length > 0 && (
                      <span
                        title="Model calls graded correct / total"
                        style={{ color: graded.length ? (correct * 2 >= graded.length ? "var(--green)" : "var(--red)") : "var(--mid)" }}
                      >
                        {graded.length ? `${correct}/${graded.length}` : `${cell.preds.length} calls`}
                      </span>
                    )}
                  </div>
                  {reminders.slice(0, 3).map((r) => (
                    <div key={r.reminder_id} style={S.chip(kindColor(r.kind), r.status !== "pending")} title={r.title}>
                      {r.ticker} {KIND_LABEL[r.kind] || r.kind}
                    </div>
                  ))}
                  {reminders.length > 3 && <div style={{ fontSize: 10, color: "var(--muted)" }}>+{reminders.length - 3} more</div>}
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ ...S.panel, flex: "0 1 380px", minWidth: 280 }}>
          <h3 style={S.h}>
            {parseIso(sel).toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "short" }).toUpperCase()}
          </h3>
          {day.reminders.length === 0 && day.preds.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>Nothing scheduled.</div>
          )}
          {day.reminders.map((r) => <ReminderCard key={r.reminder_id} r={r} onStatus={setReminderStatus} />)}
          {day.preds.length > 0 && (
            <>
              <h3 style={{ ...S.h, marginTop: 14 }}>
                MODEL CALLS {dayGraded.length > 0 && `· ${dayGraded.filter((p) => p.outcome === "CORRECT").length}/${dayGraded.length} CORRECT`}
              </h3>
              {day.preds.map((p) => <PredictionRow key={p.prediction_id} p={p} />)}
              <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                Model calls, not advice. FLAT = within ±0.5% of the previous close.
              </div>
            </>
          )}
        </div>
      </div>

      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 14, alignItems: "flex-start" }}>
        <div style={{ ...S.panel, flex: "1 1 320px" }}>
          <h3 style={S.h}>UPCOMING · NEXT 14 DAYS</h3>
          {upcoming.length === 0 && <div style={{ fontSize: 12, color: "var(--muted)" }}>No reviews scheduled.</div>}
          {upcoming.map((r) => (
            <div
              key={r.reminder_id}
              onClick={() => { const d = parseIso(r.due_date); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); setSelected(r.due_date); }}
              style={{ display: "flex", gap: 10, fontSize: 12, padding: "4px 0", cursor: "pointer", borderBottom: "1px solid var(--border)" }}
            >
              <span style={{ color: "var(--mid)", minWidth: 78 }}>
                {parseIso(r.due_date).toLocaleDateString("en-US", { weekday: "short", day: "2-digit", month: "short" })}
              </span>
              <span style={{ color: kindColor(r.kind) }}>●</span>
              <span>{r.title}</span>
            </div>
          ))}
        </div>

        <div style={{ ...S.panel, flex: "2 1 480px" }}>
          <h3 style={S.h}>LESSONS LEARNED · FED INTO TOMORROW'S CALLS</h3>
          {lessons.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              No lessons yet — they're distilled from missed calls after each close.
            </div>
          )}
          {lessons.map((l) => (
            <div key={l.lesson_id} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "6px 0", borderBottom: "1px solid var(--border)", opacity: l.active ? 1 : 0.5 }}>
              <span style={{ ...S.pill, fontSize: 10, padding: "1px 6px", minWidth: 56, textAlign: "center", color: l.ticker ? "var(--cyan)" : ORANGE }}>
                {l.ticker || "GENERAL"}
              </span>
              <span style={{ flex: 1, fontSize: 12, lineHeight: 1.45 }}>
                {l.lesson}
                <span style={{ color: "var(--muted)", fontSize: 10 }}> · {l.source_date}</span>
              </span>
              <button
                style={{ ...S.btn, color: l.active ? "var(--green)" : "var(--mid)", borderColor: l.active ? "var(--green)" : "var(--border)" }}
                title={l.active ? "Stop feeding this lesson to the model" : "Feed this lesson to the model again"}
                onClick={() => toggleLesson(l)}
              >
                {l.active ? "IN USE" : "RETIRED"}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
