import { useEffect, useRef, useState } from "react";
import { updateAccountProfile, type AccountProfile } from "../api";
import { usePrefs } from "../prefs";

type Props = {
  user: AccountProfile | null;
  onUserChange?: (user: AccountProfile) => void;
  onLogout?: () => void;
  onToast?: (msg: string) => void;
};

const MAX_AVATAR_FILE = 8 * 1024 * 1024;
const AVATAR_SIZE = 256;

/** Crop to a square and downscale so the data URL stays small. */
async function toAvatarDataUrl(file: File): Promise<string> {
  if (file.size > MAX_AVATAR_FILE) throw new Error("too-large");
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("bad-image"));
      el.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = AVATAR_SIZE;
    canvas.height = AVATAR_SIZE;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("bad-image");
    const scale = Math.max(AVATAR_SIZE / img.width, AVATAR_SIZE / img.height);
    const w = img.width * scale;
    const h = img.height * scale;
    ctx.drawImage(img, (AVATAR_SIZE - w) / 2, (AVATAR_SIZE - h) / 2, w, h);
    return canvas.toDataURL("image/jpeg", 0.86);
  } finally {
    URL.revokeObjectURL(url);
  }
}

type Draft = {
  displayName: string;
  email: string;
  title: string;
  bio: string;
  avatar: string;
  dirty: boolean;
};

