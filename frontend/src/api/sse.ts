// 채팅 SSE 리더(TRD 14.2). EventSource 는 GET 만 되므로 fetch 의 본문 스트림을 직접 읽는다.
// 이벤트 데이터 모양은 생성 타입 ChatEvents 의 같은 이름 필드다(서버 계약 테스트가 맞춰 둔다).
import { API, ApiError, type Problem } from "./client";
import type { ChatEvents, ChatRequest } from "./types";

export type ChatEvent = {
  [K in keyof ChatEvents]: { event: K; data: ChatEvents[K] };
}[keyof ChatEvents];

function parseBlock(block: string): ChatEvent | null {
  let event = "";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) {
      event = line.slice(7);
    } else if (line.startsWith("data: ")) {
      data.push(line.slice(6));
    }
  }
  if (!event || data.length === 0) {
    return null;
  }
  return { event, data: JSON.parse(data.join("\n")) } as ChatEvent;
}

/** 질문 하나를 보내고 이벤트를 차례로 넘긴다. 스트림이 끝나면 resolve 한다. */
export async function streamChat(
  body: ChatRequest,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API}/chat`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let problem: Problem = { title: `${res.status} ${res.statusText}`, status: res.status };
    try {
      problem = { ...problem, ...((await res.json()) as Partial<Problem>) } as Problem;
    } catch {
      // 본문이 JSON 이 아니면 상태 줄만 쓴다.
    }
    throw new ApiError(problem);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += value.replace(/\r\n/g, "\n");
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const parsed = parseBlock(buffer.slice(0, cut));
      buffer = buffer.slice(cut + 2);
      if (parsed) {
        onEvent(parsed);
      }
      cut = buffer.indexOf("\n\n");
    }
  }
}
