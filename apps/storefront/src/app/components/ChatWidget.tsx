"use client";

// Storefront web chat widget (design ported from catering210's IIFE widget,
// translated to a self-contained React client component).
//
// Three-phase state machine: intake (client-side taps against the fetched
// script — no server round-trips) → contact (name/phone/email + honeypot +
// optional SMS consent) → chat (scripted taps POST /answer, free text POSTs
// /message, replies arrive via cursor polling).
//
// Delivery is polling, not websockets: GET /messages?after_id=<lastId> every
// 5s while the panel is open, 20s while closed, paused when the tab is
// hidden. after_id means we only ever fetch NEW rows — no flicker, no
// re-render of the transcript.
//
// Session persistence: localStorage {sid, lastId}. Only a 404 clears it — a
// 5xx or a dropped request (Meta/IG in-app browsers eat requests) is
// transient and keeps the session. All calls send no cookies.

import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
const STORAGE_KEY = "kelley_chat_session";
const POLL_OPEN_MS = 5_000;
const POLL_CLOSED_MS = 20_000;

const GREEN = "#157A33";
const GREEN_DARK = "#0F5A25";
const INK = "#1a1a1a";

interface ScriptOption {
  id: string;
  label: string;
  next?: string;
  answer?: string;
  escalate?: boolean;
}
interface ScriptQuestion {
  id: string;
  prompt: string;
  options: ScriptOption[];
}
interface ChatScript {
  version: number;
  greeting?: string;
  handoff?: string;
  root?: string;
  questions: ScriptQuestion[];
  answers?: Record<string, { body: string }>;
}
interface ChatMessage {
  id: number;
  kind: "visitor" | "auto" | "staff";
  body: string | null;
}
interface LocalLine {
  key: string;
  side: "visitor" | "brand";
  body: string;
}

function loadSession(): { sid: string; lastId: number } | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.sid === "string" && /^wc_[0-9a-f]{32}$/.test(parsed.sid)) {
      return { sid: parsed.sid, lastId: Number(parsed.lastId) || 0 };
    }
  } catch {
    /* ignore */
  }
  return null;
}

function saveSession(sid: string, lastId: number): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ sid, lastId }));
  } catch {
    /* storage blocked — the chat still works for this page load */
  }
}

