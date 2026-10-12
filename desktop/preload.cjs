const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("sidekickDesktop", {
  isDesktop: true,
  workspace: {
    pickFolder: (opts) => ipcRenderer.invoke("workspace:pickFolder", opts || {}),
  },
  browser: {
    show: (bounds) => ipcRenderer.invoke("browser:show", bounds),
    hide: () => ipcRenderer.invoke("browser:hide"),
    setBounds: (bounds) => ipcRenderer.invoke("browser:setBounds", bounds),
    navigate: (url) => ipcRenderer.invoke("browser:navigate", url),
    scrollPage: (kind) => ipcRenderer.invoke("browser:scrollPage", kind || "bottom"),
    getUrl: () => ipcRenderer.invoke("browser:getUrl"),
    reload: () => ipcRenderer.invoke("browser:reload"),
    goBack: () => ipcRenderer.invoke("browser:goBack"),
    goForward: () => ipcRenderer.invoke("browser:goForward"),
    selectArm: (timeoutMs) => ipcRenderer.invoke("browser:selectArm", timeoutMs || 60000),
    selectCancel: () => ipcRenderer.invoke("browser:selectCancel"),
    onNavigated: (cb) => {
      const handler = (_event, url) => cb(url);
      ipcRenderer.on("browser:navigated", handler);
      return () => ipcRenderer.removeListener("browser:navigated", handler);
    },
    onRequestBounds: (cb) => {
      const handler = () => cb();
      ipcRenderer.on("browser:requestBounds", handler);
      return () => ipcRenderer.removeListener("browser:requestBounds", handler);
    },
    onLoadFailure: (cb) => {
      const handler = (_event, info) => cb(info);
      ipcRenderer.on("browser:load-fail", handler);
      return () => ipcRenderer.removeListener("browser:load-fail", handler);
    },
  },
});
