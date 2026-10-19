import { describeError } from "../api/client";

/** 오류 한 줄 + 세부 목록(게시 전 검사·검증 오류). 실패를 빈 화면으로 숨기지 않는다. */
export function ErrorBox({ error }: { error: unknown }) {
  const { title, items } = describeError(error);
  return (
    <div className="error" role="alert">
      <strong>{title}</strong>
      {items.length > 0 && (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
