/**
 * LegalPage.jsx — Legal information page for TickerTap.
 *
 * Renders three tabbed sections: Financial Disclaimer, Privacy Policy,
 * and Terms of Service. Operates in two modes:
 *
 *  - Standalone (prop `standalone`): Full-viewport layout with its own
 *    header and back button, used when accessed from auth pages
 *    (login, register, etc.) before the user is authenticated.
 *
 *  - In-shell (default): Standard page-scroll layout rendered inside
 *    AppShell for authenticated users navigating from the footer.
 *
 * All legal content is defined as internal sub-components
 * (DisclaimerContent, PrivacyContent, TermsContent) to keep the
 * top-level component focused on layout and tab switching.
 */

import { useState, useEffect } from "react";

/* ── Tab definitions ─────────────────────────────────────────────────────── */
const TABS = [
  { id: "disclaimer", label: "FINANCIAL DISCLAIMER" },
  { id: "privacy",    label: "PRIVACY POLICY" },
  { id: "terms",      label: "TERMS OF SERVICE" },
  { id: "trading-ai", label: "TRADING AI" },
];

/* ═══════════════════════════════════════════════════════════════════════════
   CONTENT COMPONENTS — one per legal section
═══════════════════════════════════════════════════════════════════════════ */

/**
 * DisclaimerContent — Financial disclaimer section.
 * Covers: not financial advice, AI scoring caveats, market data source,
 * past performance, and assumption of risk.
 */
function DisclaimerContent() {
  return (
    <div className="legal-content">
      <div className="legal-effective-date">EFFECTIVE DATE: MARCH 5, 2026</div>

      <section className="legal-section">
        <div className="legal-section-title">NOT FINANCIAL ADVICE</div>
        <p className="legal-text">
          TickerTap is a portfolio tracking and informational tool only. Nothing
          displayed on this platform &mdash; including market data, portfolio
          analytics, charts, and AI-generated scores &mdash; constitutes financial
          advice, investment recommendations, or solicitations to buy, sell, or
          hold any security or financial instrument.
        </p>
        <p className="legal-text">
          TickerTap is not a registered broker-dealer, investment adviser, or
          financial institution. The operator of this platform is not licensed to
          provide financial services or investment guidance.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">AI-GENERATED CONTENT</div>
        <p className="legal-text">
          TickerTap uses a local large language model (Llama 3 via Ollama) to
          generate impact scores and analysis for financial news articles. These
          scores are algorithmic outputs, not expert opinions. They are produced
          automatically without human review and may be inaccurate, incomplete,
          or misleading.
        </p>
        <ul className="legal-list">
          <li>AI scores reflect pattern matching, not market expertise or insider knowledge</li>
          <li>Scores may not account for breaking developments, corrections, or context</li>
          <li>Model outputs should never be the sole basis for any investment decision</li>
          <li>
            Scores range from &minus;5 (bearish) to +5 (bullish) and represent the
            model&apos;s interpretation of news sentiment, not a prediction of price movement
          </li>
        </ul>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">MARKET DATA</div>
        <p className="legal-text">
          Market data displayed on TickerTap is sourced from Yahoo Finance and is
          provided on an as-is basis. Data may be delayed, inaccurate, or
          incomplete. TickerTap does not guarantee the timeliness, accuracy, or
          completeness of any market data.
        </p>
        <p className="legal-text">
          Real-time quotes are subject to Yahoo Finance&apos;s data availability
          and may not reflect actual market prices at the time of viewing.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">PAST PERFORMANCE</div>
        <p className="legal-text">
          Past performance of any security, portfolio, or strategy displayed on
          TickerTap is not indicative of future results. Historical data and
          portfolio performance metrics are shown for informational purposes only
          and should not be interpreted as a guarantee of future returns.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">ASSUMPTION OF RISK</div>
        <p className="legal-text">
          All investment decisions are made at your own risk. You acknowledge that
          investing in securities involves risk of loss, including the potential
          loss of principal. TickerTap shall not be liable for any financial
          losses incurred as a result of using this platform or relying on
          information displayed herein.
        </p>
        <p className="legal-text">
          You are solely responsible for conducting your own due diligence and
          consulting with a qualified financial professional before making any
          investment decisions.
        </p>
      </section>
    </div>
  );
}

/**
 * PrivacyContent — Privacy policy section.
 * Covers: data collected, usage, storage, third-party sharing,
 * retention, user rights, and cookies/local storage.
 */
