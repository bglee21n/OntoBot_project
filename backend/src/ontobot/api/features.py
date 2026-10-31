"""화면 기능 플래그(Phase 18, TRD 17.1 결정 13) — 판정은 ``ontobot.features``."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ontobot.api.deps import SettingsDep, User
from ontobot.features import features

router = APIRouter(prefix="/config", tags=["config"])


class FeaturesOut(BaseModel):
    closed_network: bool
    voice_input: bool


@router.get("/features", response_model=FeaturesOut)
async def get_features(settings: SettingsDep, _: User) -> FeaturesOut:
    f = features(settings)
    return FeaturesOut(closed_network=f.closed_network, voice_input=f.voice_input)
