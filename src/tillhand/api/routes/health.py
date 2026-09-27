"""The health check Railway polls. Liveness only: it touches no dependency, so a slow Neon or Pinecone
shows up as tool timeouts rather than as the platform restarting a healthy process."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/healthz")
async def health() -> Health:
    return Health()
