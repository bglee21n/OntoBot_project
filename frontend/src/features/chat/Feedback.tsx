import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type { FeedbackOut, MessageOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.chat.feedback;
type Rating = NonNullable<MessageOut["feedback"]>;

/** 답 하나의 좋아요·싫어요(Phase 15). 싫어요는 평가 화면에서 골든 문항 후보가 된다. */
export function FeedbackButtons({ messageId, rating }: { messageId: number; rating: Rating | null }) {
  const qc = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState("");
  const send = useMutation({
    mutationFn: (body: { rating: Rating; comment?: string }) =>
      api.put<FeedbackOut>(`/messages/${messageId}/feedback`, body),
    onSuccess: () => {
      setAsking(false);
      setComment("");
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    send.mutate({ rating: "down", comment: comment.trim() || undefined });
  };
  return (
    <div className="feedback">
      <button
        type="button"
        className={rating === "up" ? "selected" : ""}
        aria-pressed={rating === "up"}
        title={t.up}
        disabled={send.isPending}
        onClick={() => send.mutate({ rating: "up" })}
      >
        👍
      </button>
      <button
        type="button"
        className={rating === "down" ? "selected" : ""}
        aria-pressed={rating === "down"}
        title={t.down}
        disabled={send.isPending}
        onClick={() => setAsking(!asking)}
      >
        👎
      </button>
      {rating && !asking && <span className="muted">{t.thanks}</span>}
      {asking && (
        <form className="row" onSubmit={submit}>
          <input
            aria-label={t.comment}
            placeholder={t.commentHint}
            value={comment}
            maxLength={1000}
            onChange={(e) => setComment(e.target.value)}
          />
          <button type="submit" disabled={send.isPending}>
            {t.send}
          </button>
        </form>
      )}
      {send.isError && <ErrorBox error={send.error} />}
    </div>
  );
}
