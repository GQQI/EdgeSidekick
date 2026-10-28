import "./styles/app.css";
import "./styles/app-plugins.css";
import "./styles/social.css";
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { PrefsProvider } from "./prefs";
import { applyTheme, loadTheme } from "./i18n";

applyTheme(loadTheme());

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <PrefsProvider>
      <App />
    </PrefsProvider>
  </React.StrictMode>,
);

