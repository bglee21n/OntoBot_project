// 그래프 전체 화면 — 그래프를 담은 상자(캔버스 + 옆 패널)를 화면 전체로 덮는다(CSS `.graph-fullscreen`).
// 브라우저가 허락하면 페이지 전체도 전체 화면으로 올린다(요소가 아니라 문서를 올린다 — 확인 창 같은
// 상자 밖 요소가 전체 화면에서도 보여야 한다). iPhone 처럼 막는 브라우저는 화면 덮기만 한다.
import { Panel, useReactFlow } from "@xyflow/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ko } from "../i18n/ko";

export type Fullscreen = { on: boolean; toggle: () => void };

export function useGraphFullscreen(): Fullscreen {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!on) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) setOn(false);
    };
    // 브라우저 전체 화면을 Esc·뒤로 가기로 빠져나오면 화면 덮기도 함께 끝낸다.
    const onChange = () => {
      if (!document.fullscreenElement) setOn(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onChange);
    document.body.classList.add("graph-fullscreen-open");
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onChange);
      document.body.classList.remove("graph-fullscreen-open");
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    };
  }, [on]);
  const toggle = useCallback(() => {
    if (!on && typeof document.documentElement.requestFullscreen === "function" && !document.fullscreenElement) {
      // 막혀도(권한·iPhone) 화면 덮기는 된다.
      void document.documentElement.requestFullscreen().catch(() => undefined);
    }
    setOn(!on);
  }, [on]);
  return { on, toggle };
}

/** React Flow 안(오른쪽 위)에 두는 단추. 켜고 끌 때 그래프를 새 크기에 맞춘다. */
export function FullscreenButton({ fs }: { fs: Fullscreen }) {
  const { fitView } = useReactFlow();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const id = window.setTimeout(() => void fitView({ padding: 0.1, duration: 200 }), 120);
    return () => window.clearTimeout(id);
  }, [fs.on, fitView]);
  const label = fs.on ? ko.graphView.exitFullscreen : ko.graphView.fullscreen;
  return (
    <Panel position="top-right">
      <button type="button" className="graph-fs-button" onClick={fs.toggle} title={label} aria-pressed={fs.on}>
        <span aria-hidden="true">{fs.on ? "✕" : "⛶"}</span> {label}
      </button>
    </Panel>
  );
}
