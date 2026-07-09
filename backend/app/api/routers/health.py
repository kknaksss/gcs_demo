from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, str]:
    # DB/Redis 연결 검사는 구현 work에서 채운다 (ARCH-001 §11 metrics 기본값).
    return {"status": "ready"}
