from fastapi import APIRouter

router = APIRouter(prefix="/shopping", tags=["shopping"])


@router.get("/")
async def index() -> dict[str, str]:
    return {"status": "not implemented"}
