from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok"]


@router.get("/health", tags=["health"], response_model=Health)
def health_check() -> dict[str, str]:
    return {"status": "ok"}
