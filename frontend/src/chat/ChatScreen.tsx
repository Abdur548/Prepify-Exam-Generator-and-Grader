import { useEffect, useRef, useState } from "react";
import type { ChatAnswer, Turn } from "./types";
import { Blocked, Unreachable } from "../system/Blocked";
import { blockers, usePreflight } from "../system/usePreflight";
import { useTopics } from "./useTopics";
import "./chat.css";

/**
 * Ask your own notes.
 *
 * The whole screen turns on one boolean. `from_material: true` means the answer
 * was drawn from pages the student uploaded, and it gets the trace mark and its
 * citations. `from_material: false` means it came from the model's general
 * knowledge — still worth reading, but the student cannot go and check it, so it
 * is rendered plainly and labelled as such.
 *
 * That distinction is the product. Blurring it would leave a revision tool whose
 * answers a student has no way to trust, which is the thing every competitor
 * already is.
 *
 * ## What it does not do
 *
 * Claim an answer is correct. The citations say *the answer was written from this
 * page*, never *this page proves it* — nothing in the system checks a claim
 * against a source (R6). The copy says "drawn from", and stays there.
 */

/** Kept short deliberately — see `send`. */
const HISTORY_TURNS = 6;

export function ChatScreen() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const nextId = useRef(1);
  const tail = useRef<HTMLDivElement>(null);
  const preflight = usePreflight();
  // Chat needs no output directory and no PDF renderer, so it is not blocked on
  // them. Saying "you can't ask questions" because WeasyPrint is missing would
  // be a false statement about what is broken.
  const stopped = blockers(preflight.checks, "chat");
  const topics = useTopics(3);

  useEffect(() => {
    tail.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || pending) return;

    const id = nextId.current++;
    setTurns((t) => [...t, { id, question: q, answer: null }]);
    setDraft("");
    setPending(true);

    // Only completed pairs, and only the last few. The endpoint drops malformed
    // pairs silently, so sending a half-finished turn would lose it without a
    // word; and every turn sent is tokens spent on the student's quota.
    const history = turns
      .filter((t): t is Turn & { answer: ChatAnswer } => t.answer !== null)
      .slice(-HISTORY_TURNS)
      .map((t) => [t.question, t.answer.answer]);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, history }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setTurns((t) =>
          t.map((x) =>
            x.id === id
              ? {
                  ...x,
                  error: body.detail || `HTTP ${res.status}`,
                  // 409 is the server being busy indexing or generating — a
                  // temporary state with a clear remedy, not a broken question.
                  busy: res.status === 409,
                }
              : x,
          ),
        );
        return;
      }
      setTurns((t) =>
        t.map((x) => (x.id === id ? { ...x, answer: body as ChatAnswer } : x)),
      );
    } catch (e) {
      setTurns((t) =>
        t.map((x) =>
          x.id === id
            ? { ...x, error: e instanceof Error ? e.message : String(e) }
            : x,
        ),
      );
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="chat">
      <header className="chat__head">
        <h1 className="chat__title">Ask your notes</h1>
        <p className="chat__sub">
          Answers drawn from your uploads carry the page they came from. Anything
          that isn&rsquo;t says so.
        </p>
      </header>

      {turns.length === 0 ? (
        <div className="chat__empty">
          {/* Drawn from the corpus that is actually loaded. Three hardcoded
              questions about A* search used to sit here — right for the deck this
              was built against, nonsense to anyone who uploaded chemistry. When
              there is no corpus there are no examples, rather than invented
              ones. */}
          {topics.length > 0 ? (
            <>
              <p>Your material covers these. Try one, or ask your own.</p>
              <div className="chat__suggest">
                {topics.map((t) => (
                  <button
                    key={t}
                    type="button"
                    disabled={pending}
                    onClick={() => void send(`Explain ${t}`)}
                  >
                    Explain {t}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p>Ask anything about the material you have uploaded.</p>
          )}
        </div>
      ) : (
        <ol className="thread">
          {turns.map((t) => (
            <TurnView key={t.id} turn={t} />
          ))}
        </ol>
      )}
      <div ref={tail} />

      {preflight.unreachable ? (
        <Unreachable onRetry={preflight.refresh} />
      ) : stopped.length > 0 ? (
        <Blocked
          blockers={stopped}
          action="answer questions"
          onRetry={preflight.refresh}
        />
      ) : (
      <>
      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
      >
        <input
          className="ask__input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about anything in your material…"
          aria-label="Your question"
          disabled={pending || preflight.loading}
        />
        <button
          className="ask__btn"
          type="submit"
          disabled={pending || preflight.loading || !draft.trim()}
        >
          {pending ? "Thinking…" : "Ask"}
        </button>
      </form>
      <p className="ask__note">
        {/* Measured: the first request of a session loads the models. */}
        The first question of a session takes about half a minute while the models
        load.
      </p>
      </>
      )}
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  return (
    <li className="turn">
      <p className="turn__q">{turn.question}</p>

      {turn.error ? (
        <div className={`turn__fail${turn.busy ? " turn__fail--busy" : ""}`}>
          <p>{turn.error}</p>
          {turn.busy && (
            <p className="turn__failhint">
              Your notes are locked while that finishes. Ask again in a few minutes.
            </p>
          )}
        </div>
      ) : turn.answer === null ? (
        <p className="turn__pending">
          <span className="turn__dots" aria-hidden="true" />
          Reading your material…
        </p>
      ) : (
        <Answer answer={turn.answer} />
      )}
    </li>
  );
}

function Answer({ answer }: { answer: ChatAnswer }) {
  const grounded = answer.from_material;

  return (
    <div className={`ans${grounded ? " ans--traced" : ""}`}>
      {/* The trace rail is the mark. It appears here and nowhere else on this
          screen, and its ABSENCE is what labels a general-knowledge answer — so
          nothing decorative may ever borrow it. */}
      <p className="ans__text">{answer.answer}</p>

      {grounded ? (
        answer.citations.length > 0 && (
          <ul className="cites">
            {answer.citations.map((c, i) => (
              <li key={`${c.file}-${c.page}-${i}`} className="cite">
                <span className="cite__file">{c.file}</span>
                <span className="cite__page">p.{c.page}</span>
              </li>
            ))}
          </ul>
        )
      ) : (
        <p className="ans__ungrounded">
          Answered from general knowledge — not from your uploads. There is no page
          of yours to check this against.
        </p>
      )}

      {answer.status === "degraded" && (
        <p className="ans__degraded">This one stopped short of a full answer.</p>
      )}
    </div>
  );
}
