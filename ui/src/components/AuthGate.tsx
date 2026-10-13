import { useState } from "react";
import { BrandMark } from "./BrandMark";

type Props = {
  mode: "setup" | "login";
  busy?: boolean;
  error?: string | null;
  onSetup: (payload: { username: string; email: string; password: string }) => Promise<void>;
  onRegister: (payload: { username: string; email: string; password: string }) => Promise<void>;
  onLogin: (payload: { email: string; password: string }) => Promise<void>;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** First-run setup, or login with open self-registration. */
export function AuthGate({ mode, busy, error, onSetup, onRegister, onLogin }: Props) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [wantRegister, setWantRegister] = useState(false);

  const registering = mode === "setup" || wantRegister;
  const title = mode === "setup" ? "创建管理员账号" : registering ? "注册" : "登录 EdgeSidekick";
  const hint = registering
    ? "用邮箱注册。每个账号的对话、模型、记忆和工作区互相隔离。"
    : "使用邮箱登录。数据仅保存在本机。";

  const submit = async () => {
    setLocalError(null);
    const mail = email.trim().toLowerCase();
    if (!EMAIL_RE.test(mail)) {
      setLocalError("请输入有效邮箱");
      return;
    }
    if (registering) {
      const u = username.trim();
      if (u.length < 2) {
        setLocalError("显示名至少 2 个字符");
        return;
      }
    }
    if (password.length < 6) {
      setLocalError("密码至少 6 位");
      return;
    }
    if (registering && password !== password2) {
      setLocalError("两次密码不一致");
      return;
    }
    try {
      if (mode === "setup") {
        await onSetup({ username: username.trim(), email: mail, password });
      } else if (wantRegister) {
        await onRegister({ username: username.trim(), email: mail, password });
      } else {
        await onLogin({ email: mail, password });
      }
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    }
  };

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

        <form
          className="auth-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          {registering && (
            <label className="auth-field">
              <span>显示名</span>
              <input
                autoComplete="nickname"
                placeholder="例如：小明"
                value={username}
                disabled={busy}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
          )}
          <label className="auth-field">
            <span>邮箱</span>
            <input
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              disabled={busy}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="auth-field">
            <span>密码</span>
            <input
              type="password"
              autoComplete={registering ? "new-password" : "current-password"}
              placeholder="至少 6 位"
              value={password}
              disabled={busy}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {registering && (
            <label className="auth-field">
              <span>确认密码</span>
              <input
                type="password"
                autoComplete="new-password"
                placeholder="再输入一次"
                value={password2}
                disabled={busy}
                onChange={(e) => setPassword2(e.target.value)}
              />
            </label>
          )}
          {(localError || error) && (
            <p className="auth-error" role="alert">
              {localError || error}
            </p>
          )}
          <button type="submit" className="welcome-cta" disabled={busy}>
            <span>
              {busy ? "请稍候…" : mode === "setup" ? "完成设置" : registering ? "注册" : "登录"}
            </span>
          </button>
          {mode === "login" && (
            <p className="auth-switch">
              {wantRegister ? "已有账号？" : "没有账号？"}
              <button
                type="button"
                className="auth-switch-btn"
                disabled={busy}
                onClick={() => {
                  setLocalError(null);
                  setWantRegister((v) => !v);
                }}
              >
                {wantRegister ? "去登录" : "注册"}
              </button>
            </p>
          )}
        </form>
      </div>
    </section>
  );
}
