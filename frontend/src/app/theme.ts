// 화면 테마(밝게·어둡게·시스템 따름). 선택은 이 브라우저에만 남는다(localStorage — 사용자 설정이 아니다).
// 실제 색은 styles.css 의 토큰이 정한다: `<html data-theme>` 가 없으면 시스템 설정(prefers-color-scheme)을 따른다.
import { useSyncExternalStore } from "react";

export type ThemeMode = "system" | "light" | "dark";

const KEY = "ontobot.theme";
const listeners = new Set<() => void>();

function read(): ThemeMode {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

let current: ThemeMode = typeof window === "undefined" ? "system" : read();

/** 첫 그리기 전에 부른다(깜빡임 방지). */
export function applyTheme(mode: ThemeMode = current): void {
  const root = document.documentElement;
  if (mode === "system") {
    delete root.dataset.theme;
  } else {
    root.dataset.theme = mode;
  }
}

export function setTheme(mode: ThemeMode): void {
  current = mode;
  try {
    if (mode === "system") {
      window.localStorage.removeItem(KEY);
    } else {
      window.localStorage.setItem(KEY, mode);
    }
  } catch {
    // 저장이 막힌 브라우저 — 이번 화면에서만 바뀐다.
  }
  applyTheme(mode);
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useTheme(): ThemeMode {
  return useSyncExternalStore(subscribe, () => current, () => "system");
}

const NEXT: Record<ThemeMode, ThemeMode> = { system: "light", light: "dark", dark: "system" };

export function nextTheme(mode: ThemeMode): ThemeMode {
  return NEXT[mode];
}
