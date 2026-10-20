import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type KeyboardEvent, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";

import { api } from "../../api/client";
import { type ChatEvent, streamChat } from "../../api/sse";
import type { ActionPreviewData, CitationData, ConversationOut, MessageOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { ActionProposal } from "./ActionProposal";
import { AnswerView, ObjectCardPanel, type Selection, type Status } from "./AnswerView";

const t = ko.chat;

/** 보내는 중인 한 턴. 답변 본문은 서버가 대조를 마친 뒤 한 번에 온다(TRD 8.3). */
interface Pending {
  question: string;
  stage: string | null;
  conditions: string | null;
  graphAsOf: string | null;
  answer: { text: string; status: Status; grade: number | null } | null;
  citations: CitationData[];
  // Action 요청 말 — 조회 답이 아니다(등급 없음).
  action: ActionPreviewData | null;
  error: { reason: string; message: string } | null;
}

function apply(p: Pending, e: ChatEvent): Pending {
  switch (e.event) {
    case "stage":
      return { ...p, stage: e.data.stage };
    case "conditions":
      return { ...p, conditions: e.data.text, graphAsOf: e.data.graph_as_of };
    case "answer":
      return { ...p, stage: null, answer: { text: e.data.text, status: "ok", grade: null } };
    case "citations":
      return { ...p, citations: e.data };
    case "grade":
      return p.answer
        ? { ...p, answer: { ...p.answer, status: e.data.status, grade: e.data.grade ?? null } }
        : p;
    case "action_preview":
      return { ...p, stage: null, action: e.data };
    case "error":
      return { ...p, stage: null, error: { reason: e.data.reason, message: e.data.message } };
    default:
      return p;
  }
}

export function ChatPage() {
  const { conversationId } = useParams();
  const current = conversationId ? Number(conversationId) : null;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [sendError, setSendError] = useState<unknown>(null);
  const [selected, setSelected] = useState<Selection | null>(null);

  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.get<ConversationOut[]>("/conversations"),
  });
  const messages = useQuery({
    queryKey: ["conversations", current, "messages"],
    queryFn: () => api.get<MessageOut[]>(`/conversations/${current}/messages`),
    enabled: current !== null,
  });

  async function send(e?: FormEvent) {
    e?.preventDefault();
    const q = question.trim();
    if (!q || pending) {
      return;
    }
    setSendError(null);
    setSelected(null);
    let turn: Pending = {
      question: q,
      stage: null,
      conditions: null,
      graphAsOf: null,
      answer: null,
      citations: [],
      action: null,
      error: null,
    };
    setPending(turn);
    setQuestion("");
    let doneConversation: number | null = null;
    try {
      await streamChat({ question: q, conversation_id: current }, (ev) => {
        if (ev.event === "done") {
          doneConversation = ev.data.conversation_id;
        }
        turn = apply(turn, ev);
        setPending(turn);
      });
      if (doneConversation !== null) {
        await qc.invalidateQueries({ queryKey: ["conversations"] });
        if (doneConversation !== current) {
          navigate(`/chat/${doneConversation}`);
        }
        setPending(null);
      }
      // done 없이 끝났으면(서버 내부 오류) 보내던 턴을 오류와 함께 그대로 남긴다.
    } catch (err) {
      setSendError(err);
      setPending(null);
      setQuestion(q);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  }

  const history = current !== null ? (messages.data ?? []) : [];

  return (
    <section className="chat">
      <nav className="chat-list" aria-label={t.conversations}>
        <button type="button" onClick={() => navigate("/chat")} disabled={!!pending}>
          {t.newConversation}
        </button>
        {conversations.isError && <ErrorBox error={conversations.error} />}
        {conversations.data?.length === 0 && <p className="muted">{t.noConversations}</p>}
        {conversations.data?.map((c) => (
          <NavLink key={c.id} to={`/chat/${c.id}`} title={c.title ?? ""}>
            {c.title || `#${c.id}`}
          </NavLink>
        ))}
      </nav>

      <div className="chat-thread">
        <h1>{t.title}</h1>
        {messages.isError && <ErrorBox error={messages.error} />}
        {history.length === 0 && !pending && <p className="notice">{t.empty}</p>}
        {history.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="bubble-user">
              {m.content}
            </div>
          ) : m.status === "action_preview" && m.meta?.action ? (
            <ActionProposal key={m.id} data={m.meta.action} />
          ) : (
            <AnswerView
              key={m.id}
              status={m.status ?? "failed"}
              grade={m.grade}
              text={m.content}
              conditions={m.meta?.conditions ?? null}
              graphAsOf={m.meta?.graph_as_of ?? null}
              citations={m.meta?.citations ?? []}
              failureReason={m.meta?.failure_reason ?? null}
              selected={selected}
              onSelect={setSelected}
            />
          ),
        )}
        {pending && <PendingTurn p={pending} selected={selected} onSelect={setSelected} />}
        {sendError !== null && <ErrorBox error={sendError} />}

        <form className="chat-input" onSubmit={send}>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKey}
            placeholder={t.placeholder}
            maxLength={1000}
            rows={2}
            disabled={!!pending}
            aria-label={t.placeholder}
          />
          <button type="submit" disabled={!!pending || !question.trim()}>
            {pending ? t.sending : t.send}
          </button>
        </form>
      </div>

      <div>
        {selected && <ObjectCardPanel selection={selected} onClose={() => setSelected(null)} />}
      </div>
    </section>
  );
}

function PendingTurn({
  p,
  selected,
  onSelect,
}: {
  p: Pending;
  selected: Selection | null;
  onSelect: (sel: Selection) => void;
}) {
  return (
    <>
      <div className="bubble-user">{p.question}</div>
      {p.action ? (
        <ActionProposal data={p.action} />
      ) : p.error ? (
        <AnswerView
          status="failed"
          grade={null}
          text={p.error.message}
          conditions={p.conditions}
          graphAsOf={p.graphAsOf}
          citations={[]}
          failureReason={p.error.reason}
          selected={selected}
          onSelect={onSelect}
        />
      ) : p.answer ? (
        <AnswerView
          status={p.answer.status}
          grade={p.answer.grade}
          text={p.answer.text}
          conditions={p.conditions}
          graphAsOf={p.graphAsOf}
          citations={p.citations}
          failureReason={null}
          selected={selected}
          onSelect={onSelect}
        />
      ) : (
        <div className="answer">
          {p.conditions && (
            <div className="conditions">
              {t.conditions}: {p.conditions}
            </div>
          )}
          <div className="stage">{p.stage ? t.stage[p.stage] : t.sending}</div>
        </div>
      )}
    </>
  );
}
