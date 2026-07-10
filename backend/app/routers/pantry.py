from fastapi import APIRouter

router = APIRouter(prefix="/pantry", tags=["pantry"])


@router.get("/")
async def index() -> dict[str, str]:
    return {"status": "not implemented"}