function PrivacyContent() {
  return (
    <div className="legal-content">
      <div className="legal-effective-date">EFFECTIVE DATE: SEPTEMBER 24, 2026</div>

      <section className="legal-section">
        <div className="legal-section-title">OVERVIEW</div>
        <p className="legal-text">
          TickerTap is a privately operated, self-hosted application. Your data is
          stored on infrastructure controlled by the platform operator. This policy
          describes what data is collected, how it is used, which outside services
          receive any of it, and your rights regarding that data.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">DATA WE COLLECT</div>

        <div className="legal-subsection-title">Account Information</div>
        <ul className="legal-list">
          <li>Email address (authentication, password recovery, account notices)</li>
          <li>First and last name (display)</li>
          <li>Hashed password (Argon2id &mdash; your plaintext password is never stored)</li>
          <li>Preferences you set (currency, language, interface options)</li>
        </ul>

        <div className="legal-subsection-title">Portfolio and Research Data</div>
        <ul className="legal-list">
          <li>Portfolios, holdings (ticker symbols, quantities, purchase prices, dates, stop levels)</li>
          <li>Transactions, orders, watchlists and alerts you create</li>
          <li>Review reminders generated from your positions, and the daily model predictions, grades and lessons derived from them</li>
        </ul>

        <div className="legal-subsection-title">Connected Services (only if you link them)</div>
        <ul className="legal-list">
          <li>Telegram: your Telegram chat ID, so notifications reach you</li>
          <li>Google Calendar: an encrypted Google access grant and the ID of the calendar TickerTap creates (see GOOGLE USER DATA below)</li>
        </ul>

        <div className="legal-subsection-title">Usage and Security Data</div>
        <ul className="legal-list">
          <li>Authentication timestamps and session activity</li>
          <li>Audit log entries for security-relevant actions (logins, password changes, linking or unlinking connected services, account deletion requests), including IP address and browser user agent</li>
        </ul>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">HOW WE USE YOUR DATA</div>
        <ul className="legal-list">
          <li>To authenticate you and keep your session secure</li>
          <li>To show your portfolio, holdings, transactions and research tools</li>
          <li>To fetch market data and news for the securities you hold or watch</li>
          <li>To send the alerts, daily briefings and review reminders you have enabled</li>
          <li>To generate AI analysis, daily predictions and their post-close reflections</li>
          <li>To send account emails (password reset, verification, deletion notices)</li>
        </ul>
        <p className="legal-text">
          TickerTap does not use your data for advertising, profiling for others,
          or marketing, and does not sell or rent it.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">AI PROCESSING</div>
        <p className="legal-text">
          News scoring, daily predictions, reflections and insider-activity analysis
          are produced by language models running on hardware controlled by the
          platform operator (a local Ollama server and the operator&apos;s self-hosted
          assistant service). Your portfolio data is not sent to any external AI
          provider. AI output is informational and is not financial advice.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">SERVICES THAT RECEIVE DATA</div>
        <p className="legal-text">
          Beyond the operator&apos;s own servers, these services receive data, each
          only for the purpose stated:
        </p>
        <ul className="legal-list">
          <li>
            <strong>Cloudflare:</strong> all traffic to TickerTap passes through
            Cloudflare&apos;s network (TLS termination and tunnelling to the
            operator&apos;s server), so Cloudflare processes requests in transit
          </li>
          <li>
            <strong>Yahoo Finance:</strong> ticker symbols are sent to retrieve market
            data. No personal information is transmitted
          </li>
          <li>
            <strong>Telegram (if you link it):</strong> alerts and briefings, which
            contain tickers, prices, position details and model calls, are delivered
            through Telegram&apos;s servers to your chat
          </li>
          <li>
            <strong>Google Calendar (if you link it):</strong> review reminders are
            written to a calendar in your own Google account &mdash; see below
          </li>
          <li>
            <strong>Proton Mail:</strong> account emails are sent through the
            operator&apos;s Proton Mail account, so your email address and the
            message content pass through Proton
          </li>
        </ul>
        <p className="legal-text">
          Push notifications (ntfy) and the AI services run on servers controlled by
          the platform operator.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">GOOGLE USER DATA</div>
        <p className="legal-text">
          If you choose to connect Google Calendar in Settings, TickerTap requests a
          single permission,{" "}
          <em>&ldquo;Make secondary Google calendars, and see, create, change, and delete events on them&rdquo;</em>{" "}
          (scope <code>calendar.app.created</code>).
        </p>
        <ul className="legal-list">
          <li>
            <strong>What we access:</strong> only the &ldquo;TickerTap Reviews&rdquo;
            calendar TickerTap creates in your account and the events on it. We do
            not read your other calendars or events
          </li>
          <li>
            <strong>What we store:</strong> an OAuth refresh token (encrypted with
            AES-256-GCM, with the key held outside the database), the granted scope,
            the ID of the TickerTap calendar and the IDs of events we created.
            Access tokens are kept in memory only
          </li>
          <li>
            <strong>What we write:</strong> your review reminders (phase reviews,
            profit-target events, earnings). By default an event includes the
            ticker, review type, rule text, entry price and percentage gain; you can
            switch to ticker and review type only in Settings
          </li>
          <li>
            <strong>How it is used:</strong> solely to keep that calendar in sync
            with your reminders. Google user data is not used for advertising, is not
            sold or transferred to anyone, is not used to train AI models, and is not
            read by a person
          </li>
          <li>
            <strong>Retention and removal:</strong> disconnecting in Settings, or
            deleting your TickerTap account, revokes the grant with Google and
            deletes the stored token immediately. You can also remove access in your
            Google Account under Security &rarr; Third-party access. Events already
            written stay in your calendar unless you choose to delete the TickerTap
            calendar when disconnecting
          </li>
        </ul>
        <p className="legal-text">
          TickerTap&apos;s use and transfer of information received from Google APIs
          adheres to the{" "}
          <a
            href="https://developers.google.com/terms/api-services-user-data-policy"
            target="_blank"
            rel="noopener noreferrer"
          >
            Google API Services User Data Policy
          </a>
          , including the Limited Use requirements.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">DATA STORAGE AND SECURITY</div>
        <ul className="legal-list">
          <li>Data is stored in a PostgreSQL database on the operator&apos;s self-hosted infrastructure</li>
          <li>All connections use TLS encryption in transit</li>
          <li>Passwords are hashed with Argon2id; third-party access tokens are encrypted at rest</li>
          <li>Access tokens are kept in your browser&apos;s sessionStorage (cleared when the tab closes) and expire automatically</li>
          <li>An inactivity timer logs you out after 5 minutes of inactivity</li>
        </ul>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">DATA RETENTION</div>
        <p className="legal-text">
          Your data is kept while your account exists. When you request deletion,
          connected services are disconnected immediately and your account is
          scheduled for permanent deletion after 30 days (you can cancel within that
          window), or deleted immediately if you choose permanent deletion. Deletion
          removes your portfolio data, transactions, predictions, reminders and
          connected-service tokens.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">YOUR RIGHTS</div>
        <ul className="legal-list">
          <li><strong>Access and export:</strong> view your data in the application; contact the operator for a full copy</li>
          <li><strong>Correction:</strong> update your profile and portfolio data in the application</li>
          <li><strong>Deletion:</strong> delete your account yourself in Settings, as described above</li>
          <li><strong>Withdraw consent:</strong> disconnect Telegram or Google Calendar at any time</li>
          <li><strong>Questions or complaints:</strong> contact the platform operator; if you are in the EU you may also contact your data protection authority</li>
        </ul>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">COOKIES AND LOCAL STORAGE</div>
        <p className="legal-text">
          TickerTap sets one first-party, httpOnly cookie that holds your session
          refresh token; it is sent only to TickerTap&apos;s authentication
          endpoints. Your access token is kept in sessionStorage, and localStorage is
          used for inactivity tracking and interface preferences. No tracking,
          analytics or third-party cookies are used.
        </p>
      </section>
    </div>
  );
}

