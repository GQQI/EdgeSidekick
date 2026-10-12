"use strict";

/**
 * Marks the in-app BrowserView so Playwright CDP can attach to it
 * instead of the workbench window. Also softens the most common
 * automation fingerprint before page scripts run.
 */
const { contextBridge } = require("electron");
contextBridge.exposeInMainWorld("__sidekickGuest", true);

try {
  // Runs in the isolated preload world; main also injects on dom-ready.
  Object.defineProperty(navigator, "webdriver", { get: () => undefined });
} catch {
  /* ignore */
}
