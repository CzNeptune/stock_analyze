import json
import os
import subprocess
from enum import Enum
from enum import unique

from app.conf.path import JS_DIR


def normalize_code(raw):
    """
    智能识别股票代码前缀，支持直接输入数字代码。
    规则：
      A股6位数字: 60/68/69开头 → sh(沪市), 00/30/20开头 → sz(深市), 43/83/87/88/92开头 → bj(北交所)
      港股: 5位或4位数字 → hk
      美股: 纯字母 → us
      已带前缀(sh/sz/hk/us/bj): 原样返回
    """
    code = raw.strip().lower()

    # 已带前缀
    for prefix in ("sh", "sz", "bj", "hk", "us"):
        if code.startswith(prefix) and len(code) > len(prefix):
            return code

    # 纯字母 → 美股
    if code.isalpha():
        return "us" + code.upper()

    # 纯数字 → 按位数和开头判断市场
    if code.isdigit():
        n = len(code)
        if n == 6:
            # A股
            head = code[:2]
            if head in ("60", "68", "69"):
                return "sh" + code
            elif head in ("00", "30", "20", "02", "31"):
                return "sz" + code
            elif head in ("43", "83", "87", "88", "92"):
                return "bj" + code
            else:
                # 兜底：6开头沪市，其余深市
                return ("sh" if code[0] == "6" else "sz") + code
        elif n == 5:
            # 港股5位
            return "hk" + code
        elif n == 4:
            # 港股4位（部分老代码）
            return "hk" + code
        elif n == 1:
            # 港股1位代码（极少，如0001长和实际是5位）
            return "hk" + code.zfill(5)

    # 无法识别，原样返回（让 westock-data 自己报错）
    return code


# ============================================================
# 通过node工具查询数据, 返回json对象, 可通过环境变量设置路径
# ============================================================
DEFAULT_NODE = "node"
DEFAULT_WESTOCK_DATA = JS_DIR / "westock-data.js"
DEFAULT_WESTOCK_TOOL = JS_DIR / "westock-tool.js"

NODE_BIN = os.environ.get("NOTE_BIN", DEFAULT_NODE)
WESTOCK_DATA = os.environ.get("WESTOCK_DATA", DEFAULT_WESTOCK_DATA)
WESTOCK_TOOL = os.environ.get("WESTOCK_TOOL", DEFAULT_WESTOCK_TOOL)


@unique
class WeStockType(str, Enum):
    DATA = "data"
    TOOL = "tool"


def fetch_data_from_script(script_type: WeStockType, args: list[str]):
    if script_type == WeStockType.DATA:
        cmd = [NODE_BIN, WESTOCK_DATA] + args + ["--raw"]
    elif script_type == WeStockType.TOOL:
        cmd = [NODE_BIN, WESTOCK_TOOL] + args + ["--raw"]
    else:
        raise ValueError(f"Invalid script type: {script_type}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("westock-data 调用超时(30s)")
    except FileNotFoundError:
        raise RuntimeError("westock-data 未找到, 请检查命令路径")

    if result.returncode != 0:
        raise RuntimeError(f"westock-data 执行失败：{result.stderr[:200]}")

    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("westock-data 返回空输出")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"westock-data 返回非 JSON：{stdout[:200]}")


def get_stock_list_from_name(name: str):
    # 根据股票名称查询股票信息 (名称, 代码)
    # 返回列表 [{"code": xx, "name": xx, "type": xx}...]
    result = fetch_data_from_script(WeStockType.DATA, ["search", name])
    return result
