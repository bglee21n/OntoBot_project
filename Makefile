# 명령의 정본은 scripts/task.py 다. 여기서는 부르기만 한다(Windows 에서는 task.py 를 직접 실행).
TASK = uv run --project backend python scripts/task.py

.PHONY: sync lint test-unit test-int test-contract eval build dev-api dev-web
sync lint test-unit test-int test-contract eval build dev-api dev-web:
	$(TASK) $@
