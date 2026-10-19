# ADR-0003: 객체 속성 jsonb 단일 테이블

- 상태: 채택 (2026-10-19)
- 근거: TRD 6.2

## 결정

모든 타입의 객체를 `object(type, pk, properties jsonb)` 한 테이블에 둔다. `indexed=true` 속성은 게시 시 작업(`create_property_index`)이 표현식 인덱스를 만든다.

## 재검토 조건

타입별 객체가 수백만 건을 넘거나 필터 성능이 목표에 못 미치면 타입별 테이블로 재검토한다.
