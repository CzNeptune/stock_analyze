from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.conf.path import STATIC_DIR

router = APIRouter(prefix="/stock")


@router.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
