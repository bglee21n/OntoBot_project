import { nextTheme, setTheme, useTheme } from "../app/theme";
import { ko } from "../i18n/ko";

const ICON = { system: "◐", light: "☀", dark: "☾" } as const;

/** 시스템 → 밝게 → 어둡게 순서로 돈다. */
export function ThemeToggle() {
  const mode = useTheme();
  const t = ko.theme;
  const label = `${t.label}: ${t[mode]}`;
  return (
    <button
      type="button"
      className="link theme-toggle"
      onClick={() => setTheme(nextTheme(mode))}
      title={`${label} — ${t.change}`}
      aria-label={`${label} — ${t.change}`}
    >
      <span aria-hidden="true">{ICON[mode]}</span>
      <span className="theme-toggle-text">{t[mode]}</span>
    </button>
  );
}
