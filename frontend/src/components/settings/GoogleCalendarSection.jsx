/**
 * GoogleCalendarSection.jsx — Settings section for linking the user's own
 * Google account (scope: calendar.app.created) so review reminders appear in
 * an app-created "TickerTap Reviews" calendar.
 *
 * The disclosure shown before connecting is part of Google's User Data Policy
 * ("prominent and timely" in-product disclosure) — keep it accurate if what
 * we access, store or send changes.
 */

import { useCallback, useEffect, useState } from "react";
import api from "../../api/client";

const OUTCOME_MESSAGES = {
  linked: { tone: "ok", text: "Google Calendar connected. Your review reminders will appear in the “TickerTap Reviews” calendar." },
  denied: { tone: "warn", text: "You cancelled on Google's screen — nothing was linked." },
  scope_missing: { tone: "warn", text: "The calendar permission wasn't granted, so nothing was linked. Tick it on Google's screen to connect." },
  expired: { tone: "warn", text: "That link attempt expired or was already used. Please try again." },
  error: { tone: "err", text: "Something went wrong linking Google Calendar. Please try again." },
};

const DETAIL_TEXT = {
  full: "ticker, review type, the rule text, entry price and % gain",
  minimal: "ticker and review type only",
};

const fmtDate = (s) => (s ? new Date(s).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function GoogleCalendarSection({ token, S }) {
  const [status, setStatus] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [deleteCalendar, setDeleteCalendar] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await api.getGoogleCalendarStatus(token));
    } catch (e) {
      setError(e.message);
    }
  }, [token]);

  useEffect(() => {
    // Google's callback redirects to /settings?google=<outcome>; show it once and clean the URL.
    const params = new URLSearchParams(window.location.search);
    const g = params.get("google");
    if (g && OUTCOME_MESSAGES[g]) {
      setOutcome(OUTCOME_MESSAGES[g]);
      params.delete("google");
      const qs = params.toString();
      window.history.replaceState(window.history.state, "", window.location.pathname + (qs ? `?${qs}` : ""));
    }
    load();
  }, [load]);

  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api.startGoogleCalendarLink(token);
      window.location.assign(url);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  const setDetail = async (level) => {
    try {
      setStatus(await api.updateGoogleCalendarSettings(level, token));
    } catch (e) {
      setError(e.message);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.unlinkGoogleCalendar(deleteCalendar, token);
      setOutcome({
        tone: res.revoked ? "ok" : "warn",
        text: res.revoked
          ? "Disconnected. TickerTap's access to your Google account has been revoked."
          : "Disconnected here, but Google didn't confirm the revocation — you can remove TickerTap under Google Account → Security → Third-party access.",
      });
      setConfirmUnlink(false);
      setDeleteCalendar(false);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const toneColor = { ok: "var(--green)", warn: "#f59e0b", err: "var(--red)" };
  const btn = { ...S.saveBtn, opacity: busy ? 0.6 : 1 };
  const ghostBtn = {
    background: "none", border: "1px solid var(--border)", color: "var(--text)", fontFamily: "var(--font-mono)",
    fontSize: 11, letterSpacing: 1, padding: "8px 16px", borderRadius: 3, cursor: "pointer",
  };

  return (
    <div style={S.section}>
      <div style={S.sectionTitle}>GOOGLE CALENDAR</div>

      {outcome && (
        <div style={{ fontSize: 12, color: toneColor[outcome.tone], border: `1px solid ${toneColor[outcome.tone]}`, borderRadius: 3, padding: "8px 12px", marginBottom: 12 }}>
          {outcome.text}
        </div>
      )}
      {error && <div style={{ fontSize: 12, color: "var(--red)", marginBottom: 12 }}>{error}</div>}

      {!status ? (
        <div style={{ color: "var(--muted)", fontSize: 11 }}>Loading…</div>
      ) : !status.configured && !status.linked ? (
        <div style={{ color: "var(--muted)", fontSize: 12 }}>
          Not available yet — the server&apos;s Google connection hasn&apos;t been set up.
        </div>
      ) : !status.linked ? (
        <>
          <div style={{ fontSize: 12, lineHeight: 1.6, color: "var(--text)" }}>
            <div style={{ marginBottom: 8 }}>
              Show your review reminders (phase reviews, profit-target hits, earnings) in your own Google Calendar.
              Before you connect:
            </div>
            <ul style={{ margin: "0 0 10px", paddingLeft: 18 }}>
              <li>TickerTap creates one new calendar, <b>“TickerTap Reviews”</b>, in your Google account and adds events only there.</li>
              <li>Access is limited to calendars TickerTap creates — it can&apos;t see or change your other calendars or events.</li>
              <li>Events include the {DETAIL_TEXT.full}. You can switch to {DETAIL_TEXT.minimal} after connecting.</li>
              <li>
                We store only an encrypted access grant for your account. It is used for this sync and nothing else — never
                shared, sold, or read by a person.
              </li>
              <li>Disconnect any time here (this revokes access immediately) or in Google Account → Security → Third-party access.</li>
            </ul>
            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
              Google will show an &ldquo;unverified app&rdquo; notice: TickerTap is a private app that hasn&apos;t been through
              Google&apos;s optional verification review. Choose <i>Advanced → Go to TickerTap</i> to continue. Details in the{" "}
              <a href="/legal-privacy" style={{ color: "var(--cyan)" }}>privacy policy</a>.
            </div>
          </div>
          <button style={btn} disabled={busy} onClick={connect}>
            {busy ? "REDIRECTING TO GOOGLE…" : "CONNECT GOOGLE CALENDAR"}
          </button>
        </>
      ) : (
        <>
          <div style={S.row}>
            <span style={S.label}>Status</span>
            <span style={{ ...S.value, color: status.status === "active" ? "var(--green)" : "var(--red)" }}>
              {status.status === "active" ? "CONNECTED" : "NEEDS RECONNECT"}
            </span>
          </div>
          {status.last_error && (
            <div style={{ fontSize: 11, color: "var(--red)", padding: "6px 0" }}>{status.last_error}</div>
          )}
          <div style={S.row}>
            <span style={S.label}>Connected since</span>
            <span style={S.value}>{fmtDate(status.linked_at)}</span>
          </div>
          <div style={S.row}>
            <span style={S.label}>Last sync</span>
            <span style={S.value}>{status.last_sync_at ? fmtDate(status.last_sync_at) : "Not synced yet"}</span>
          </div>
          <div style={S.row}>
            <span style={S.label}>Event details</span>
            <select style={S.select} value={status.detail_level} onChange={(e) => setDetail(e.target.value)}>
              <option value="full">Full — incl. entry price &amp; gain %</option>
              <option value="minimal">Minimal — ticker &amp; review type</option>
            </select>
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: 14, flexWrap: "wrap", alignItems: "center" }}>
            {status.status !== "active" && (
              <button style={btn} disabled={busy} onClick={connect}>RECONNECT</button>
            )}
            {!confirmUnlink ? (
              <button style={{ ...ghostBtn, color: "var(--red)", borderColor: "var(--red)" }} onClick={() => setConfirmUnlink(true)}>
                DISCONNECT
              </button>
            ) : (
              <div style={{ border: "1px solid var(--red)", borderRadius: 3, padding: "10px 12px", fontSize: 12, width: "100%" }}>
                <div style={{ marginBottom: 8 }}>Disconnect Google Calendar and revoke TickerTap&apos;s access?</div>
                {status.calendar_created && (
                  <label style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, cursor: "pointer" }}>
                    <input type="checkbox" checked={deleteCalendar} onChange={(e) => setDeleteCalendar(e.target.checked)} />
                    Also delete the &ldquo;TickerTap Reviews&rdquo; calendar from my Google account
                  </label>
                )}
                <div style={{ display: "flex", gap: 8 }}>
                  <button style={{ ...ghostBtn, color: "var(--red)", borderColor: "var(--red)" }} disabled={busy} onClick={disconnect}>
                    {busy ? "DISCONNECTING…" : "YES, DISCONNECT"}
                  </button>
                  <button style={ghostBtn} onClick={() => { setConfirmUnlink(false); setDeleteCalendar(false); }}>CANCEL</button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