function clearSession(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<"intake" | "contact" | "chat">("intake");
  const [script, setScript] = useState<ChatScript | null>(null);
  const [questionId, setQuestionId] = useState<string | null>(null);
  const [intake, setIntake] = useState<{ question: string; answer: string }[]>([]);
  const [lines, setLines] = useState<LocalLine[]>([]);
  const [unread, setUnread] = useState(0);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [contact, setContact] = useState({ name: "", phone: "", email: "", smsOptIn: false });
  const [contactError, setContactError] = useState<string | null>(null);

  const sessionRef = useRef<{ sid: string; lastId: number } | null>(null);
  const openRef = useRef(open);
  openRef.current = open;
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const firstInteractionAt = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const appendLine = useCallback((side: "visitor" | "brand", body: string) => {
    setLines((prev) => [
      ...prev,
      { key: `${prev.length}-${body.slice(0, 12)}`, side, body },
    ]);
  }, []);

  // ── polling ──────────────────────────────────────────────────────────────
  const pollNow = useCallback(async () => {
    const session = sessionRef.current;
    if (!session || document.hidden) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/web-chat/${session.sid}/messages?after_id=${session.lastId}` +
          `&page_url=${encodeURIComponent(window.location.href.slice(0, 900))}`,
        { credentials: "omit" }
      );
      if (res.status === 404) {
        // The one and only signal that the session is truly gone.
        clearSession();
        sessionRef.current = null;
        return;
      }
      if (!res.ok) return;
      const data = await res.json();
      const fresh: ChatMessage[] = data?.messages || [];
      let newLastId = session.lastId;
      let inboundCount = 0;
      for (const m of fresh) {
        if (m.id > newLastId) newLastId = m.id;
        if (m.kind === "visitor") {
          appendLine("visitor", m.body || "");
        } else {
          appendLine("brand", m.body || "");
          inboundCount += 1;
        }
      }
      if (newLastId !== session.lastId) {
        sessionRef.current = { ...session, lastId: newLastId };
        saveSession(session.sid, newLastId);
      }
      if (inboundCount > 0 && !openRef.current) {
        setUnread((u) => u + inboundCount);
      }
    } catch {
      /* transient — next tick retries */
    }
  }, [appendLine]);

  useEffect(() => {
    let cancelled = false;
    function schedule() {
      if (pollTimer.current) clearTimeout(pollTimer.current);
      pollTimer.current = setTimeout(async () => {
        if (cancelled) return;
        await pollNow();
        if (!cancelled) schedule();
      }, openRef.current ? POLL_OPEN_MS : POLL_CLOSED_MS);
    }
    schedule();
    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [pollNow, open]);

  // ── restore a prior session on mount ─────────────────────────────────────
  useEffect(() => {
    const session = loadSession();
    if (!session) return;
    sessionRef.current = { sid: session.sid, lastId: 0 }; // replay transcript
    setPhase("chat");
    void pollNow();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── script fetch on first open ───────────────────────────────────────────
  useEffect(() => {
    if (!open || script) return;
    fetch(`${API_BASE}/api/web-chat/script`, { credentials: "omit" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        const s: ChatScript | undefined = data?.script;
        if (s?.questions?.length) {
          setScript(s);
          setQuestionId((prev) => prev || s.root || s.questions[0].id);
        }
      })
      .catch(() => {});
  }, [open, script]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines, phase, questionId, open]);

  const currentQuestion =
    script?.questions.find((q) => q.id === questionId) || null;

  // ── intake taps (client-side only, pre-contact) ─────────────────────────
  function tapIntakeOption(q: ScriptQuestion, opt: ScriptOption) {
    if (firstInteractionAt.current === null) {
      firstInteractionAt.current = Date.now();
    }
    appendLine("visitor", opt.label);
    setIntake((prev) => [...prev, { question: q.prompt, answer: opt.label }]);
    const answer = opt.answer ? script?.answers?.[opt.answer]?.body : null;
    if (answer) appendLine("brand", answer);
    if (opt.next) {
      setQuestionId(opt.next);
    } else {
      // Answered or escalating — either way we now need a way to reach them.
      setPhase("contact");
    }
  }

  // ── contact submit → /start ──────────────────────────────────────────────
  async function submitContact(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!contact.phone.trim() && !contact.email.trim()) {
      setContactError("Leave a phone number or an email so we can reply.");
      return;
    }
    setBusy(true);
    setContactError(null);
    try {
      const res = await fetch(`${API_BASE}/api/web-chat/start`, {
        method: "POST",
        credentials: "omit",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: contact.name.trim() || null,
          phone: contact.phone.trim() || null,
          email: contact.email.trim() || null,
          sms_opt_in: contact.smsOptIn,
          page_url: window.location.href.slice(0, 900),
          intake,
          script_version: script?.version,
          company_website: "", // honeypot — humans never see or fill this
          elapsed_ms: firstInteractionAt.current
            ? Date.now() - firstInteractionAt.current
            : 0,
        }),
      });
      if (!res.ok) {
        setContactError(
          res.status === 422
            ? "That phone/email doesn't look right — try again."
            : "Couldn't start the chat — please try again."
        );
        return;
      }
      const data = await res.json();
      const sid: string = data.session_id;
      let lastId = 0;
      for (const m of (data.messages || []) as ChatMessage[]) {
        if (m.id > lastId) lastId = m.id;
      }
      sessionRef.current = { sid, lastId };
      saveSession(sid, lastId);
      setPhase("chat");
      appendLine(
        "brand",
        "Thanks! You're connected — ask us anything, or keep tapping through the options."
      );
    } catch {
      setContactError("Couldn't start the chat — please try again.");
    } finally {
      setBusy(false);
    }
  }

  // ── chat-phase actions ───────────────────────────────────────────────────
  async function tapChatOption(q: ScriptQuestion, opt: ScriptOption) {
    const session = sessionRef.current;
    if (!session || busy) return;
    if (opt.next) {
      appendLine("visitor", opt.label);
      setQuestionId(opt.next);
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/web-chat/${session.sid}/answer`,
        {
          method: "POST",
          credentials: "omit",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question_id: q.id, option_id: opt.id }),
        }
      );
      if (res.status === 404) {
        clearSession();
        sessionRef.current = null;
        setPhase("contact");
        return;
      }
      if (res.ok) {
        const data = await res.json();
        for (const m of (data.messages || []) as ChatMessage[]) {
          if (m.id > (sessionRef.current?.lastId || 0)) {
            sessionRef.current = { sid: session.sid, lastId: m.id };
          }
          appendLine(m.kind === "visitor" ? "visitor" : "brand", m.body || "");
        }
        if (sessionRef.current) {
          saveSession(sessionRef.current.sid, sessionRef.current.lastId);
        }
      }
    } catch {
      /* transient */
    } finally {
      setBusy(false);
      setQuestionId(script?.root || script?.questions[0]?.id || null);
    }
  }

  async function sendFreeText(e: React.FormEvent) {
    e.preventDefault();
    const session = sessionRef.current;
    const text = draft.trim();
    if (!session || !text || busy) return;
    setBusy(true);
    setDraft("");
    try {
      const res = await fetch(
        `${API_BASE}/api/web-chat/${session.sid}/message`,
        {
          method: "POST",
          credentials: "omit",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ body: text }),
        }
      );
      if (res.status === 404) {
        clearSession();
        sessionRef.current = null;
        setDraft(text); // don't eat their message
        setPhase("contact");
        return;
      }
      if (res.ok) {
        const data = await res.json();
        for (const m of (data.messages || []) as ChatMessage[]) {
          if (m.id > (sessionRef.current?.lastId || 0)) {
            sessionRef.current = { sid: session.sid, lastId: m.id };
          }
          appendLine(m.kind === "visitor" ? "visitor" : "brand", m.body || "");
        }
        if (sessionRef.current) {
          saveSession(sessionRef.current.sid, sessionRef.current.lastId);
        }
      } else {
        setDraft(text);
      }
    } catch {
      setDraft(text);
    } finally {
      setBusy(false);
    }
  }

  // ── render ───────────────────────────────────────────────────────────────
  const panelStyle: React.CSSProperties = {
    position: "fixed",
    bottom: "calc(92px + var(--mobile-cta-h, 0px))",
    right: 16,
    width: "min(360px, calc(100vw - 32px))",
    maxHeight: "min(560px, calc(100vh - 120px))",
    display: open ? "flex" : "none",
    flexDirection: "column",
    background: "#fff",
    borderRadius: 14,
    boxShadow: "0 12px 40px rgba(0,0,0,0.22)",
    overflow: "hidden",
    zIndex: 9999,
    fontFamily: "inherit",
  };

  const bubbleBase: React.CSSProperties = {
    maxWidth: "82%",
    padding: "8px 12px",
    borderRadius: 12,
    fontSize: 14,
    lineHeight: 1.45,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    marginBottom: 8,
  };

  const optionStyle: React.CSSProperties = {
    display: "block",
    width: "100%",
    textAlign: "left",
    padding: "9px 12px",
    marginBottom: 6,
    borderRadius: 10,
    border: `1.5px solid ${GREEN}`,
    background: "#fff",
    color: GREEN_DARK,
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "9px 11px",
    borderRadius: 9,
    border: "1px solid #cbd5d1",
    fontSize: 14,
    marginBottom: 8,
    boxSizing: "border-box",
  };

  return (
    <>
      {/* Launcher */}
      <button
        type="button"
        aria-label={open ? "Close chat" : "Chat with us"}
        onClick={() => {
          setOpen((o) => !o);
          setUnread(0);
          if (firstInteractionAt.current === null) {
            firstInteractionAt.current = Date.now();
          }
        }}
        style={{
          position: "fixed",
          // Lifts clear of the fixed mobile call/directions bar. The variable
          // is 0px above `lg`, where that bar is not rendered, so the bubble
          // sits exactly where it always did on desktop.
          bottom: "calc(20px + var(--mobile-cta-h, 0px))",
          right: 16,
          width: 58,
          height: 58,
          borderRadius: "50%",
          border: "none",
          background: GREEN,
          color: "#fff",
          boxShadow: "0 6px 20px rgba(21,122,51,0.45)",
          cursor: "pointer",
          zIndex: 9999,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {open ? (
          <span style={{ fontSize: 22, lineHeight: 1 }}>✕</span>
        ) : (
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H9l-4.2 3.6c-.5.4-1.3.1-1.3-.6V5.5Z"
              fill="#fff"
            />
          </svg>
        )}
        {unread > 0 && !open && (
          <span
            style={{
              position: "absolute",
              top: -2,
              right: -2,
              minWidth: 20,
              height: 20,
              borderRadius: 10,
              background: "#d32f2f",
              color: "#fff",
              fontSize: 12,
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "0 5px",
            }}
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {/* Panel */}
      <div style={panelStyle}>
        <div
          style={{
            background: GREEN,
            color: "#fff",
            padding: "12px 16px",
            fontWeight: 700,
            fontSize: 15,
          }}
        >
          Kelley Autoplex
          <div style={{ fontWeight: 400, fontSize: 12.5, opacity: 0.9 }}>
            {phase === "chat"
              ? "We reply here — keep the tab open or leave your number."
              : "Usually replies within minutes during business hours."}
          </div>
        </div>

        <div
          ref={scrollRef}
          style={{ flex: 1, overflowY: "auto", padding: "14px 14px 6px", background: "#f6f8f6" }}
        >
          {script?.greeting && lines.length === 0 && (
            <div style={{ ...bubbleBase, background: "#fff", border: "1px solid #e2e8e2", color: INK }}>
              {script.greeting}
            </div>
          )}
          {lines.map((l) => (
            <div
              key={l.key}
              style={{ display: "flex", justifyContent: l.side === "visitor" ? "flex-end" : "flex-start" }}
            >
              <div
                style={
                  l.side === "visitor"
                    ? { ...bubbleBase, background: GREEN, color: "#fff" }
                    : { ...bubbleBase, background: "#fff", border: "1px solid #e2e8e2", color: INK }
                }
              >
                {l.body}
              </div>
            </div>
          ))}
        </div>

        <div style={{ padding: "10px 14px 14px", background: "#f6f8f6" }}>
          {phase !== "contact" && currentQuestion && (
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: "#3c4a3f", margin: "4px 0 8px" }}>
                {currentQuestion.prompt}
              </div>
              {currentQuestion.options.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  style={optionStyle}
                  disabled={busy}
                  onClick={() =>
                    phase === "intake"
                      ? tapIntakeOption(currentQuestion, opt)
                      : tapChatOption(currentQuestion, opt)
                  }
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}

          {phase === "contact" && (
            <form onSubmit={submitContact}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "#3c4a3f", margin: "4px 0 8px" }}>
                Where should we reach you?
              </div>
              <input
                style={inputStyle}
                placeholder="Name"
                value={contact.name}
                onChange={(e) => setContact((c) => ({ ...c, name: e.target.value }))}
                autoComplete="name"
              />
              <input
                style={inputStyle}
                placeholder="Phone"
                type="tel"
                value={contact.phone}
                onChange={(e) => setContact((c) => ({ ...c, phone: e.target.value }))}
                autoComplete="tel"
              />
              <input
                style={inputStyle}
                placeholder="Email"
                type="email"
                value={contact.email}
                onChange={(e) => setContact((c) => ({ ...c, email: e.target.value }))}
                autoComplete="email"
              />
              {/* Honeypot: visually hidden, never labeled, tab-skipped. */}
              <input
                style={{ position: "absolute", left: -9999, width: 1, height: 1, opacity: 0 }}
                tabIndex={-1}
                aria-hidden
                autoComplete="off"
                name="company_website"
              />
              <label style={{ display: "flex", gap: 8, fontSize: 12, color: "#4a5a4e", marginBottom: 8 }}>
                <input
                  type="checkbox"
                  checked={contact.smsOptIn}
                  onChange={(e) => setContact((c) => ({ ...c, smsOptIn: e.target.checked }))}
                  style={{ marginTop: 2 }}
                />
                <span>
                  It&apos;s OK to text me about my inquiry. Msg &amp; data rates may
                  apply. Reply STOP to opt out.
                </span>
              </label>
              {contactError && (
                <div style={{ color: "#c62828", fontSize: 12.5, marginBottom: 8 }}>{contactError}</div>
              )}
              <button
                type="submit"
                disabled={busy}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  borderRadius: 10,
                  border: "none",
                  background: GREEN,
                  color: "#fff",
                  fontWeight: 700,
                  fontSize: 14,
                  cursor: "pointer",
                  opacity: busy ? 0.7 : 1,
                }}
              >
                {busy ? "Connecting…" : "Start chat"}
              </button>
            </form>
          )}

          {phase === "chat" && (
            <form onSubmit={sendFreeText} style={{ display: "flex", gap: 8, marginTop: 4 }}>
              <input
                style={{ ...inputStyle, marginBottom: 0, flex: 1 }}
                placeholder="Type a message…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={2000}
              />
              <button
                type="submit"
                disabled={busy || !draft.trim()}
                aria-label="Send"
                style={{
                  width: 42,
                  borderRadius: 9,
                  border: "none",
                  background: GREEN,
                  color: "#fff",
                  cursor: "pointer",
                  fontSize: 17,
                  opacity: busy || !draft.trim() ? 0.6 : 1,
                }}
              >
                ➤
              </button>
            </form>
          )}
        </div>
      </div>
    </>
  );
}
