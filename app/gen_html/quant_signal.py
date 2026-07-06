#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化买卖信号判断工具
=====================
多指标综合评分（均线交叉 + MACD + RSI + 成交量），输出短/中/长线买卖信号及预期价格区间。

用法：
    python quant_signal.py <股票代码>
    python quant_signal.py 600519      # 直接输入数字，自动识别
    python quant_signal.py 000858
    python quant_signal.py 00700
    python quant_signal.py AAPL
    python quant_signal.py sh600519     # 也支持带前缀

数据来源：westock-data（腾讯自选股行情接口）
依赖：仅 Python 标准库，零外部依赖
"""

import json
import math
import os
import subprocess
import time
from datetime import datetime

from app.conf.path import JS_DIR
from app.conf.path import REPORT_DIR
from app.core.logger import log

# ============================================================
# 配置：westock-data CLI 路径（可通过环境变量覆盖）
# ============================================================
DEFAULT_NODE = "node"
DEFAULT_SCRIPT = JS_DIR / "westock-data.js"

NODE_BIN = os.environ.get("WESTOCK_NODE", DEFAULT_NODE)
SCRIPT_PATH = os.environ.get("WESTOCK_SCRIPT", DEFAULT_SCRIPT)


# ============================================================
# 第一层：数据获取
# ============================================================

def run_westock(args):
    """调用 westock-data CLI，返回解析后的 JSON 对象。"""
    cmd = [NODE_BIN, SCRIPT_PATH] + args + ["--raw"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("westock-data 调用超时（30s）")
    except FileNotFoundError:
        raise RuntimeError(f"找不到 Node.js：{NODE_BIN}，请设置 WESTOCK_NODE 环境变量")

    if result.returncode != 0:
        raise RuntimeError(f"westock-data 执行失败：{result.stderr[:200]}")

    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("westock-data 返回空输出")

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"westock-data 返回非 JSON：{stdout[:200]}")


def get_quote(code):
    """获取实时行情。"""
    data = run_westock(["quote", code])
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    return data


def get_kline(code, period="day", limit=120):
    """获取 K 线数据，返回列表（按日期升序）。"""
    data = run_westock(["kline", code, "--period", period, "--limit", str(limit)])
    if isinstance(data, list):
        data.reverse()  # westock-data 返回降序，转为升序方便计算
        return data
    return []


# ============================================================
# 第二层：技术指标计算（纯 Python 实现）
# ============================================================

def calc_ma(closes, n):
    """简单移动平均，返回与 closes 等长的列表（前 n-1 个为 None）。"""
    result = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        result[i] = sum(closes[i - n + 1: i + 1]) / n
    return result


def calc_ema(values, n):
    """指数移动平均。"""
    if len(values) == 0:
        return []
    alpha = 2 / (n + 1)
    result = [values[0]]
    for i in range(1, len(values)):
        result.append(alpha * values[i] + (1 - alpha) * result[-1])
    return result


def calc_macd(closes, fast=12, slow=26, signal=9):
    """计算 MACD 指标，返回 {dif, dea, macd_hist} 三个等长列表。"""
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = calc_ema(dif, signal)
    macd_hist = [2 * (d - e) for d, e in zip(dif, dea)]
    return {"dif": dif, "dea": dea, "hist": macd_hist}


def calc_rsi(closes, n=6):
    """计算 RSI 指标。"""
    if len(closes) < n + 1:
        return [None] * len(closes)
    result = [None] * len(closes)
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    # 第一个 RSI 值用简单平均
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for i in range(n, len(closes)):
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100 - 100 / (1 + rs)
        # 平滑
        if i < len(gains):
            avg_gain = (avg_gain * (n - 1) + gains[i]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i]) / n
    return result


def calc_boll(closes, n=20, k=2):
    """计算布林带，返回 {upper, mid, lower}。"""
    mid = calc_ma(closes, n)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        window = closes[i - n + 1: i + 1]
        std = math.sqrt(sum((x - mid[i]) ** 2 for x in window) / n)
        upper[i] = mid[i] + k * std
        lower[i] = mid[i] - k * std
    return {"upper": upper, "mid": mid, "lower": lower}


def safe_get(lst, idx):
    """安全获取列表元素，越界返回 None。"""
    if lst is None:
        return None
    if -len(lst) <= idx < len(lst):
        return lst[idx]
    return None


# ============================================================
# 第三层：多指标评分引擎
# ============================================================

def score_ma_cross(ma_fast, ma_mid, ma_slow, prev_fast, prev_mid, ma_names=None):
    """
    均线交叉评分（-100 ~ +100）。
    判断多头/空头排列、金叉/死叉、价格与均线关系。
    ma_names: [快线名, 中线名, 慢线名]，用于生成描述文字。
    """
    if ma_names is None:
        ma_names = ["MA5", "MA10", "MA20"]
    n_f, n_m, n_s = ma_names
    score = 0
    reasons = []

    # 多头/空头排列
    if ma_fast is not None and ma_mid is not None and ma_slow is not None:
        if ma_fast > ma_mid > ma_slow:
            score += 40
            reasons.append(f"{n_f}>{n_m}>{n_s} 多头排列(+40)")
        elif ma_fast < ma_mid < ma_slow:
            score -= 40
            reasons.append(f"{n_f}<{n_m}<{n_s} 空头排列(-40)")
        else:
            if ma_fast > ma_mid:
                score += 15
                reasons.append(f"{n_f}>{n_m} 短期偏强(+15)")
            else:
                score -= 15
                reasons.append(f"{n_f}<{n_m} 短期偏弱(-15)")

    # 金叉/死叉（快线穿越中线）
    if ma_fast is not None and ma_mid is not None and prev_fast is not None and prev_mid is not None:
        if prev_fast <= prev_mid and ma_fast > ma_mid:
            score += 30
            reasons.append(f"{n_f} 上穿 {n_m} 金叉(+30)")
        elif prev_fast >= prev_mid and ma_fast < ma_mid:
            score -= 30
            reasons.append(f"{n_f} 下穿 {n_m} 死叉(-30)")

    return max(-100, min(100, score)), reasons


def score_macd(dif, dea, hist, prev_dif, prev_dea):
    """MACD 评分（-100 ~ +100）。"""
    score = 0
    reasons = []

    # DIF 与 DEA 关系
    if dif is not None and dea is not None:
        if dif > dea:
            score += 25
            reasons.append("DIF>DEA 金叉状态(+25)")
        else:
            score -= 25
            reasons.append("DIF<DEA 死叉状态(-25)")

    # 金叉/死叉信号
    if dif is not None and dea is not None and prev_dif is not None and prev_dea is not None:
        if prev_dif <= prev_dea and dif > dea:
            score += 25
            reasons.append("MACD 今日金叉(+25)")
        elif prev_dif >= prev_dea and dif < dea:
            score -= 25
            reasons.append("MACD 今日死叉(-25)")

    # MACD 柱状线变化
    if hist is not None:
        if hist > 0:
            score += 15
            reasons.append(f"MACD柱>0 红柱(+15)")
        else:
            score -= 15
            reasons.append(f"MACD柱<0 绿柱(-15)")

    # DIF 零轴位置
    if dif is not None:
        if dif > 0:
            score += 10
            reasons.append("DIF>0 零轴上方(+10)")
        else:
            score -= 10
            reasons.append("DIF<0 零轴下方(-10)")

    return max(-100, min(100, score)), reasons


def score_rsi(rsi6, rsi12=None):
    """RSI 评分（-100 ~ +100）。"""
    score = 0
    reasons = []

    if rsi6 is None:
        return 0, ["RSI 数据不足"]

    if rsi6 < 20:
        score = 50
        reasons.append(f"RSI6={rsi6:.1f} 严重超卖(+50)")
    elif rsi6 < 30:
        score = 30
        reasons.append(f"RSI6={rsi6:.1f} 超卖(+30)")
    elif rsi6 < 40:
        score = 10
        reasons.append(f"RSI6={rsi6:.1f} 偏弱(+10)")
    elif rsi6 <= 60:
        score = 0
        reasons.append(f"RSI6={rsi6:.1f} 中性区间(0)")
    elif rsi6 <= 70:
        score = -10
        reasons.append(f"RSI6={rsi6:.1f} 偏强(-10)")
    elif rsi6 <= 80:
        score = -30
        reasons.append(f"RSI6={rsi6:.1f} 超买(-30)")
    else:
        score = -50
        reasons.append(f"RSI6={rsi6:.1f} 严重超买(-50)")

    # RSI6 vs RSI12 背离
    if rsi12 is not None:
        if rsi6 > rsi12:
            score += 5
        else:
            score -= 5

    return max(-100, min(100, score)), reasons


def score_volume(volumes, closes, idx):
    """成交量评分（-100 ~ +100）。"""
    score = 0
    reasons = []

    if idx < 20 or len(volumes) <= idx:
        return 0, ["成交量数据不足"]

    today_vol = volumes[idx]
    ma5_vol = sum(volumes[idx - 4: idx + 1]) / 5
    ma20_vol = sum(volumes[idx - 19: idx + 1]) / 20
    price_chg = closes[idx] - closes[idx - 1] if idx > 0 else 0
    price_up = price_chg > 0

    # 放量/缩量
    if ma5_vol > 0:
        vol_ratio = today_vol / ma5_vol
    else:
        vol_ratio = 1.0

    if vol_ratio > 1.5:
        if price_up:
            score += 30
            reasons.append(f"放量上涨 量比{vol_ratio:.2f}(+30)")
        else:
            score -= 30
            reasons.append(f"放量下跌 量比{vol_ratio:.2f}(-30)")
    elif vol_ratio < 0.5:
        if price_up:
            score -= 10
            reasons.append(f"缩量上涨 量比{vol_ratio:.2f} 量价背离(-10)")
        else:
            score += 10
            reasons.append(f"缩量下跌 量比{vol_ratio:.2f} 卖压减弱(+10)")

    # 量能趋势
    if ma5_vol > ma20_vol:
        score += 15
        reasons.append("5日均量>20日均量 量能放大(+15)")
    else:
        score -= 15
        reasons.append("5日均量<20日均量 量能萎缩(-15)")

    return max(-100, min(100, score)), reasons


def calc_composite(scores):
    """
    综合评分 = 均线(30%) + MACD(30%) + RSI(20%) + 成交量(20%)
    返回 -100 ~ +100。
    """
    weights = {"ma": 0.30, "macd": 0.30, "rsi": 0.20, "vol": 0.20}
    total = sum(scores.get(k, 0) * w for k, w in weights.items())
    return max(-100, min(100, round(total, 1)))


def score_to_signal(score):
    """综合评分映射到买卖信号。"""
    if score >= 30:
        return "买入", "强"
    elif score >= 10:
        return "偏多", "中"
    elif score > -10:
        return "持有", "弱"
    elif score > -30:
        return "偏空", "中"
    else:
        return "卖出", "强"


# ============================================================
# 第四层：短/中/长线信号判断 + 预期价格区间
# ============================================================

def calc_price_range(closes, highs, lows, boll, ma_list, period_label):
    """
    计算预期价格区间。
    返回 {"support": 支撑价, "resistance": 压力价, "current": 当前价, "reason": 理由}
    """
    current = closes[-1]
    reasons = []

    if period_label == "short":
        # 短线：布林带上下轨 + 近5日高低点
        boll_lower = safe_get(boll["lower"], -1)
        boll_upper = safe_get(boll["upper"], -1)
        low_5d = min(lows[-5:]) if len(lows) >= 5 else min(lows)
        high_5d = max(highs[-5:]) if len(highs) >= 5 else max(highs)

        support = min(filter(lambda x: x is not None and x > 0, [boll_lower, low_5d]),
                      default=current * 0.95)
        resistance = max(filter(lambda x: x is not None and x > 0, [boll_upper, high_5d]),
                         default=current * 1.05)
        reasons = [f"布林下轨 {boll_lower:.2f}" if boll_lower else "",
                   f"近5日最低 {low_5d:.2f}",
                   f"布林上轨 {boll_upper:.2f}" if boll_upper else "",
                   f"近5日最高 {high_5d:.2f}"]

    elif period_label == "mid":
        # 中线：MA20/MA60 + 近20日波动区间
        ma20 = safe_get(ma_list["ma20"], -1)
        ma60 = safe_get(ma_list["ma60"], -1)
        low_20d = min(lows[-20:]) if len(lows) >= 20 else min(lows)
        high_20d = max(highs[-20:]) if len(highs) >= 20 else max(highs)

        candidates_support = [x for x in [ma60, low_20d] if x is not None and x > 0]
        candidates_resist = [x for x in [ma20, high_20d] if x is not None and x > 0]
        support = min(candidates_support, default=current * 0.92)
        resistance = max(candidates_resist, default=current * 1.08)
        reasons = [f"MA60 {ma60:.2f}" if ma60 else "",
                   f"近20日最低 {low_20d:.2f}",
                   f"MA20 {ma20:.2f}" if ma20 else "",
                   f"近20日最高 {high_20d:.2f}"]

    else:
        # 长线：MA120/MA250 + 52周高低点
        ma120 = safe_get(ma_list.get("ma120"), -1)
        ma250 = safe_get(ma_list.get("ma250"), -1)
        lookback = min(250, len(lows))
        low_long = min(lows[-lookback:]) if lookback > 0 else current * 0.8
        high_long = max(highs[-lookback:]) if lookback > 0 else current * 1.2

        candidates_support = [x for x in [ma250, low_long] if x is not None and x > 0]
        candidates_resist = [x for x in [ma120, high_long] if x is not None and x > 0]
        support = min(candidates_support, default=current * 0.85)
        resistance = max(candidates_resist, default=current * 1.15)
        reasons = [f"MA250 {ma250:.2f}" if ma250 else "",
                   f"近{lookback}日最低 {low_long:.2f}",
                   f"MA120 {ma120:.2f}" if ma120 else "",
                   f"近{lookback}日最高 {high_long:.2f}"]

    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "current": round(current, 2),
        "reasons": [r for r in reasons if r]
    }


def analyze_term(closes, highs, lows, volumes, boll, ma_dict, macd_data, rsi_data, term):
    """
    计算某一时间维度的综合信号。
    term: "short" | "mid" | "long"
    """
    n = len(closes)
    if n < 60:
        return None

    idx = n - 1  # 最新交易日

    # 根据时间维度选择指标参数
    if term == "short":
        rsi_n = 6
        ma_keys = ["ma5", "ma10", "ma20"]
    elif term == "mid":
        rsi_n = 12
        ma_keys = ["ma10", "ma20", "ma60"]
    else:
        rsi_n = 24
        ma_keys = ["ma20", "ma60", "ma120"]

    # 获取当前和前一日指标值
    ma_cur = {k: safe_get(ma_dict.get(k), idx) for k in ma_keys}
    ma_prev = {k: safe_get(ma_dict.get(k), idx - 1) for k in ma_keys}

    # 均线评分
    ma_names = [k.upper() for k in ma_keys]
    ma_score, ma_reasons = score_ma_cross(
        ma_cur.get(ma_keys[0]), ma_cur.get(ma_keys[1]), ma_cur.get(ma_keys[2]),
        ma_prev.get(ma_keys[0]), ma_prev.get(ma_keys[1]),
        ma_names=ma_names
    )

    # MACD 评分
    macd_score, macd_reasons = score_macd(
        safe_get(macd_data["dif"], idx), safe_get(macd_data["dea"], idx),
        safe_get(macd_data["hist"], idx),
        safe_get(macd_data["dif"], idx - 1), safe_get(macd_data["dea"], idx - 1)
    )

    # RSI 评分
    rsi_key = f"rsi{rsi_n}"
    rsi_val = safe_get(rsi_data.get(rsi_key), idx)
    rsi_val_alt = safe_get(rsi_data.get(f"rsi{rsi_n // 2 if rsi_n > 6 else 12}"), idx)
    rsi_score, rsi_reasons = score_rsi(rsi_val, rsi_val_alt)

    # 成交量评分
    vol_score, vol_reasons = score_volume(volumes, closes, idx)

    # 综合评分
    composite = calc_composite({"ma": ma_score, "macd": macd_score,
                                "rsi": rsi_score, "vol": vol_score})
    signal, strength = score_to_signal(composite)

    # 预期价格区间
    price_range = calc_price_range(closes, highs, lows, boll, ma_dict, term)

    return {
        "term": term,
        "term_label": {"short": "短线(1-5日)", "mid": "中线(1-4周)", "long": "长线(1-3月)"}[term],
        "scores": {"ma": ma_score, "macd": macd_score, "rsi": rsi_score, "vol": vol_score},
        "composite": composite,
        "signal": signal,
        "strength": strength,
        "reasons": ma_reasons + macd_reasons + rsi_reasons + vol_reasons,
        "price_range": price_range,
        "indicators": {
            "ma": {k: round(v, 2) if v else None for k, v in ma_cur.items()},
            "macd": {
                "dif": round(safe_get(macd_data["dif"], idx), 4) if safe_get(macd_data["dif"], idx) else None,
                "dea": round(safe_get(macd_data["dea"], idx), 4) if safe_get(macd_data["dea"], idx) else None,
                "hist": round(safe_get(macd_data["hist"], idx), 4) if safe_get(macd_data["hist"], idx) else None,
            },
            "rsi": round(rsi_val, 2) if rsi_val else None,
            "boll": {
                "upper": round(safe_get(boll["upper"], idx), 2) if safe_get(boll["upper"], idx) else None,
                "mid": round(safe_get(boll["mid"], idx), 2) if safe_get(boll["mid"], idx) else None,
                "lower": round(safe_get(boll["lower"], idx), 2) if safe_get(boll["lower"], idx) else None,
            }
        }
    }


# ============================================================
# 第五层：终端输出
# ============================================================

# ANSI 颜色
class C:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


def signal_color(signal):
    if signal in ("买入", "偏多"):
        return C.GREEN
    elif signal in ("卖出", "偏空"):
        return C.RED
    else:
        return C.YELLOW


def score_bar(score):
    """生成 -100~+100 的进度条。"""
    width = 30
    mid = width // 2
    pos = int(score / 100 * mid)
    if pos >= 0:
        bar = "─" * mid + "│" + C.GREEN + "█" * pos + C.END + "─" * (mid - pos)
    else:
        pos = abs(pos)
        bar = "─" * (mid - pos) + C.RED + "█" * pos + C.END + "│" + "─" * mid
    return f"[{bar}] {score:+.1f}"


def print_terminal(code, name, quote, terms, kline_data):
    """终端打印完整分析结果。"""
    price = quote.get("price", 0)
    pe = quote.get("pe_ratio", 0)
    pb = quote.get("pb_ratio", 0)
    chg_pct = quote.get("change_percent", 0)
    mkt_cap = quote.get("total_market_cap", 0)

    log.info("")
    log.info(f"{C.BOLD}{'=' * 64}{C.END}")
    log.info(f"{C.BOLD}  {name} ({code})  量化买卖信号分析{C.END}")
    log.info(f"  日期: {kline_data[-1]['date'] if kline_data else 'N/A'}    生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info(f"{C.BOLD}{'=' * 64}{C.END}")
    log.info("")

    # 行情概览
    chg_color = C.RED if chg_pct > 0 else C.GREEN if chg_pct < 0 else C.YELLOW
    mkt_str = f"{mkt_cap / 1e8:.0f}亿" if mkt_cap else "N/A"
    log.info(f"  现价: {C.BOLD}{price}{C.END}  涨跌: {chg_color}{chg_pct:+.2f}%{C.END}  "
             f"PE: {pe:.1f}  PB: {pb:.2f}  市值: {mkt_str}")
    log.info("")

    # 三个时间维度的信号
    for term_result in terms:
        if not term_result:
            continue
        label = term_result["term_label"]
        signal = term_result["signal"]
        composite = term_result["composite"]
        color = signal_color(signal)
        strength = term_result["strength"]

        log.info(f"{C.BOLD}  ┌─ {label} {C.END}")
        log.info(f"{C.BOLD}  │ 信号: {color}{C.BOLD}{signal}{C.END}{color}({strength}){C.END}  "
                 f"综合评分: {composite:+.1f}{C.END}")
        log.info(f"  │ {score_bar(composite)}")
        log.info(f"  │")

        # 分项评分
        s = term_result["scores"]
        log.info(f"  │  均线: {s['ma']:+6.1f}   MACD: {s['macd']:+6.1f}   "
                 f"RSI: {s['rsi']:+6.1f}   量能: {s['vol']:+6.1f}")

        # 预期价格区间
        pr = term_result["price_range"]
        upside = (pr["resistance"] - pr["current"]) / pr["current"] * 100 if pr["current"] else 0
        downside = (pr["support"] - pr["current"]) / pr["current"] * 100 if pr["current"] else 0
        log.info(f"  │  支撑: {C.GREEN}{pr['support']}{C.END} ({downside:+.1f}%)  "
                 f"压力: {C.RED}{pr['resistance']}{C.END} ({upside:+.1f}%)  "
                 f"当前: {pr['current']}")

        # 理由
        log.info(f"  │  理由:")
        for r in term_result["reasons"]:
            if r:
                log.info(f"  │    · {r}")

        log.info(f"{C.BOLD}  └{'─' * 60}{C.END}")
        log.info("")

    # 关键指标速览
    if terms and terms[0]:
        ind = terms[0]["indicators"]
        log.info(f"{C.DIM}  关键指标(日线):{C.END}")
        ma_str = "  ".join(f"{k.upper()}={v}" for k, v in ind["ma"].items() if v)
        log.info(f"  {ma_str}")
        macd = ind["macd"]
        log.info(f"  MACD: DIF={macd['dif']} DEA={macd['dea']} 柱={macd['hist']}")
        log.info(f"  RSI6={ind['rsi']}  BOLL: 上={ind['boll']['upper']} 中={ind['boll']['mid']} 下={ind['boll']['lower']}")
        log.info("")

    log.info(f"{C.DIM}  * 本工具基于多指标量化评分，仅供参考，不构成投资建议。{C.END}")
    log.info(f"{C.DIM}  * 评分规则: 均线(30%)+MACD(30%)+RSI(20%)+成交量(20%)，范围-100~+100{C.END}")
    log.info("")


# ============================================================
# 第六层：HTML 报告生成
# ============================================================

def generate_html(code, name, quote, terms, kline_data, all_indicators):
    """生成 HTML 可视化报告。"""
    price = quote.get("price", 0)
    chg_pct = quote.get("change_percent", 0)
    pe = quote.get("pe_ratio", 0)
    pb = quote.get("pb_ratio", 0)
    mkt_cap = quote.get("total_market_cap", 0)
    mkt_str = f"{mkt_cap / 1e8:.0f}亿" if mkt_cap else "N/A"
    date_str = kline_data[-1]["date"] if kline_data else "N/A"

    # 准备图表数据（取最近60个交易日）
    chart_limit = min(60, len(kline_data))
    chart_data = kline_data[-chart_limit:]
    dates = json.dumps([d["date"] for d in chart_data])
    closes_json = json.dumps([d["last"] for d in chart_data])
    ma5_json = json.dumps([round(x, 2) if x else None for x in all_indicators["ma5"][-chart_limit:]])
    ma10_json = json.dumps([round(x, 2) if x else None for x in all_indicators["ma10"][-chart_limit:]])
    ma20_json = json.dumps([round(x, 2) if x else None for x in all_indicators["ma20"][-chart_limit:]])
    ma60_json = json.dumps([round(x, 2) if x else None for x in all_indicators["ma60"][-chart_limit:]])
    dif_json = json.dumps([round(x, 4) if x else None for x in all_indicators["macd"]["dif"][-chart_limit:]])
    dea_json = json.dumps([round(x, 4) if x else None for x in all_indicators["macd"]["dea"][-chart_limit:]])
    hist_json = json.dumps([round(x, 4) if x else None for x in all_indicators["macd"]["hist"][-chart_limit:]])
    rsi_json = json.dumps([round(x, 2) if x else None for x in all_indicators["rsi6"][-chart_limit:]])
    vol_json = json.dumps([d["volume"] for d in chart_data])
    boll_upper = json.dumps([round(x, 2) if x else None for x in all_indicators["boll"]["upper"][-chart_limit:]])
    boll_lower = json.dumps([round(x, 2) if x else None for x in all_indicators["boll"]["lower"][-chart_limit:]])

    # 信号卡片
    def term_card(t):
        if not t:
            return ""
        color_map = {"买入": "#1D9E75", "偏多": "#639922", "持有": "#854F0B",
                     "偏空": "#D85A30", "卖出": "#E24B4A"}
        bg_map = {"买入": "#E1F5EE", "偏多": "#EAF3DE", "持有": "#FAEEDA",
                  "偏空": "#FAECE7", "卖出": "#FCEBEB"}
        color = color_map.get(t["signal"], "#444")
        bg = bg_map.get(t["signal"], "#F1EFE8")
        pr = t["price_range"]
        upside = (pr["resistance"] - pr["current"]) / pr["current"] * 100 if pr["current"] else 0
        downside = (pr["support"] - pr["current"]) / pr["current"] * 100 if pr["current"] else 0
        reasons_html = "".join(f"<li>{r}</li>" for r in t["reasons"] if r)
        s = t["scores"]
        return f"""
        <div class="term-card" style="background:{bg}; border-left: 4px solid {color};">
          <div class="term-header">
            <span class="term-label">{t["term_label"]}</span>
            <span class="signal-badge" style="background:{color};">{t["signal"]}({t["strength"]})</span>
          </div>
          <div class="composite-score">
            <span class="score-num" style="color:{color};">{t["composite"]:+.1f}</span>
            <div class="score-bar-wrap">
              <div class="score-bar-bg"><div class="score-bar-fill" style="width:{(t["composite"] + 100) / 2}%; background:{color};"></div></div>
              <div class="score-bar-mid"></div>
            </div>
          </div>
          <div class="sub-scores">
            <span>均线 {s["ma"]:+.0f}</span><span>MACD {s["macd"]:+.0f}</span>
            <span>RSI {s["rsi"]:+.0f}</span><span>量能 {s["vol"]:+.0f}</span>
          </div>
          <div class="price-range">
            <div class="range-item support">支撑 <b>{pr["support"]}</b> <span class="pct">({downside:+.1f}%)</span></div>
            <div class="range-item current">当前 <b>{pr["current"]}</b></div>
            <div class="range-item resistance">压力 <b>{pr["resistance"]}</b> <span class="pct">({upside:+.1f}%)</span></div>
          </div>
          <ul class="reasons">{reasons_html}</ul>
        </div>"""

    cards_html = "".join(term_card(t) for t in terms if t)

    chg_color = "#E24B4A" if chg_pct > 0 else "#1D9E75" if chg_pct < 0 else "#854F0B"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}({code}) 量化信号分析</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  :root {{
    --bg: #f8f7f4; --card: #ffffff; --border: #e5e3dd;
    --text: #2c2c2a; --muted: #888780; --primary: #8b0000; --accent: #d4af37;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: "PingFang SC","Microsoft YaHei",sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding: 20px; }}
  .container {{ max-width: 920px; margin: 0 auto; }}
  .header {{ background: var(--card); border-radius: 12px; padding: 24px 28px; margin-bottom: 16px; border: 1px solid var(--border); }}
  .header h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
  .header .subtitle {{ font-size: 13px; color: var(--muted); }}
  .header .metrics {{ display: flex; gap: 24px; margin-top: 16px; flex-wrap: wrap; }}
  .header .metric {{ font-size: 13px; }}
  .header .metric b {{ font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .header .metric .label {{ color: var(--muted); font-size: 12px; }}
  .section {{ background: var(--card); border-radius: 12px; padding: 20px 24px; margin-bottom: 16px; border: 1px solid var(--border); }}
  .section h2 {{ font-size: 15px; font-weight: 600; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }}
  .terms-grid {{ display: grid; grid-template-columns: 1fr; gap: 16px; }}
  .term-card {{ border-radius: 8px; padding: 16px 20px; }}
  .term-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
  .term-label {{ font-size: 14px; font-weight: 600; }}
  .signal-badge {{ color: #fff; padding: 2px 12px; border-radius: 4px; font-size: 13px; font-weight: 600; }}
  .composite-score {{ display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }}
  .score-num {{ font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; min-width: 60px; }}
  .score-bar-wrap {{ flex: 1; position: relative; height: 8px; }}
  .score-bar-bg {{ background: #e0ddd5; border-radius: 4px; height: 100%; overflow: hidden; }}
  .score-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
  .score-bar-mid {{ position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: #aaa; }}
  .sub-scores {{ display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }}
  .price-range {{ display: flex; gap: 12px; margin-bottom: 12px; }}
  .range-item {{ flex: 1; text-align: center; padding: 8px; border-radius: 6px; font-size: 12px; }}
  .range-item.support {{ background: #E1F5EE; }}
  .range-item.current {{ background: #F1EFE8; }}
  .range-item.resistance {{ background: #FAECE7; }}
  .range-item b {{ display: block; font-size: 16px; font-variant-numeric: tabular-nums; }}
  .range-item .pct {{ font-size: 11px; color: var(--muted); }}
  .reasons {{ font-size: 12px; color: var(--muted); list-style: none; }}
  .reasons li {{ padding: 2px 0; }}
  .reasons li::before {{ content: "· "; }}
  .chart-box {{ position: relative; height: 360px; margin-bottom: 16px; }}
  .chart-box.small {{ height: 200px; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 12px; font-size: 11px; color: var(--muted); margin-bottom: 8px; }}
  .legend span {{ display: flex; align-items: center; gap: 4px; }}
  .legend i {{ width: 10px; height: 3px; display: inline-block; }}
  .disclaimer {{ text-align: center; font-size: 12px; color: var(--muted); padding: 16px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{name} <span style="color:var(--muted);font-size:14px;font-weight:400;">({code})</span></h1>
    <div class="subtitle">量化买卖信号分析 | 业绩分析 · 估值建模 · 技术研判 | {date_str}</div>
    <div class="metrics">
      <div class="metric"><div class="label">现价</div><b>{price}</b></div>
      <div class="metric"><div class="label">涨跌幅</div><b style="color:{chg_color};">{chg_pct:+.2f}%</b></div>
      <div class="metric"><div class="label">PE(TTM)</div><b>{pe:.1f}</b></div>
      <div class="metric"><div class="label">PB</div><b>{pb:.2f}</b></div>
      <div class="metric"><div class="label">市值</div><b>{mkt_str}</b></div>
    </div>
  </div>

  <div class="section">
    <h2>短 / 中 / 长线买卖信号</h2>
    <div class="terms-grid">{cards_html}</div>
  </div>

  <div class="section">
    <h2>价格走势与均线</h2>
    <div class="legend">
      <span><i style="background:#2c2c2a;"></i>收盘价</span>
      <span><i style="background:#185FA5;"></i>MA5</span>
      <span><i style="background:#BA7517;"></i>MA10</span>
      <span><i style="background:#1D9E75;"></i>MA20</span>
      <span><i style="background:#993556;"></i>MA60</span>
      <span><i style="background:#888;opacity:0.5;"></i>布林带</span>
    </div>
    <div class="chart-box"><canvas id="priceChart"></canvas></div>
  </div>

  <div class="section">
    <h2>MACD 指标</h2>
    <div class="legend">
      <span><i style="background:#185FA5;"></i>DIF</span>
      <span><i style="background:#BA7517;"></i>DEA</span>
      <span><i style="background:#888;opacity:0.5;"></i>MACD柱</span>
    </div>
    <div class="chart-box small"><canvas id="macdChart"></canvas></div>
  </div>

  <div class="section">
    <h2>RSI 与成交量</h2>
    <div class="legend">
      <span><i style="background:#993556;"></i>RSI6</span>
      <span><i style="background:#888;opacity:0.5;"></i>成交量</span>
      <span>虚线: 30/70 超买卖线</span>
    </div>
    <div class="chart-box small"><canvas id="rsiChart"></canvas></div>
  </div>

  <div class="disclaimer">
    本报告基于多指标量化评分自动生成，评分规则：均线(30%)+MACD(30%)+RSI(20%)+成交量(20%)<br>
    数据来源：westock-data（腾讯自选股）| 仅供参考，不构成投资建议
  </div>
</div>

<script>
const dates = {dates};
const closes = {closes_json};
const ma5 = {ma5_json};
const ma10 = {ma10_json};
const ma20 = {ma20_json};
const ma60 = {ma60_json};
const bollUpper = {boll_upper};
const bollLower = {boll_lower};
const dif = {dif_json};
const dea = {dea_json};
const hist = {hist_json};
const rsi6 = {rsi_json};
const volumes = {vol_json};

Chart.defaults.font.family = "PingFang SC, Microsoft YaHei, sans-serif";
Chart.defaults.font.size = 11;

new Chart(document.getElementById("priceChart"), {{
  type: "line",
  data: {{
    labels: dates,
    datasets: [
      {{ label: "收盘价", data: closes, borderColor: "#2c2c2a", borderWidth: 1.5, pointRadius: 0, tension: 0.1, yAxisID: "y" }},
      {{ label: "BOLL上轨", data: bollUpper, borderColor: "rgba(136,136,128,0.25)", borderWidth: 1, pointRadius: 0, fill: "+1", backgroundColor: "rgba(136,136,128,0.06)", yAxisID: "y" }},
      {{ label: "BOLL下轨", data: bollLower, borderColor: "rgba(136,136,128,0.25)", borderWidth: 1, pointRadius: 0, fill: false, yAxisID: "y" }},
      {{ label: "MA5", data: ma5, borderColor: "#185FA5", borderWidth: 1.5, pointRadius: 0, tension: 0.1, yAxisID: "y" }},
      {{ label: "MA10", data: ma10, borderColor: "#BA7517", borderWidth: 1.5, pointRadius: 0, tension: 0.1, yAxisID: "y" }},
      {{ label: "MA20", data: ma20, borderColor: "#1D9E75", borderWidth: 1.5, pointRadius: 0, tension: 0.1, yAxisID: "y" }},
      {{ label: "MA60", data: ma60, borderColor: "#993556", borderWidth: 1.5, pointRadius: 0, tension: 0.1, yAxisID: "y" }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 8 }} }}, y: {{ position: "right" }} }}
  }}
}});

new Chart(document.getElementById("macdChart"), {{
  type: "bar",
  data: {{
    labels: dates,
    datasets: [
      {{ label: "MACD柱", data: hist, backgroundColor: hist.map(h => h >= 0 ? "rgba(226,75,74,0.5)" : "rgba(29,158,117,0.5)"), yAxisID: "y" }},
      {{ label: "DIF", type: "line", data: dif, borderColor: "#185FA5", borderWidth: 1.5, pointRadius: 0, tension: 0.2, yAxisID: "y" }},
      {{ label: "DEA", type: "line", data: dea, borderColor: "#BA7517", borderWidth: 1.5, pointRadius: 0, tension: 0.2, yAxisID: "y" }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 8 }} }}, y: {{ position: "right" }} }}
  }}
}});

new Chart(document.getElementById("rsiChart"), {{
  type: "bar",
  data: {{
    labels: dates,
    datasets: [
      {{ label: "成交量", data: volumes, backgroundColor: "rgba(136,136,128,0.2)", yAxisID: "y1" }},
      {{ label: "RSI6", type: "line", data: rsi6, borderColor: "#993556", borderWidth: 1.5, pointRadius: 0, tension: 0.2, yAxisID: "y2" }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 8 }} }},
      y1: {{ position: "left", title: {{ display: true, text: "成交量" }} }},
      y2: {{ position: "right", min: 0, max: 100, title: {{ display: true, text: "RSI" }}, grid: {{ drawOnChartArea: false }} }}
    }}
  }}
}});
</script>
</body>
</html>"""
    return html


