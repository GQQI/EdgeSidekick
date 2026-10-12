import { IconFolder, IconPlus } from "./icons";
import { BrandMark } from "./BrandMark";

export type WelcomeWorkspace = {
  path: string;
  name: string;
};

type Props = {
  title: string;
  hint: string;
  openLabel: string;
  browsingLabel: string;
  recentLabel: string;
  busy: boolean;
  workspaces: WelcomeWorkspace[];
  onBrowse: () => void;
  onSelect: (path: string) => void;
};

/** First-run workspace picker — brand-forward, single composition. */
export function WelcomeGate({
  title,
  hint,
  openLabel,
  browsingLabel,
  recentLabel,
  busy,
  workspaces,
  onBrowse,
  onSelect,
}: Props) {
  return (
    <section className="welcome-gate" aria-label={title}>
      <div className="welcome-stage">
        <div className="welcome-brand">
          <span className="welcome-brand-mark">
            <BrandMark size={52} />
          </span>
          <div className="welcome-brand-text">
            <span className="welcome-brand-name">EdgeSidekick</span>
            <span className="welcome-brand-sub">AI AGENT SYSTEM</span>
          </div>
        </div>

        <h1 className="welcome-title">{title}</h1>
        <p className="welcome-hint">{hint}</p>

        <button
          type="button"
          className="welcome-cta"
          disabled={busy}
          onClick={onBrowse}
        >
          <IconPlus size={18} />
          <span>{busy ? browsingLabel : openLabel}</span>
        </button>

        {workspaces.length > 0 && (
          <div className="welcome-recent">
            <div className="welcome-recent-label">{recentLabel}</div>
            <ul className="welcome-list">
              {workspaces.map((w) => (
                <li key={w.path}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onSelect(w.path)}
                  >
                    <span className="welcome-folder-icon" aria-hidden>
                      <IconFolder size={18} />
                    </span>
                    <span className="welcome-folder-text">
                      <strong>{w.name}</strong>
                      <span>{w.path}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
