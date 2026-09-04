import { CHECK_LABELS, type CheckName } from "./usePreflight";
import "./system.css";

/**
 * What stands between the student and the thing they were about to do.
 *
 * Shown *instead of* enabling the control, never beside it. A disabled button
 * with no explanation is the version of this that sends someone to reinstall the
 * app; the failure strings the backend returns are already written for a person
 * and say what to change, so they are shown verbatim.
 */

interface Props {
  blockers: { name: CheckName; reason: string }[];
  /** What they were trying to do, e.g. "build a paper". */
  action: string;
  onRetry: () => void;
}

export function Blocked({ blockers, action, onRetry }: Props) {
  if (blockers.length === 0) return null;

  return (
    <div className="blocked" role="status">
      <p className="blocked__lead">
        This machine can&rsquo;t {action} right now.
      </p>
      <dl className="blocked__list">
        {blockers.map((b) => (
          <div key={b.name}>
            <dt>{CHECK_LABELS[b.name]}</dt>
            {/* Verbatim. The memory check in particular explains the page-file
                cause and the remedy, and paraphrasing it would lose both. */}
            <dd>{b.reason}</dd>
          </div>
        ))}
      </dl>
      <button type="button" className="blocked__btn" onClick={onRetry}>
        Check again
      </button>
    </div>
  );
}

/** The server answered nothing at all — a different problem, and a different fix. */
export function Unreachable({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="blocked" role="status">
      <p className="blocked__lead">The Prepify server isn&rsquo;t responding.</p>
      <p className="blocked__hint">
        Nothing is wrong with your material. Start the backend, then check again.
      </p>
      <code className="blocked__cmd">
        uvicorn coursegen.app.main:app --workers 1
      </code>
      <button type="button" className="blocked__btn" onClick={onRetry}>
        Check again
      </button>
    </div>
  );
}
