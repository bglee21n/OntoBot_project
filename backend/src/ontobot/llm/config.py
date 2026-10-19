"""역할별 공급자·모델 설정 읽기(TRD 10절)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: Literal["ollama", "openai_compat", "anthropic"]
    base_url: str | None = None
    api_key_env: str | None = None
    # 외부 공급자면 PII 를 가역 토큰으로 바꿔 보낸다(Phase 5, TRD 9.2).
    external: bool = False


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    timeout_s: float = 60


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig]
    roles: dict[str, RoleConfig]

    def role(self, name: str) -> tuple[RoleConfig, ProviderConfig]:
        role = self.roles[name]
        return role, self.providers[role.provider]


def load(path: Path) -> LlmConfig:
    cfg = LlmConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    missing = {r.provider for r in cfg.roles.values()} - cfg.providers.keys()
    if missing:
        raise ValueError(f"정의되지 않은 공급자: {sorted(missing)}")
    return cfg
