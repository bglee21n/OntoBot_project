import { useRef, useState } from "react";

import { useFeatures } from "../../app/features";
import { ko } from "../../i18n/ko";

const t = ko.workshop.voice;

// 브라우저 음성 인식(Web Speech API) — 표준 타입이 없어 쓰는 만큼만 적는다.
interface Recognition {
  lang: string;
  interimResults: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/**
 * 음성 입력(결정 13) — 서버가 켠 경우(폐쇄망 아님 + 설정)만 **그린다**. 꺼져 있으면 아무것도 그리지 않는다.
 * 인식한 글은 입력 칸에 붙을 뿐이고, 보내면 글 발언과 똑같이 마스킹 저장된다.
 */
export function VoiceButton({ onText }: { onText: (text: string) => void }) {
  const features = useFeatures();
  const [listening, setListening] = useState(false);
  const rec = useRef<Recognition | null>(null);
  const Ctor = recognitionCtor();
  if (!features.voice_input || Ctor === null) return null;
  const toggle = () => {
    if (listening) {
      rec.current?.stop();
      return;
    }
    const r = new Ctor();
    r.lang = "ko-KR";
    r.interimResults = false;
    r.onresult = (e) => {
      const text = Array.from(e.results)
        .map((x) => x[0]?.transcript ?? "")
        .join(" ")
        .trim();
      if (text) onText(text);
    };
    r.onend = () => setListening(false);
    rec.current = r;
    setListening(true);
    r.start();
  };
  return (
    <span className="stack voice">
      <button type="button" className={listening ? "danger" : "link"} onClick={toggle}>
        {listening ? t.stop : t.start}
      </button>
      <span className="muted">{t.notice}</span>
    </span>
  );
}
