import { IconClock, IconMoon, IconPlus, IconSun } from "./icons";
import { BrandMark } from "./BrandMark";
import type { MsgKey, Theme } from "../i18n";

type Props = {
  t: (key: MsgKey, ...args: string[]) => string;
  theme: Theme;
  hasWorkspace: boolean;
  /** Hide history / new-chat (social mode has its own chrome). */
  hideSessionActions?: boolean;
  onOpenHistory: () => void;
  onNewChat: () => void;
  onToggleTheme: () => void;
};

export function AppHeader({
  t,
  theme,
  hasWorkspace,
  hideSessionActions = false,
  onOpenHistory,
  onNewChat,
  onToggleTheme,
}: Props) {
  return (
    <header className="top">
      <div className="top-left">
        <div className="brand">
          <span className="brand-mark brand-mark-anim">
            <BrandMark size={30} />
          </span>
          <div className="brand-text">
            <strong>EdgeSidekick</strong>
            <span className="brand-sub">AI AGENT SYSTEM</span>
          </div>
        </div>
        {hasWorkspace && !hideSessionActions && (
          <>
            <button type="button" className="chip action iconed" onClick={onOpenHistory}>
              <IconClock size={15} />
              <span>{t("history")}</span>
            </button>
            <button type="button" className="chip action iconed" onClick={onNewChat}>
              <IconPlus size={15} />
              <span>{t("newChat")}</span>
            </button>
          </>
        )}
      </div>
      <div className="top-right">
        <button
          type="button"
          className="theme-toggle"
          title={theme === "dark" ? t("themeLight") : t("themeDark")}
          aria-label={theme === "dark" ? t("themeLight") : t("themeDark")}
          onClick={onToggleTheme}
        >
          {theme === "dark" ? <IconSun /> : <IconMoon />}
        </button>
      </div>
    </header>
  );
}
