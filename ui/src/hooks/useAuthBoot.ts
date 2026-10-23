import { useEffect } from "react";
import {
  ensureApiToken,
  fetchAuthStatus,
  getStoredToken,
  setApiToken,
  type AccountProfile,
  type Health,
} from "../api";

type Phase = "loading" | "setup" | "login" | "ok";

type AccountUser = AccountProfile | null;

type Deps = {
  boot: () => Promise<void>;
  setHealth: (h: Health | null) => void;
  setBootReady: (v: boolean) => void;
  setAuthPhase: (p: Phase) => void;
  setAccountUser: (u: AccountUser) => void;
};

export function useAuthBoot({
  boot,
  setHealth,
  setBootReady,
  setAuthPhase,
  setAccountUser,
}: Deps) {
  useEffect(() => {
    void (async () => {
      try {
        const status = await fetchAuthStatus();
        if (status.needs_setup) {
          setAuthPhase("setup");
          setBootReady(true);
          return;
        }
        // A saved login that the server no longer accepts (unused for 30 days)
        // must not be treated as signed in.
        if (status.multi_user && getStoredToken() && status.authenticated === false) {
          setApiToken(null);
          setAccountUser(null);
          setAuthPhase("login");
          setBootReady(true);
          return;
        }
        await ensureApiToken();
        if (status.multi_user && !getStoredToken()) {
          setAuthPhase("login");
          setBootReady(true);
          return;
        }
        const again = await fetchAuthStatus();
        if (again.user) setAccountUser(again.user);
        if (status.multi_user && !again.authenticated) {
          setApiToken(null);
          setAccountUser(null);
          setAuthPhase("login");
          setBootReady(true);
          return;
        }
        setAuthPhase("ok");
        await boot();
      } catch (e) {
        setHealth({ ok: false, demo: true, model: "offline", workspace: String(e) });
        setAuthPhase("ok");
        setBootReady(true);
      }
    })();
  }, []);

  const finishAuth = async () => {
    setAuthPhase("ok");
    setBootReady(false);
    try {
      const me = await fetchAuthStatus();
      if (me.user) setAccountUser(me.user);
      await boot();
    } catch (e) {
      setHealth({ ok: false, demo: true, model: "offline", workspace: String(e) });
      setBootReady(true);
    }
  };

  return { finishAuth };
}