/**
 * TermsContent — Terms of service section.
 * Covers: acceptance, service description, account responsibilities,
 * acceptable use, IP, availability, liability, warranties,
 * termination, changes, governing law, and contact.
 */
function TermsContent() {
  return (
    <div className="legal-content">
      <div className="legal-effective-date">EFFECTIVE DATE: MARCH 5, 2026</div>

      <section className="legal-section">
        <div className="legal-section-title">ACCEPTANCE OF TERMS</div>
        <p className="legal-text">
          By accessing or using TickerTap, you agree to be bound by these Terms of
          Service. If you do not agree to these terms, you must not use the
          platform.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">SERVICE DESCRIPTION</div>
        <p className="legal-text">
          TickerTap is a self-hosted portfolio tracking application that provides
          market data visualization, portfolio management, and AI-scored financial
          news. It is not a brokerage, trading platform, or financial advisory
          service. No actual securities transactions are executed through TickerTap.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">ACCOUNT RESPONSIBILITIES</div>
        <ul className="legal-list">
          <li>You must provide accurate information when creating an account</li>
          <li>You are responsible for maintaining the security of your login credentials</li>
          <li>You must not share your account with others</li>
          <li>You must notify the platform operator immediately of any unauthorized access</li>
          <li>You must be at least 18 years of age to use this service</li>
        </ul>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">ACCEPTABLE USE</div>
        <p className="legal-text">
          You agree to use TickerTap only for its intended purpose as a portfolio
          tracking tool. You must not:
        </p>
        <ul className="legal-list">
          <li>Attempt to gain unauthorized access to the platform or its infrastructure</li>
          <li>Use automated tools to scrape data or overload the service</li>
          <li>Circumvent rate limits or security measures</li>
          <li>Use the platform for any unlawful purpose</li>
          <li>Interfere with other users&apos; access to the service</li>
          <li>Reverse engineer, decompile, or attempt to extract the source code</li>
        </ul>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">INTELLECTUAL PROPERTY</div>
        <p className="legal-text">
          The TickerTap name, logo, design, and source code are the property of the
          platform operator. The Bloomberg-inspired visual design is an original
          work and does not imply any affiliation with Bloomberg L.P.
        </p>
        <p className="legal-text">
          Market data displayed is sourced from Yahoo Finance and is subject to
          Yahoo Finance&apos;s own terms of service. AI-generated content is
          produced by open-source language models and is not claimed as proprietary
          analysis.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">SERVICE AVAILABILITY</div>
        <p className="legal-text">
          TickerTap is provided on an &ldquo;as is&rdquo; and &ldquo;as
          available&rdquo; basis. The platform operator does not guarantee
          uninterrupted, error-free, or secure operation of the service. The
          service may be modified, suspended, or discontinued at any time without
          prior notice.
        </p>
        <ul className="legal-list">
          <li>Market data availability depends on Yahoo Finance API uptime</li>
          <li>AI scoring depends on the availability of the local Ollama instance</li>
          <li>Scheduled or unscheduled maintenance may cause temporary service interruptions</li>
        </ul>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">LIMITATION OF LIABILITY</div>
        <p className="legal-text">
          To the maximum extent permitted by applicable law, the platform operator
          shall not be liable for any indirect, incidental, special, consequential,
          or punitive damages, including but not limited to:
        </p>
        <ul className="legal-list">
          <li>Financial losses resulting from investment decisions influenced by platform data</li>
          <li>Loss of data due to system failures or security incidents</li>
          <li>Service interruptions or unavailability</li>
          <li>Inaccuracies in market data, portfolio calculations, or AI-generated scores</li>
          <li>Unauthorized access to your account due to compromised credentials</li>
        </ul>
        <p className="legal-text">
          The platform operator&apos;s total liability for any claims arising from
          use of the service shall not exceed the amount you have paid for the
          service (which, for a free application, is zero).
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">DISCLAIMER OF WARRANTIES</div>
        <p className="legal-text">
          TickerTap is provided without warranties of any kind, whether express or
          implied, including but not limited to implied warranties of
          merchantability, fitness for a particular purpose, and non-infringement.
          The platform operator does not warrant that market data is accurate, that
          AI scores are reliable, or that the service will meet your requirements.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">ACCOUNT TERMINATION</div>
        <p className="legal-text">
          The platform operator reserves the right to suspend or terminate your
          account at any time for violation of these terms or for any other reason.
          You may delete your account at any time by contacting the platform
          operator.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">CHANGES TO TERMS</div>
        <p className="legal-text">
          These terms may be updated at any time. Continued use of the platform
          after changes are posted constitutes acceptance of the modified terms.
          Material changes will be communicated through the platform interface
          where practical.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">GOVERNING LAW</div>
        <p className="legal-text">
          These terms shall be governed by and construed in accordance with the
          laws of the jurisdiction in which the platform is operated, without
          regard to conflict of law provisions.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-section-title">CONTACT</div>
        <p className="legal-text">
          For questions regarding these terms, privacy concerns, or account
          deletion requests, contact the platform operator at the email address
          provided during platform setup.
        </p>
      </section>
    </div>
  );
}

