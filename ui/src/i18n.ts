export type Locale = "zh" | "en";
export type Theme = "light" | "dark";
export type Density = "comfort" | "compact";

const LOCALE_KEY = "sidekick.locale";
const THEME_KEY = "edgesidekick.theme";
const DENSITY_KEY = "sidekick.density";

export function loadLocale(): Locale {
  const v = localStorage.getItem(LOCALE_KEY);
  return v === "en" ? "en" : "zh";
}

export function saveLocale(locale: Locale) {
  localStorage.setItem(LOCALE_KEY, locale);
}

export function loadTheme(): Theme {
  const v = localStorage.getItem(THEME_KEY);
  return v === "dark" ? "dark" : "light";
}

export function saveTheme(theme: Theme) {
  localStorage.setItem(THEME_KEY, theme);
}

export function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "dark" ? "#0a0f16" : "#f2f5fa");
}

export function loadDensity(): Density {
  const v = localStorage.getItem(DENSITY_KEY);
  return v === "compact" ? "compact" : "comfort";
}

export function saveDensity(density: Density) {
  localStorage.setItem(DENSITY_KEY, density);
}

export function applyDensity(density: Density) {
  document.documentElement.setAttribute("data-density", density);
}

import { zh } from "./i18n_zh";
import { en } from "./i18n_en";

export type MsgKey = keyof typeof zh;

const tables: Record<Locale, Record<MsgKey, string>> = { zh, en };

export function translate(locale: Locale, key: MsgKey, ...args: string[]): string {
  let s = tables[locale][key] || tables.zh[key] || key;
  args.forEach((a, i) => {
    s = s.replace(`{${i}}`, a);
  });
  return s;
}

export const SLASH_DESC: Record<string, { zh: string; en: string }> = {
  help: { zh: "列出全部快捷命令", en: "List all slash commands" },
  new: { zh: "开启新对话", en: "Start a new chat" },
  stop: { zh: "停止当前生成", en: "Stop current generation" },
  clear: { zh: "清空当前对话界面（不删历史文件）", en: "Clear chat UI (keep history files)" },
  history: { zh: "打开历史会话", en: "Open chat history" },
  save: { zh: "保存当前会话到磁盘", en: "Save current session to disk" },
  skills: { zh: "查看全部 Skills", en: "List all skills" },
  skill: { zh: "调用指定 Skill，可在名称后附加指令", en: "Run a skill; optional instructions after the name" },
  memory: { zh: "打开记忆库页面；可加 refresh 刷新", en: "Open the memory page; optional refresh" },
  model: { zh: "打开模型设置", en: "Open model settings" },
  workspace: { zh: "打开工作区设置", en: "Open workspace settings" },
  runtime: { zh: "打开运行时事件面板", en: "Open runtime event panel" },
  stats: { zh: "查看上下文与迭代统计", en: "Show context and iteration stats" },
  files: { zh: "展开文件浏览器", en: "Expand file explorer" },
  settings: { zh: "打开设置", en: "Open settings" },
};
