import { useState } from "react";
import { BrandMark } from "./BrandMark";
import { usePrefs } from "../prefs";

type Props = {
  mode: "setup" | "login";
  busy?: boolean;
  error?: string | null;
  onSetup: (payload: { username: string; email: string; password: string }) => Promise<void>;
  onRegister: (payload: { username: string; email: string; password: string }) => Promise<void>;
  onLogin: (payload: { email: string; password: string }) => Promise<void>;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const COPY = {
  zh: {
    language: "语言",
    titleSetup: "创建管理员账号",
    titleLogin: "登录",
    titleRegister: "注册",
    hintSetup: "用邮箱创建本机账号。每个账号的对话、模型、记忆和工作区互相隔离。",
    hintLogin: "使用邮箱登录。",
    hintRegister: "用邮箱注册。每个账号的对话、模型、记忆和工作区互相隔离。",
    displayName: "显示名",
    displayPlaceholder: "例如：小明",
    email: "邮箱",
    password: "密码",
    passwordPlaceholder: "至少 6 位",
    confirm: "确认密码",
    confirmPlaceholder: "再输入一次",
    busy: "请稍候…",
    submitSetup: "完成设置",
    submitRegister: "注册",
    submitLogin: "登录",
    noAccount: "没有账号？",
    haveAccount: "已有账号？",
    goRegister: "注册",
    goLogin: "去登录",
    invalidEmail: "请输入有效邮箱",
    shortName: "显示名至少 2 个字符",
    shortPassword: "密码至少 6 位",
    mismatch: "两次密码不一致",
  },
  en: {
    language: "Language",
    titleSetup: "Create admin account",
    titleLogin: "Sign in",
    titleRegister: "Sign up",
    hintSetup: "Create a local account with email. Each account keeps its own chats, models, memory, and workspace.",
    hintLogin: "Sign in with your email.",
    hintRegister: "Sign up with email. Each account keeps its own chats, models, memory, and workspace.",
    displayName: "Display name",
    displayPlaceholder: "e.g. Alex",
    email: "Email",
    password: "Password",
    passwordPlaceholder: "At least 6 characters",
    confirm: "Confirm password",
    confirmPlaceholder: "Type it again",
    busy: "Please wait…",
    submitSetup: "Finish setup",
    submitRegister: "Sign up",
    submitLogin: "Sign in",
    noAccount: "No account yet?",
    haveAccount: "Already have an account?",
    goRegister: "Sign up",
    goLogin: "Sign in",
    invalidEmail: "Enter a valid email",
    shortName: "Display name must be at least 2 characters",
    shortPassword: "Password must be at least 6 characters",
    mismatch: "Passwords do not match",
  },
} as const;

const ERROR_COPY = {
  zh: {
    badCredentials: "邮箱或密码不正确",
    nameTaken: "这个显示名已被使用",
    emailTaken: "这个邮箱已经注册",
    alreadySetUp: "已经完成初始化",
    registrationClosed: "注册未开放，请联系已有用户添加账号",
    shortName: "显示名至少 2 个字符",
    shortPassword: "密码至少 6 位",
    invalidEmail: "请输入有效邮箱",
  },
  en: {
    badCredentials: "Invalid email or password",
    nameTaken: "That display name is taken",
    emailTaken: "That email is already registered",
    alreadySetUp: "This instance is already set up",
    registrationClosed: "Registration is closed; ask an existing user to add accounts",
    shortName: "Display name must be at least 2 characters",
    shortPassword: "Password must be at least 6 characters",
    invalidEmail: "Enter a valid email",
  },
} as const;

type ErrorKey = keyof (typeof ERROR_COPY)["zh"];

const BACKEND_ERRORS: Record<string, ErrorKey> = {
  "invalid email or password": "badCredentials",
  "username too short": "shortName",
  "password must be at least 6 characters": "shortPassword",
  "username taken": "nameTaken",
  "email already registered": "emailTaken",
  "invalid email": "invalidEmail",
  "already set up": "alreadySetUp",
  "registration closed; ask an existing user to add accounts": "registrationClosed",
};

function localizeError(raw: string, locale: "zh" | "en"): string {
  const mapped = BACKEND_ERRORS[raw.trim().toLowerCase()];
  return mapped ? ERROR_COPY[locale][mapped] : raw;
}

/** First-run setup, or login with open self-registration. */
export function AuthGate({ mode, busy, error, onSetup, onRegister, onLogin }: Props) {
  const { locale, setLocale } = usePrefs();
  const c = COPY[locale];
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [wantRegister, setWantRegister] = useState(false);

  const registering = mode === "setup" || wantRegister;
  const title = mode === "setup" ? c.titleSetup : registering ? c.titleRegister : c.titleLogin;
  const hint = mode === "setup" ? c.hintSetup : registering ? c.hintRegister : c.hintLogin;

  const submit = async () => {
    setLocalError(null);
    const mail = email.trim().toLowerCase();
    if (!EMAIL_RE.test(mail)) {
      setLocalError(c.invalidEmail);
      return;
    }
    if (registering) {
      const u = username.trim();
      if (u.length < 2) {
        setLocalError(c.shortName);
        return;
      }
    }
    if (password.length < 6) {
      setLocalError(c.shortPassword);
      return;
    }
    if (registering && password !== password2) {
      setLocalError(c.mismatch);
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
      const raw = e instanceof Error ? e.message : String(e);
      setLocalError(localizeError(raw, locale));
    }
  };

  return (
    <section className="welcome-gate" aria-label={title}>
      <div className="auth-locale" role="group" aria-label={c.language}>
        <button
          type="button"
          className={locale === "zh" ? "active" : ""}
          aria-pressed={locale === "zh"}
          onClick={() => setLocale("zh")}
        >
          中文
        </button>
        <button
          type="button"
          className={locale === "en" ? "active" : ""}
          aria-pressed={locale === "en"}
          onClick={() => setLocale("en")}
        >
          EN
        </button>
      </div>
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
              <span>{c.displayName}</span>
              <input
                autoComplete="nickname"
                placeholder={c.displayPlaceholder}
                value={username}
                disabled={busy}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
          )}
          <label className="auth-field">
            <span>{c.email}</span>
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
            <span>{c.password}</span>
            <input
              type="password"
              autoComplete={registering ? "new-password" : "current-password"}
              placeholder={c.passwordPlaceholder}
              value={password}
              disabled={busy}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {registering && (
            <label className="auth-field">
              <span>{c.confirm}</span>
              <input
                type="password"
                autoComplete="new-password"
                placeholder={c.confirmPlaceholder}
                value={password2}
                disabled={busy}
                onChange={(e) => setPassword2(e.target.value)}
              />
            </label>
          )}
          {(localError || error) && (
            <p className="auth-error" role="alert">
              {localError || (error ? localizeError(error, locale) : "")}
            </p>
          )}
          <button type="submit" className="welcome-cta" disabled={busy}>
            <span>
              {busy
                ? c.busy
                : mode === "setup"
                  ? c.submitSetup
                  : registering
                    ? c.submitRegister
                    : c.submitLogin}
            </span>
          </button>
          {mode === "login" && (
            <p className="auth-switch">
              {wantRegister ? c.haveAccount : c.noAccount}
              <button
                type="button"
                className="auth-switch-btn"
                disabled={busy}
                onClick={() => {
                  setLocalError(null);
                  setWantRegister((v) => !v);
                }}
              >
                {wantRegister ? c.goLogin : c.goRegister}
              </button>
            </p>
          )}
        </form>
      </div>
    </section>
  );
}