/**
 * TradingAIDisclaimerContent — Trading AI algorithm disclaimer.
 * Covers: educational purposes, not financial advice, no guarantee of profits,
 * backtest limitations, algorithmic risks.
 */
function TradingAIDisclaimerContent() {
  return (
    <div className="legal-content">
      <div className="legal-effective-date">EFFECTIVE: MARCH 10, 2026</div>

      <section className="legal-section">
        <div className="legal-section-title">Trading AI Disclaimer</div>
        <p className="legal-text">
          The Trading AI feature of TickerTap provides algorithmic backtesting and signal
          generation tools <strong>for educational and informational purposes only</strong>.
          It does <strong>not</strong> constitute financial advice, investment recommendations,
          or an offer to buy or sell any security.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-subsection-title">No Guarantee of Results</div>
        <p className="legal-text">
          Past performance of any strategy, whether backtested or forward-tested, does
          <strong> not guarantee future results</strong>. Trading in financial markets involves
          substantial risk of loss. You should not invest money you cannot afford to lose.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-subsection-title">Backtest Limitations</div>
        <p className="legal-text">
          Backtests are simulations run on historical data and are subject to inherent limitations
          including but not limited to:
        </p>
        <ul className="legal-list">
          <li><strong>Survivorship bias</strong> — historical data may not include delisted securities.</li>
          <li><strong>Look-ahead bias</strong> — strategies may inadvertently use future information.</li>
          <li><strong>Overfitting</strong> — strategies optimised to historical data may fail on new data.</li>
          <li><strong>Slippage and liquidity</strong> — simulated fills may not reflect real market conditions.</li>
          <li><strong>Commission estimates</strong> — actual trading costs may differ from simulation parameters.</li>
        </ul>
      </section>

      <section className="legal-section">
        <div className="legal-subsection-title">Algorithmic Risk</div>
        <p className="legal-text">
          Algorithmic trading strategies may produce unexpected results due to market regime
          changes, data quality issues, software bugs, or parameter sensitivity. TickerTap
          provides an "overfit score" as a risk indicator but this metric is
          <strong> not a guarantee</strong> of strategy robustness.
        </p>
      </section>

      <section className="legal-section">
        <div className="legal-subsection-title">Your Responsibility</div>
        <p className="legal-text">
          By using the Trading AI feature you acknowledge that: (a) you are solely responsible
          for any trading decisions you make; (b) TickerTap and its operators bear no liability
          for losses incurred; (c) you have read and understood this disclaimer in its entirety.
        </p>
        <p className="legal-text">
          <strong>If you do not agree with these terms, do not use the Trading AI feature.</strong>
        </p>
      </section>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════

/**
 * LegalPage — Tabbed legal information page.
 *
 * @param {object}   props
 * @param {string}   props.initialTab  - "disclaimer" | "privacy" | "terms"
 * @param {Function} props.onBack      - Navigation callback (returns to previous page)
 * @param {boolean}  [props.standalone] - If true, renders full-viewport layout
 *                                         for unauthenticated users
 * @returns {JSX.Element}
 */
export function LegalPage({ initialTab = "disclaimer", onBack, standalone }) {
  const [activeTab, setActiveTab] = useState(initialTab);

  /* Sync active tab when navigated via footer links while already mounted */
  useEffect(() => { setActiveTab(initialTab); }, [initialTab]);

  /* ── Shared tab bar (used by both render modes) ──────────────────────── */
  const tabBar = (
    <div className="legal-tabs">
      {TABS.map(t => (
        <button
          key={t.id}
          className={`legal-tab${activeTab === t.id ? " active" : ""}`}
          onClick={() => setActiveTab(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );

  /* ── Shared content area ─────────────────────────────────────────────── */
  const content = (
    <div className="page-inner">
      {activeTab === "disclaimer" && <DisclaimerContent />}
      {activeTab === "privacy"    && <PrivacyContent />}
      {activeTab === "terms"      && <TermsContent />}
      {activeTab === "trading-ai" && <TradingAIDisclaimerContent />}
    </div>
  );

  /* ── Standalone mode (unauthenticated — full-viewport) ───────────────── */
  if (standalone) {
    return (
      <div className="legal-standalone">
        <div className="legal-standalone-header">
          <span
            style={{
              fontFamily: "var(--font-disp)",
              fontSize: 22,
              color: "var(--amber)",
              letterSpacing: 2,
            }}
          >
            TT
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--muted)",
              letterSpacing: 1,
            }}
          >
            TICKER-TAP
          </span>
          <button className="legal-back-btn" onClick={onBack}>
            &larr; BACK TO SIGN IN
          </button>
        </div>
        {tabBar}
        <div className="legal-standalone-body">
          {content}
        </div>
      </div>
    );
  }

  /* ── In-shell mode (authenticated — standard page layout) ────────────── */
  return (
    <div className="page-scroll">
      <div className="page-header">
        <div>
          <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button
              className="btn btn-ghost"
              onClick={onBack}
              style={{ padding: "4px 8px", fontSize: 14 }}
            >
              &larr;
            </button>
            LEGAL
          </div>
          <div className="page-sub">
            FINANCIAL DISCLAIMER &middot; PRIVACY POLICY &middot; TERMS OF SERVICE
          </div>
        </div>
      </div>
      {tabBar}
      {content}
    </div>
  );
}
