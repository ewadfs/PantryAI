from fastapi import APIRouter

router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.get("/")
async def index() -> dict[str, str]:
    return {"status": "not implemented"}
