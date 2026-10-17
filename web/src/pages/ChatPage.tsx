// Chat with the campaign's assistant. Threads are private to you; answers cite your visible
// notes (N), SRD rules (E) and tools (T). New answers are announced to screen readers.
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { AnswerText } from "../answer";
import { Drawer } from "../components/Drawer";
import { EntryPanel } from "../components/EntryPanel";
import { message, useCampaign, useLoad } from "../hooks";
import { PlusIcon, SendIcon } from "../icons";
import type { Citation, Message, Thread } from "../types";

const STARTERS = [
  "What do we know about the baron?",
  "Summarise the last session",
  "What are the odds a +5 attack hits AC 15 with advantage?",
  "How does grappling work?",
];

export function ChatPage() {
  const { campaign } = useCampaign();
  const { tid } = useParams();
  const navigate = useNavigate();
  const threadId = tid ? Number(tid) : null;
  const base = `/c/${campaign.id}/chat`;
  const { data: threads, reload: reloadThreads } = useLoad(() => api.threads(campaign.id), [campaign.id]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  // Which thread is waiting for an answer ("new" while its thread is being created). Busy UI
  // shows only in that thread, so switching away never blocks another thread (C7).
  const [pendingFor, setPendingFor] = useState<number | "new" | null>(null);
  const busy = pendingFor !== null && (pendingFor === threadId || (pendingFor === "new" && threadId === null));
  const [error, setError] = useState<string | null>(null);
  const [openEntry, setOpenEntry] = useState<number | null>(null);
  const [source, setSource] = useState<Citation | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const end = useRef<HTMLDivElement>(null);
  // A thread this page just created already has its messages on screen; reloading it would
  // race the pending answer and wipe the first question (found in the browser walk-through).
  const created = useRef<number | null>(null);
  // The thread on screen right now, so a late answer can't land in another thread (C2).
  const current = useRef<number | null>(threadId);
  useEffect(() => {
    // Only when the open thread really changes: a render between creating a thread and the URL
    // catching up must not reset it, or the first answer would be dropped.
    current.current = threadId;
  }, [threadId]);

  useEffect(() => {
    if (threadId !== null && threadId === created.current) return;
    created.current = null; // left the new thread: from now on it reloads normally (C1)
    setMessages([]);
    setError(null);
    if (threadId === null) return;
    let live = true; // C6: a slow load of a thread you've left is ignored
    api
      .thread(campaign.id, threadId)
      .then((t) => live && setMessages(t.messages))
      .catch((e) => live && setError(message(e)));
    return () => {
      live = false;
    };
  }, [campaign.id, threadId]);

  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect must
    // return nothing or a cleanup function.
    end.current?.scrollIntoView({ block: "end" });
  }, [messages, busy]);

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    let target: number | null = threadId;
    setPendingFor(target ?? "new");
    setError(null);
    const done = () => setPendingFor((p) => (p === target || p === "new" ? null : p));
    try {
      if (target === null) {
        const thread = await api.createThread(campaign.id, q.slice(0, 60));
        target = thread.id;
        reloadThreads();
        if (current.current !== null) {
          // The user moved to another thread meanwhile: don't pull them back (re-review).
          setPendingFor((p) => (p === "new" ? null : p));
          await api.ask(campaign.id, target, q).catch(() => undefined);
          return;
        }
        setPendingFor(thread.id);
        created.current = thread.id;
        current.current = thread.id;
        navigate(`${base}/${thread.id}`, { replace: true });
      }
      const pending: Message = {
        id: -Date.now(), role: "user", text: q, created_at: new Date().toISOString(),
        citations: [], confidence: null, insufficient_evidence: null, stop_reason: null,
      };
      setMessages((m) => [...m, pending]);
      setText("");
      const reply = await api.ask(campaign.id, target, q);
      if (current.current === target) {
        setMessages((m) => [...m, reply]);
        setAnnouncement(`Answer: ${reply.text.slice(0, 300)}`);
      }
    } catch (e) {
      if (current.current === target) setError(message(e));
    } finally {
      done();
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    send(text);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(text);
    }
  };

  // UB5: tidy up the thread list.
  const rename = async (t: Thread) => {
    const title = window.prompt("Rename chat", t.title)?.trim();
    if (!title || title === t.title) return;
    try {
      await api.renameThread(campaign.id, t.id, title);
      reloadThreads();
    } catch (e) {
      setError(message(e));
    }
  };

  const remove = async (t: Thread) => {
    if (!window.confirm(`Delete the chat “${t.title}”? This can't be undone.`)) return;
    try {
      await api.deleteThread(campaign.id, t.id);
      if (t.id === threadId) navigate(base, { replace: true });
      reloadThreads();
    } catch (e) {
      setError(message(e));
    }
  };

  const onCitation = (c: Citation) => {
    if (c.kind === "note" && c.entry_id != null) setOpenEntry(c.entry_id);
    else setSource(c);
  };

  return (
    <section className="chat">
      <aside className="thread-list" aria-label="Your chats">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => navigate(base)}
        >
          <PlusIcon /> New chat
        </button>
        <p className="muted small">Only you can see your chats.</p>
        <ul>
          {threads?.map((t) => (
            <li key={t.id} className="thread-row">
              <button
                type="button"
                className={t.id === threadId ? "thread active" : "thread"}
                aria-current={t.id === threadId ? "page" : undefined}
                onClick={() => navigate(`${base}/${t.id}`)}
              >
                {t.title}
              </button>
              <span className="thread-actions">
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Rename ${t.title}`}
                  title="Rename"
                  onClick={() => rename(t)}
                >
                  ✎
                </button>
                <button
                  type="button"
                  className="icon-button danger"
                  aria-label={`Delete ${t.title}`}
                  title="Delete"
                  onClick={() => remove(t)}
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      </aside>

      <div className="conversation">
        <label className="thread-picker">
          <span className="sr-only">Switch chat</span>
          <select
            aria-label="Switch chat"
            value={threadId ?? ""}
            onChange={(e) => navigate(e.target.value ? `${base}/${e.target.value}` : base)}
          >
            <option value="">New chat</option>
            {threads?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
        </label>
        <div className="messages" role="log" aria-label="Conversation">
          {messages.length === 0 && !busy && (
            <div className="chat-empty">
              <h1>Ask about {campaign.name}</h1>
              <p className="muted">
                Answers come from the notes you can see and the 2024 rules (SRD 5.2.1), with
                sources you can open.
              </p>
              <div className="starters">
                {STARTERS.map((s) => (
                  <button key={s} type="button" className="chip" onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`message message-${m.role}`}>
              {m.role === "user" ? (
                <p>{m.text}</p>
              ) : (
                <>
                  <AnswerText text={m.text} citations={m.citations} onCitation={onCitation} />
                  {m.insufficient_evidence && m.stop_reason === "completed" && (
                    <p className="note">Your notes and the SRD didn't fully cover this.</p>
                  )}
                </>
              )}
            </div>
          ))}
          {busy && (
            <div className="message message-assistant thinking" aria-hidden="true">
              <span /> <span /> <span />
            </div>
          )}
          <div ref={end} />
        </div>
        <p className="sr-only" aria-live="polite">
          {busy ? "Thinking…" : announcement}
        </p>
        {error && <p className="error" role="alert">{error}</p>}
        <form className="composer" onSubmit={onSubmit}>
          <label className="sr-only" htmlFor="chat-input">
            Your question
          </label>
          <textarea
            id="chat-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about your campaign or the rules…"
            maxLength={2000}
            rows={2}
          />
          <button className="btn btn-primary" disabled={busy || !text.trim()} aria-label="Send">
            <SendIcon />
          </button>
        </form>
      </div>

      {openEntry !== null && <EntryPanel entryId={openEntry} onClose={() => setOpenEntry(null)} />}
      {source && (
        <Drawer title={source.kind === "srd" ? "Rules source" : "Tool result"} onClose={() => setSource(null)}>
          <p>
            <strong>{source.id}</strong>: {source.label}
          </p>
          {source.kind === "srd" && (
            <p className="muted">From the System Reference Document 5.2.1 (CC-BY-4.0).</p>
          )}
        </Drawer>
      )}
    </section>
  );
}