export function AccountPane({ user, onUserChange, onLogout, onToast }: Props) {
  const { t, locale } = usePrefs();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [title, setTitle] = useState("");
  const [bio, setBio] = useState("");
  const [avatar, setAvatar] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);
  const draftRef = useRef<Draft>({
    displayName: "",
    email: "",
    title: "",
    bio: "",
    avatar: "",
    dirty: false,
  });
  const userIdRef = useRef<string | null>(null);
  const onUserChangeRef = useRef(onUserChange);
  const onToastRef = useRef(onToast);
  onUserChangeRef.current = onUserChange;
  onToastRef.current = onToast;

  useEffect(() => {
    if (!user) return;
    userIdRef.current = user.id;
    setDisplayName(user.display_name || user.username);
    setEmail(user.email || "");
    setTitle(user.title || "");
    setBio(user.bio || "");
    setAvatar(user.avatar || "");
    setSaved(false);
    setError("");
    draftRef.current = {
      displayName: user.display_name || user.username,
      email: user.email || "",
      title: user.title || "",
      bio: user.bio || "",
      avatar: user.avatar || "",
      dirty: false,
    };
  }, [user?.id, user?.display_name, user?.email, user?.title, user?.bio, user?.avatar]);

  useEffect(() => {
    draftRef.current = {
      displayName,
      email,
      title,
      bio,
      avatar,
      dirty: draftRef.current.dirty,
    };
  }, [displayName, email, title, bio, avatar]);

  // Flush unsaved edits when leaving the account pane / closing settings.
  useEffect(() => {
    return () => {
      const draft = draftRef.current;
      const uid = userIdRef.current;
      if (!draft.dirty || !uid) return;
      const name = draft.displayName.trim();
      if (!name) return;
      void updateAccountProfile({
        display_name: name,
        email: draft.email.trim(),
        title: draft.title.trim(),
        bio: draft.bio.trim(),
        avatar: draft.avatar,
      })
        .then((next) => {
          draftRef.current.dirty = false;
          onUserChangeRef.current?.(next);
        })
        .catch((e) => {
          const msg = e instanceof Error ? e.message : String(e);
          onToastRef.current?.(msg);
        });
    };
  }, []);

  if (!user) {
    return (
      <div className="settings-pane account-pane">
        <header className="settings-pane-intro">
          <h3>{t("acctTitle")}</h3>
          <p className="hint">{t("acctLegacy")}</p>
        </header>
      </div>
    );
  }

  const initial = (displayName || user.username || "?").trim().slice(0, 1).toUpperCase();
  const memberSince = user.created_at
    ? new Date(user.created_at * 1000).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US")
    : "";

  const touch = () => {
    draftRef.current.dirty = true;
    setSaved(false);
    setError("");
  };

  const pickAvatar = () => fileRef.current?.click();

  const persist = async (nextAvatar?: string) => {
    const name = displayName.trim();
    if (!name) {
      setError(t("acctEmptyName"));
      return false;
    }
    setSaving(true);
    setError("");
    try {
      const next = await updateAccountProfile({
        display_name: name,
        email: email.trim(),
        title: title.trim(),
        bio: bio.trim(),
        avatar: nextAvatar !== undefined ? nextAvatar : avatar,
      });
      draftRef.current.dirty = false;
      onUserChange?.(next);
      setSaved(true);
      onToast?.(t("acctSaved"));
      return true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(t("acctSaveFail", msg));
      onToast?.(t("acctSaveFail", msg));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const onAvatarFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const next = await toAvatarDataUrl(file);
      setAvatar(next);
      touch();
      // Avatar changes should stick even if the user closes settings immediately.
      await persist(next);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      const text = msg === "too-large" ? t("acctAvatarTooLarge") : t("acctAvatarBad");
      setError(text);
      onToast?.(text);
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="settings-pane account-pane">
      <header className="settings-pane-intro">
        <h3>{t("acctTitle")}</h3>
        <p className="hint">{t("acctHint")}</p>
      </header>

      <section className="account-hero">
        <div className="account-avatar">
          {avatar ? <img src={avatar} alt="" /> : <span>{initial}</span>}
        </div>
        <div className="account-identity">
          <strong>{displayName.trim() || user.username}</strong>
          <span>
            {user.username}
            {title.trim() ? ` · ${title.trim()}` : ""}
          </span>
          {memberSince ? <em>{t("acctMemberSince", memberSince)}</em> : null}
        </div>
        <div className="account-avatar-actions">
          <button type="button" className="ghost" disabled={saving} onClick={pickAvatar}>
            {t("acctUpload")}
          </button>
          {avatar ? (
            <button
              type="button"
              className="ghost danger"
              disabled={saving}
              onClick={() => {
                setAvatar("");
                touch();
                void persist("");
              }}
            >
              {t("acctRemoveAvatar")}
            </button>
          ) : null}
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            hidden
            onChange={(e) => void onAvatarFile(e.target.files?.[0])}
          />
        </div>
      </section>
      <p className="hint account-avatar-hint">{t("acctAvatarHint")}</p>

      <section className="account-card">
        <div className="account-form">
          <label className="account-field">
            <span>{t("acctDisplayName")}</span>
            <input
              value={displayName}
              maxLength={60}
              placeholder={user.username}
              onChange={(e) => {
                setDisplayName(e.target.value);
                touch();
              }}
            />
          </label>
          <label className="account-field">
            <span>{t("acctEmail")}</span>
            <input
              type="email"
              value={email}
              autoComplete="email"
              onChange={(e) => {
                setEmail(e.target.value);
                touch();
              }}
            />
          </label>
          <label className="account-field account-field-wide">
            <span>{t("acctRole")}</span>
            <input
              value={title}
              maxLength={80}
              onChange={(e) => {
                setTitle(e.target.value);
                touch();
              }}
            />
          </label>
          <label className="account-field account-field-wide">
            <span>{t("acctBio")}</span>
            <textarea
              rows={3}
              value={bio}
              maxLength={500}
              placeholder={t("acctBioPlaceholder")}
              onChange={(e) => {
                setBio(e.target.value);
                touch();
              }}
            />
          </label>
        </div>
        {error ? <div className="side-error account-error">{error}</div> : null}
        <div className="account-actions">
          <button type="button" className="primary" disabled={saving} onClick={() => void persist()}>
            {saving ? t("commonSaving") : t("acctSave")}
          </button>
          {saved ? <span className="account-saved">{t("acctSaved")}</span> : null}
        </div>
      </section>

      <section className="account-card account-session">
        <div className="account-session-info">
          <strong>{t("acctSignIn")}</strong>
          <span>{user.email || user.username}</span>
          <p className="hint">{t("acctLogoutHint")}</p>
        </div>
        {onLogout ? (
          <button type="button" className="ghost danger" onClick={() => void onLogout()}>
            {t("acctLogout")}
          </button>
        ) : null}
      </section>
    </div>
  );
}
