from fastapi import APIRouter

router = APIRouter(prefix="/deals", tags=["deals"])


@router.get("/")
async def index() -> dict[str, str]:
    return {"status": "not implemented"}
