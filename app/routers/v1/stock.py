from fastapi import APIRouter

from app.conf.path import REPORT_DIR_URI
from app.core.enums import SearchType
from app.core.logger import log
from app.gen_html.quant_signal import analyze as quant_analyze
from app.gen_html.stock_evaluator import analyze as expect_analyze
from app.gen_html.stock_report import analyze as report_analyze

router = APIRouter(prefix="/stock/api/v1")


@router.get("/search")
async def search(keyword: str, searchType: SearchType):
    log.info(f"search: keyword: {keyword}, searchType: {searchType}")
    if searchType == SearchType.REPORT:
        log.info("分析财报")
        name, filename = report_analyze(keyword)
        if name and filename:
            return {
                "pageUrl": f"{REPORT_DIR_URI}/{filename}",
                "title": f"财报分析:{name}"
            }
    elif searchType == SearchType.EXPECT:
        log.info("分析预期")
        name, filename = expect_analyze(keyword)
        if name and filename:
            return {
                "pageUrl": f"{REPORT_DIR_URI}/{filename}",
                "title": f"预期分析:{name}"
            }

    elif searchType == SearchType.QUANT:
        log.info("分析量化")
        name, filename = quant_analyze(keyword)
        if name and filename:
            return {
                "pageUrl": f"{REPORT_DIR_URI}/{filename}",
                "title": f"量化分析:{name}"
            }
    return {
        "pageUrl": "",
        "title": f"无法处理:{keyword}"
    }
