"""설정 — 환경변수(접두어 ``ONTOBOT_``)와 ``.env`` 파일에서 읽는다(TRD 9.3).

비밀값(DB 비밀번호 등)은 여기서만 받고 DB·로그에는 남기지 않는다. ``SecretStr``이라 ``repr``·
로그에 값이 찍히지 않는다.

DB 접속 URL은 문자열을 이어 붙이지 않고 ``URL.create``로 조립한다. 비밀번호에 ``!``·``@`` 같은
글자가 있으면 문자열 조립은 ``@`` 를 호스트 구분자로 읽어 **엉뚱한 호스트로 접속을 시도**한다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# 저장소 루트의 .env — 백엔드와 설치 스크립트(deploy/windows)가 같은 파일을 쓴다.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ONTOBOT_",
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"

    # PostgreSQL — 기록의 원본(TRD 5절)
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "ontobot_db"
    db_user: str = "ontobot"
    db_password: SecretStr = SecretStr("")
    # 연결 풀(프로세스 하나당). 채팅 한 건은 답이 끝날 때까지(LLM 호출 포함) 연결 하나를 쥔다 —
    # 풀 크기가 곧 API 프로세스의 동시 채팅 상한이다. 기본값(5+10)에서 동시 16명이 첫 이벤트를
    # 3~6초 기다렸다(Phase 8 부하 측정, ADR-0011). PostgreSQL max_connections(기본 100) 안에서
    # api·worker 를 합쳐 잡는다.
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout_s: float = 30
    # 통합 테스트가 스키마를 지우고 다시 만드는 DB. 개발 DB(db_name)와 반드시 달라야 한다.
    test_db_name: str = "ontobot_test"

    # Neo4j — 링크 투영(TRD 5절). 사용은 Phase 2부터.
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")

    # 통합 테스트 전용 Neo4j 인스턴스. Community는 사용자 DB가 하나뿐이고 테스트가 그래프를
    # 비우므로 개발 인스턴스와 반드시 분리한다(ADR-0007). 운영 설정에는 두지 않는다.
    test_neo4j_uri: str = "bolt://localhost:7688"
    test_neo4j_user: str = "neo4j"
    test_neo4j_password: SecretStr = SecretStr("")

    # LLM 역할별 공급자·모델 설정 파일(TRD 10절).
    llm_config_path: Path = _REPO_ROOT / "config" / "llm.yaml"

    # 등급 ②(일부 근거)를 허용할지. 끄면 ②를 ③으로 처리한다(TRD 2절 4번, 8.2-6).
    grade2_enabled: bool = True

    # 인증(TRD 9.1). 로컬 계정은 IdP 가 없는 폐쇄망·개발용이며 운영에서 끌 수 있다.
    local_auth_enabled: bool = True
    session_cookie_name: str = "ontobot_session"
    session_ttl_hours: int = 8
    # 운영(HTTPS)에서는 반드시 true. 개발 http://localhost 에서는 false 여야 쿠키가 붙는다.
    cookie_secure: bool = False
    # 상태를 바꾸는 요청(POST·PATCH·DELETE)의 Origin 허용 목록 — SameSite=Lax 에 더한 CSRF 방어.
    allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:8000"]

    # OIDC(Authorization Code + PKCE). issuer 가 비면 OIDC 는 꺼진다(로컬 계정만).
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")  # 공개 클라이언트(PKCE 만)면 비워 둔다
    oidc_redirect_uri: str = "http://localhost:8000/api/v1/auth/callback"
    oidc_scopes: str = "openid profile email"
    # 처음 로그인한 OIDC 사용자에게 줄 역할. 데이터 읽기 권한은 별도(type_permission)다.
    oidc_default_roles: list[str] = ["user"]
    oidc_login_ttl_seconds: int = 600
    # 로그인 뒤 돌아갈 화면의 오리진. 운영(백엔드가 프론트를 서빙)은 비워 둔다. 개발은 Vite 주소.
    frontend_base_url: str = ""
    # 프론트 빌드 산출물(frontend/dist). 설치본은 API 가 같은 오리진에서 서빙한다.
    # 개발은 비운다(Vite 가 서빙).
    frontend_dist: Path | None = None

    # 보존 기간(일, TRD 9.3). 0 이면 지우지 않는다. 만료분은 워커가 매일 지운다(tracing.retention).
    # 감사 로그는 DB 트리거로 추가 전용이라 여기서 지우지 않는다 — 보관·이관은 운영 절차(P1)다.
    retention_trace_days: int = 90
    retention_message_days: int = 365
    retention_utterance_days: int = 180

    # 로그(TRD 9.3·15절): JSON 한 줄. log_dir 이 있으면 파일로도 남기고 크기로 회전한다.
    log_level: str = "INFO"
    log_dir: Path | None = None
    log_max_bytes: int = 20 * 1024 * 1024
    log_backups: int = 10

    @field_validator("frontend_dist", "log_dir", mode="before")
    @classmethod
    def _empty_path_is_none(cls, v: object) -> object:
        # env.example 의 빈 값(``ONTOBOT_LOG_DIR=``)은 "없음"이다.
        # 그대로 두면 Path("") 가 현재 폴더가 된다.
        return None if v == "" else v

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)

    def database_url(self, *, driver: str = "postgresql+asyncpg") -> URL:
        return URL.create(
            drivername=driver,
            username=self.db_user,
            password=self.db_password.get_secret_value() or None,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
