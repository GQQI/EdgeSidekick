/** Electron desktop bridge — live BrowserView. */

import type { DomElementPayload } from "./browser/protocol";

export type DesktopBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type SidekickDesktopApi = {
  isDesktop: true;
  workspace?: {
    pickFolder: (opts?: { title?: string }) => Promise<{ cancelled: boolean; path: string | null }>;
  };
  browser: {
    show: (bounds: DesktopBounds) => Promise<{ ok: boolean; live?: boolean }>;
    hide: () => Promise<{ ok: boolean }>;
    setBounds: (bounds: DesktopBounds) => Promise<{ ok: boolean }>;
    navigate: (url: string) => Promise<{ url: string; live?: boolean }>;
    scrollPage: (kind?: "top" | "bottom" | "up" | "down") => Promise<{ ok: boolean }>;
    getUrl: () => Promise<string>;
    reload: () => Promise<{ ok: boolean }>;
    goBack: () => Promise<{ ok: boolean }>;
    goForward: () => Promise<{ ok: boolean }>;
    selectArm: (timeoutMs?: number) => Promise<DomElementPayload | null>;
    selectCancel: () => Promise<{ ok: boolean }>;
    onNavigated: (cb: (url: string) => void) => () => void;
    onRequestBounds: (cb: () => void) => () => void;
    onLoadFailure: (cb: (info: { code: number; description: string; url: string }) => void) => () => void;
  };
};

declare global {
  interface Window {
    sidekickDesktop?: SidekickDesktopApi;
  }
}

export function getDesktop(): SidekickDesktopApi | null {
  return typeof window !== "undefined" && window.sidekickDesktop?.isDesktop
    ? window.sidekickDesktop
    : null;
}

export function isDesktopApp(): boolean {
  return getDesktop() != null;
}