# ============================================================
# 主流程
# ============================================================

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


def analyze(raw_code: str):
    code = normalize_code(raw_code)

    if code != raw_code.lower():
        log.info(f"代码识别: {raw_code} → {code}")
    log.info(f"正在获取 {code} 的行情数据...")

    # 1. 获取数据
    try:
        quote = get_quote(code)
        kline_day = get_kline(code, "day", 120)
        kline_week = get_kline(code, "week", 60)
    except RuntimeError as e:
        log.info(f"数据获取失败: {e}")
        return None, None
    name = quote.get("name", code)

    # 若今日已经生成报告,则直接返回
    today = time.strftime("%Y-%m-%d", time.localtime())
    output_filename = f"quant_{code}_{today}.html"
    html_path = os.path.join(REPORT_DIR, output_filename)
    if os.path.exists(html_path):
        log.info(f"今日已生成报告: {html_path}, 直接返回结果")
        return name, output_filename

    if not kline_day or len(kline_day) < 60:
        log.info(f"K线数据不足（仅 {len(kline_day)} 条），至少需要 60 条日线")
        return None, None

    log.info(f"已获取: {name}({code})  日线{len(kline_day)}条  周线{len(kline_week)}条")
    log.info(f"正在计算技术指标和评分...")

    # 2. 提取价格序列
    closes = [d["last"] for d in kline_day]
    highs = [d["high"] for d in kline_day]
    lows = [d["low"] for d in kline_day]
    volumes = [d["volume"] for d in kline_day]

    # 3. 计算技术指标
    ma_dict = {
        "ma5": calc_ma(closes, 5),
        "ma10": calc_ma(closes, 10),
        "ma20": calc_ma(closes, 20),
        "ma30": calc_ma(closes, 30),
        "ma60": calc_ma(closes, 60),
        "ma120": calc_ma(closes, 120),
        "ma250": calc_ma(closes, min(250, len(closes))),
    }
    macd_data = calc_macd(closes)
    rsi_data = {
        "rsi2": calc_rsi(closes, 2),
        "rsi6": calc_rsi(closes, 6),
        "rsi12": calc_rsi(closes, 12),
        "rsi24": calc_rsi(closes, 24),
    }
    boll = calc_boll(closes, 20, 2)

    all_indicators = {**ma_dict, "macd": macd_data, "rsi6": rsi_data["rsi6"], "boll": boll}

    # 4. 短/中/长线信号分析
    terms = [
        analyze_term(closes, highs, lows, volumes, boll, ma_dict, macd_data, rsi_data, "short"),
        analyze_term(closes, highs, lows, volumes, boll, ma_dict, macd_data, rsi_data, "mid"),
        analyze_term(closes, highs, lows, volumes, boll, ma_dict, macd_data, rsi_data, "long"),
    ]

    # 5. 终端输出
    print_terminal(code, name, quote, terms, kline_day)

    # 6. 生成 HTML 报告
    html = generate_html(code, name, quote, terms, kline_day, all_indicators)

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"HTML 报告已生成: {html_path}")
    return name, output_filename
