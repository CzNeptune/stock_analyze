#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
股票买卖评估工具（色盲友好版）
用法: python stock_evaluator.py <股票代码> [股票代码2 ...]
示例: python stock_evaluator.py 600941
      python stock_evaluator.py 600941 000858  (批量, 纯数字自动识别前缀)
      python stock_evaluator.py sh600941        (也可带前缀)
输出: reports/<代码>_eval_<日期>.html

色盲友好设计:
  - 买入区: 蓝色 #0072B2 + 实心填充 + ✓ 符号
  - 观察区: 橙色 #E69F00 + 斜线纹理 + ⚠ 符号
  - 回避区: 朱红 #D55E00 + 网格纹理 + ✗ 符号
  - 所有区域均有文字标签，不依赖颜色单一区分
"""

import datetime
import json
import os
import re
import subprocess
import time

from app.conf.path import JS_DIR
from app.conf.path import REPORT_DIR
from app.core.logger import log
from app.gen_html import eval_guide

# ============================================================
# 配置：westock-data CLI 路径（可通过环境变量覆盖）
# ============================================================
DEFAULT_NODE = "node"
DEFAULT_SCRIPT = JS_DIR / "westock-data.js"

NODE_BIN = os.environ.get("WESTOCK_NODE", DEFAULT_NODE)
SCRIPT_PATH = os.environ.get("WESTOCK_SCRIPT", DEFAULT_SCRIPT)

RISK_FREE_RATE = 0.025
EQUITY_RISK_PREMIUM = 0.065

# ============================================================
# 色盲友好配色 (Okabe-Ito 色盲安全调色板)
# ============================================================
# 这套配色经过科学验证，对红绿色盲、蓝黄色盲均友好
COLOR_BUY_BG = "#0072B2"  # 蓝色 — 买入区
COLOR_BUY_LIGHT = "#D6EAF8"  # 浅蓝背景
COLOR_BUY_BORDER = "#0072B2"
COLOR_OBSERVE_BG = "#E69F00"  # 橙色 — 观察区
COLOR_OBSERVE_LIGHT = "#FCF3E0"  # 浅橙背景
COLOR_OBSERVE_BORDER = "#E69F00"
COLOR_AVOID_BG = "#D55E00"  # 朱红 — 回避区
COLOR_AVOID_LIGHT = "#FDEBD0"  # 浅朱红背景
COLOR_AVOID_BORDER = "#D55E00"

# 中性色
COLOR_DARK = "#1A1A2E"
COLOR_TEXT = "#2C2C2A"
COLOR_MUTED = "#6F6F6F"
COLOR_BORDER = "#D5D5D5"
COLOR_BG = "#FAFAFA"


# ============================================================
# Markdown 表格解析器
# ============================================================
def parse_markdown_tables(text):
    if not text:
        return []
    results = []
    lines = text.strip().split("\n")
    i = 0
    current_title = ""
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("**") and line.endswith("**") and "|" not in line:
            current_title = line.strip("*").strip()
            i += 1
            continue
        if line.startswith("####"):
            current_title = line.replace("#", "").strip()
            i += 1
            continue
        if line.startswith("|") and line.count("|") >= 3:
            headers_line = line
            if i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|?$", lines[i + 1].strip()):
                headers = [h.strip() for h in headers_line.strip("|").split("|")]
                i += 2
                rows = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    vals = [v.strip() for v in lines[i].strip().strip("|").split("|")]
                    row = {}
                    for j, h in enumerate(headers):
                        row[h] = vals[j] if j < len(vals) else ""
                    rows.append(row)
                    i += 1
                results.append({"headers": headers, "rows": rows, "title": current_title})
                current_title = ""
                continue
        i += 1
    return results


def parse_table_single(text):
    tables = parse_markdown_tables(text)
    if tables and tables[0]["rows"]:
        return tables[0]["rows"][0]
    return {}


def parse_table_rows(text):
    tables = parse_markdown_tables(text)
    if tables:
        return tables[0]["rows"]
    return []


def safe_float(val, default=None):
    if val is None or val == "" or val == "-" or val == "--":
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.strip().replace(",", "").replace("%", "").replace("亿", "").replace("万", "")
        try:
            return float(val)
        except ValueError:
            return default
    return default


# ============================================================
# 数据获取层
# ============================================================
class DataFetcher:
    def __init__(self, stock_code):
        self.code = stock_code
        self.data = {}

    def fetch_all(self):
        commands = {
            "quote": ["quote", self.code],
            "finance": ["finance", self.code, "--num", "4"],
            "tech_ma": ["technical", self.code, "--group", "ma"],
            "tech_macd": ["technical", self.code, "--group", "macd"],
            "tech_boll": ["technical", self.code, "--group", "boll"],
            "tech_rsi": ["technical", self.code, "--group", "rsi"],
            "consensus": ["consensus", self.code],
            "chip": ["chip", self.code],
            "score": ["score", self.code],
            "dividend": ["dividend", "list", self.code, "--years", "5"],
        }
        for key, args in commands.items():
            log.info(f"  [fetch] {key} ...", end=" ", flush=True)
            self.data[key] = self._fetch(args)
            log.info("ok" if self.data[key] else "empty")
        return self.data

    def _fetch(self, args):
        cmd = [NODE_BIN, SCRIPT_PATH] + args
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=45, encoding="utf-8"
            )
            return result.stdout.strip() if result.stdout else ""
        except subprocess.TimeoutExpired:
            log.info("[timeout]", end="")
            return ""
        except Exception as e:
            log.info(f"[error:{e}]", end="")
            return ""


# ============================================================
# 数据处理层
# ============================================================
class DataProcessor:
    def __init__(self, raw_data, stock_code):
        self.raw = raw_data
        self.code = stock_code
        self.m = {}

    def process(self):
        self._parse_quote()
        self._parse_finance()
        self._parse_technical()
        self._parse_consensus()
        self._parse_chip()
        self._parse_score()
        self._parse_dividend()
        return self.m

    def _parse_quote(self):
        row = parse_table_single(self.raw.get("quote", ""))
        q = {}
        q["name"] = row.get("name", "未知")
        q["price"] = safe_float(row.get("price"))
        q["market_cap"] = safe_float(row.get("total_market_cap"))
        q["pe_ttm"] = safe_float(row.get("pe_ratio"))
        q["pb"] = safe_float(row.get("pb_ratio"))
        q["ps_ttm"] = safe_float(row.get("ps_ratio"))
        q["dividend_yield"] = safe_float(row.get("dividend_ratio_ttm"))
        q["turnover_rate"] = safe_float(row.get("turnover_rate"))
        q["high_52w"] = safe_float(row.get("high_52week"))
        q["low_52w"] = safe_float(row.get("low_52week"))
        q["chg_ytd"] = safe_float(row.get("chg_ytd"))
        q["chg_60d"] = safe_float(row.get("chg_60d"))
        self.m["quote"] = q

    def _parse_finance(self):
        raw = self.raw.get("finance", "")
        tables = parse_markdown_tables(raw)
        m = {"periods": []}

        lrb_rows = []
        zcfz_rows = []
        xjll_rows = []
        for t in tables:
            title = t.get("title", "").lower()
            if "lrb" in title:
                lrb_rows = t["rows"]
            elif "zcfz" in title:
                zcfz_rows = t["rows"]
            elif "xjll" in title:
                xjll_rows = t["rows"]

        all_dates = set()
        for r in lrb_rows:
            d = r.get("_date") or r.get("EndDate", "")
            if d:
                all_dates.add(d)
        for r in zcfz_rows:
            d = r.get("_date") or r.get("EndDate", "")
            if d:
                all_dates.add(d)
        for r in xjll_rows:
            d = r.get("_date") or r.get("EndDate", "")
            if d:
                all_dates.add(d)

        sorted_dates = sorted(all_dates)
        periods = []
        for d in sorted_dates:
            lrb = next((r for r in lrb_rows if r.get("_date") == d or r.get("EndDate") == d), {})
            zcfz = next((r for r in zcfz_rows if r.get("_date") == d or r.get("EndDate") == d), {})
            xjll = next((r for r in xjll_rows if r.get("_date") == d or r.get("EndDate") == d), {})

            revenue = safe_float(lrb.get("OperatingRevenue")) or safe_float(lrb.get("TotalOperatingRevenue"))
            net_profit = safe_float(lrb.get("NPParentCompanyOwners"))
            ttm_profit = safe_float(lrb.get("NPParentCompanyOwnersTTM"))
            ttm_revenue = safe_float(lrb.get("OperatingRevenueTTM")) or safe_float(lrb.get("TotalOperatingRevenueTTM"))
            gross_profit_ttm = safe_float(lrb.get("GrossProfitTTM"))

            gross_margin = None
            if ttm_revenue and gross_profit_ttm and ttm_revenue != 0:
                gross_margin = gross_profit_ttm / ttm_revenue * 100
            net_margin = None
            if revenue and net_profit and revenue != 0:
                net_margin = net_profit / revenue * 100

            total_equity = safe_float(zcfz.get("TotalShareholderEquity"))
            total_liab = safe_float(zcfz.get("TotalLiability"))
            debt_ratio = None
            if total_equity is not None and total_liab is not None:
                total_assets = total_equity + total_liab
                if total_assets and total_assets != 0:
                    debt_ratio = total_liab / total_assets * 100

            roe = None
            if ttm_profit and total_equity and total_equity != 0:
                roe = ttm_profit / total_equity * 100

            ocf = safe_float(xjll.get("NetOperateCashFlow"))
            fcf = safe_float(xjll.get("FCFF")) or safe_float(xjll.get("FCFE"))

            period = {
                "date": d,
                "revenue": revenue,
                "net_profit": net_profit,
                "ttm_revenue": ttm_revenue,
                "ttm_profit": ttm_profit,
                "eps": safe_float(lrb.get("BasicEPS")),
                "gross_margin": gross_margin,
                "net_margin": net_margin,
                "roe": roe,
                "debt_ratio": debt_ratio,
                "operating_cf": ocf,
                "fcf": fcf,
            }
            periods.append(period)

        # 计算同比
        for i in range(len(periods)):
            d = periods[i]["date"]
            try:
                dt = datetime.datetime.strptime(d[:10], "%Y-%m-%d")
                prev_year = next(
                    (p for p in periods if p["date"].startswith(str(dt.year - 1)) and p["date"][5:7] == d[5:7]),
                    None,
                )
                if prev_year:
                    if periods[i]["revenue"] and prev_year["revenue"] and prev_year["revenue"] != 0:
                        periods[i]["revenue_yoy"] = (periods[i]["revenue"] - prev_year["revenue"]) / abs(prev_year["revenue"]) * 100
                    if periods[i]["net_profit"] and prev_year["net_profit"] and prev_year["net_profit"] != 0:
                        periods[i]["profit_yoy"] = (periods[i]["net_profit"] - prev_year["net_profit"]) / abs(prev_year["net_profit"]) * 100
            except (ValueError, IndexError):
                pass

        m["periods"] = periods
        if len(periods) >= 2:
            m["latest"] = periods[-1]
            m["prev"] = periods[-2]
        elif periods:
            m["latest"] = periods[-1]
            m["prev"] = {}
        else:
            m["latest"] = {}
            m["prev"] = {}
        self.m["finance"] = m

    def _parse_technical(self):
        m = {}
        ma_row = parse_table_single(self.raw.get("tech_ma", ""))
        m["ma5"] = safe_float(ma_row.get("ma.MA_5"))
        m["ma10"] = safe_float(ma_row.get("ma.MA_10"))
        m["ma20"] = safe_float(ma_row.get("ma.MA_20"))
        m["ma30"] = safe_float(ma_row.get("ma.MA_30"))
        m["ma60"] = safe_float(ma_row.get("ma.MA_60"))
        m["ma120"] = safe_float(ma_row.get("ma.MA_120"))
        m["ma250"] = safe_float(ma_row.get("ma.MA_250"))
        macd_row = parse_table_single(self.raw.get("tech_macd", ""))
        m["macd_dif"] = safe_float(macd_row.get("macd.DIF"))
        m["macd_dea"] = safe_float(macd_row.get("macd.DEA"))
        m["macd_hist"] = safe_float(macd_row.get("macd.MACD"))
        boll_row = parse_table_single(self.raw.get("tech_boll", ""))
        m["boll_upper"] = safe_float(boll_row.get("boll.BOLL_UPPER"))
        m["boll_mid"] = safe_float(boll_row.get("boll.BOLL_MID"))
        m["boll_lower"] = safe_float(boll_row.get("boll.BOLL_LOWER"))
        rsi_row = parse_table_single(self.raw.get("tech_rsi", ""))
        m["rsi6"] = safe_float(rsi_row.get("rsi.RSI_6"))
        m["rsi12"] = safe_float(rsi_row.get("rsi.RSI_12"))
        self.m["technical"] = m

    def _parse_consensus(self):
        raw = self.raw.get("consensus", "")
        m = {}
        tp_match = re.search(r"目标价[:\s]*([\d.]+)", raw)
        m["target_price"] = safe_float(tp_match.group(1)) if tp_match else None
        rows = parse_table_rows(raw)
        m["forecasts"] = []
        for r in rows:
            m["forecasts"].append({
                "year": r.get("year", ""),
                "eps": safe_float(r.get("eps")),
                "revenue": safe_float(r.get("revenue")),
                "net_profit": safe_float(r.get("netProfit")),
                "pe": safe_float(r.get("pe")),
                "revenue_yoy": safe_float(r.get("revenueYoy")),
                "profit_yoy": safe_float(r.get("netProfitYoy")),
                "analyst_count": safe_float(r.get("institutionCnt")),
            })
        if m["forecasts"]:
            f = m["forecasts"][0]
            m["eps_forecast"] = f.get("eps")
            m["analyst_count"] = f.get("analyst_count") or 0
            m["fwd_pe"] = f.get("pe")
        else:
            m["eps_forecast"] = None
            m["analyst_count"] = 0
            m["fwd_pe"] = None
        self.m["consensus"] = m

    def _parse_chip(self):
        row = parse_table_single(self.raw.get("chip", ""))
        m = {}
        m["profit_ratio"] = safe_float(row.get("chipProfitRate"))
        m["cost_avg"] = safe_float(row.get("chipAvgCost"))
        m["concentration_90"] = safe_float(row.get("chipConcentration90"))
        m["concentration_70"] = safe_float(row.get("chipConcentration70"))
        self.m["chip"] = m

    def _parse_score(self):
        row = parse_table_single(self.raw.get("score", ""))
        m = {}

        def extract_score(val):
            if not val:
                return None
            nums = re.findall(r"([\d.]+)", val)
            return safe_float(nums[0]) if nums else None

        m["overall"] = extract_score(row.get("综合评分"))
        m["capital"] = extract_score(row.get("资金评分"))
        m["fundamental"] = extract_score(row.get("基本面评分"))
        m["risk"] = extract_score(row.get("风险评分"))
        m["technical"] = extract_score(row.get("技术评分"))
        self.m["score"] = m

    def _parse_dividend(self):
        raw = self.raw.get("dividend", "")
        rows = parse_table_rows(raw)
        m = {"history": []}
        for r in rows:
            cash = safe_float(r.get("cashDiviRMB"))
            per_share = cash / 10 if cash else None
            m["history"].append({
                "year": r.get("reportEndDate", ""),
                "amount": per_share,
                "plan": r.get("dividendPlan", ""),
            })
        m["count"] = len(m["history"])
        self.m["dividend"] = m


# ============================================================
# 五维评估引擎
# ============================================================
class StockEvaluator:
    """
    五维评估框架:
      1. 基本面 (25%) — ROE, 营收增速, 净利率, 现金流, 负债率, 现金流/净利
      2. 估值   (25%) — PE, PB, PEG, 股息率, PE历史分位, DCF溢价
      3. 技术面 (15%) — MA趋势, MACD, RSI, 布林位置, 量价
      4. 行业   (20%) — 综合评分体系中的基本面评分作为代理
      5. 资金   (15%) — 机构覆盖, 筹码集中度, 综合资金评分
    """

    def __init__(self, metrics):
        self.m = metrics
        self.results = {}

    def evaluate(self):
        self.results["fundamental"] = self._eval_fundamental()
        self.results["valuation"] = self._eval_valuation()
        self.results["technical"] = self._eval_technical()
        self.results["industry"] = self._eval_industry()
        self.results["capital"] = self._eval_capital()
        self.results["overall"] = self._calc_overall()
        self.results["verdict"] = self._verdict()
        return self.results

    # ---- 评分工具 ----
    @staticmethod
    def _grade(value, buy_threshold, observe_threshold, higher_better=True):
        """返回 ('buy'|'observe'|'avoid', 0-100分)"""
        if value is None:
            return ("observe", 50)  # 无数据默认中性
        if higher_better:
            if value >= buy_threshold:
                score = min(100, 60 + (value - buy_threshold) / buy_threshold * 40)
                return ("buy", score)
            elif value >= observe_threshold:
                score = 30 + (value - observe_threshold) / (buy_threshold - observe_threshold) * 30
                return ("observe", max(30, score))
            else:
                score = max(0, 30 * value / observe_threshold) if observe_threshold != 0 else 0
                return ("avoid", score)
        else:
            if value <= buy_threshold:
                score = min(100, 60 + (buy_threshold - value) / buy_threshold * 40)
                return ("buy", score)
            elif value <= observe_threshold:
                score = 30 + (observe_threshold - value) / (observe_threshold - buy_threshold) * 30
                return ("observe", max(30, score))
            else:
                score = max(0, 30 * buy_threshold / value) if value != 0 else 0
                return ("avoid", score)

    def _eval_fundamental(self):
        fin = self.m.get("finance", {})
        latest = fin.get("latest", {})
        items = []

        # 1. ROE
        roe = latest.get("roe")
        zone, score = self._grade(roe, 15, 8, higher_better=True)
        items.append({"name": "ROE", "value": f"{roe:.1f}%" if roe else "N/A",
                      "zone": zone, "score": score, "note": ">15%优秀, >8%及格"})

        # 2. 营收增速
        rev_yoy = latest.get("revenue_yoy")
        zone, score = self._grade(rev_yoy, 10, 0, higher_better=True)
        items.append({"name": "营收增速", "value": f"{rev_yoy:+.1f}%" if rev_yoy is not None else "N/A",
                      "zone": zone, "score": score, "note": ">10%成长性好, >0%及格"})

        # 3. 净利率
        nm = latest.get("net_margin")
        zone, score = self._grade(nm, 15, 5, higher_better=True)
        items.append({"name": "净利率", "value": f"{nm:.1f}%" if nm else "N/A",
                      "zone": zone, "score": score, "note": ">15%优秀, >5%及格"})

        # 4. 经营现金流/净利润
        ocf = latest.get("operating_cf")
        np = latest.get("net_profit")
        cf_ratio = None
        if ocf is not None and np and np != 0:
            cf_ratio = ocf / abs(np)
        zone, score = self._grade(cf_ratio, 1.0, 0.5, higher_better=True)
        items.append({"name": "现金流/净利", "value": f"{cf_ratio:.2f}" if cf_ratio else "N/A",
                      "zone": zone, "score": score, "note": ">1.0利润含金量高"})

        # 5. 资产负债率
        debt = latest.get("debt_ratio")
        zone, score = self._grade(debt, 40, 65, higher_better=False)
        items.append({"name": "负债率", "value": f"{debt:.1f}%" if debt else "N/A",
                      "zone": zone, "score": score, "note": "<40%健康, <65%及格"})

        # 6. 净利增速 vs 营收增速（经营杠杆）
        profit_yoy = latest.get("profit_yoy")
        if profit_yoy is not None and rev_yoy is not None:
            if profit_yoy > rev_yoy:
                zone, score = "buy", 80
            elif profit_yoy > 0:
                zone, score = "observe", 50
            else:
                zone, score = "avoid", 20
        else:
            zone, score = "observe", 50
        items.append({"name": "利润增速vs营收", "value": f"{profit_yoy:+.1f}% vs {rev_yoy:+.1f}%" if profit_yoy is not None and rev_yoy is not None else "N/A",
                      "zone": zone, "score": score, "note": "利润增速>营收=有杠杆"})

        avg_score = sum(i["score"] for i in items) / len(items) if items else 0
        return {"items": items, "avg_score": avg_score, "weight": 0.25}

    def _eval_valuation(self):
        q = self.m.get("quote", {})
        consensus = self.m.get("consensus", {})
        items = []

        # 1. PE (TTM)
        pe = q.get("pe_ttm")
        zone, score = self._grade(pe, 15, 30, higher_better=False)
        items.append({"name": "PE (TTM)", "value": f"{pe:.1f}" if pe else "N/A",
                      "zone": zone, "score": score, "note": "<15便宜, >30偏贵"})

        # 2. PB
        pb = q.get("pb")
        zone, score = self._grade(pb, 1.5, 3.0, higher_better=False)
        items.append({"name": "PB", "value": f"{pb:.2f}" if pb else "N/A",
                      "zone": zone, "score": score, "note": "<1.5低估, >3偏贵"})

        # 3. PEG
        pe_val = pe
        profit_yoy = self.m.get("finance", {}).get("latest", {}).get("profit_yoy")
        peg = None
        if pe_val and profit_yoy and profit_yoy > 0:
            peg = pe_val / profit_yoy
        zone, score = self._grade(peg, 1.0, 2.0, higher_better=False)
        items.append({"name": "PEG", "value": f"{peg:.2f}" if peg else "N/A",
                      "zone": zone, "score": score, "note": "<1低估, >2偏贵"})

        # 4. 股息率
        dy = q.get("dividend_yield")
        threshold = RISK_FREE_RATE * 2 * 100  # 无风险利率2倍
        zone, score = self._grade(dy, threshold, 1.0, higher_better=True)
        items.append({"name": "股息率", "value": f"{dy:.2f}%" if dy else "N/A",
                      "zone": zone, "score": score, "note": f">{threshold:.1f}%优秀, >1%及格"})

        # 5. 距52周高点距离（估值参考）
        price = q.get("price")
        high_52w = q.get("high_52w")
        if price and high_52w and high_52w > 0:
            dist_to_high = (high_52w - price) / high_52w * 100
            if dist_to_high > 30:
                zone, score = "buy", 85
            elif dist_to_high > 15:
                zone, score = "observe", 55
            else:
                zone, score = "avoid", 25
        else:
            zone, score = "observe", 50
            dist_to_high = None
        items.append({"name": "距52周高点", "value": f"-{dist_to_high:.1f}%" if dist_to_high is not None else "N/A",
                      "zone": zone, "score": score, "note": ">30%低位, <15%高位"})

        # 6. 机构目标价上行空间
        tp = consensus.get("target_price")
        if tp and price and price > 0:
            upside = (tp - price) / price * 100
            zone, score = self._grade(upside, 20, 0, higher_better=True)
        else:
            upside = None
            zone, score = "observe", 50
        items.append({"name": "机构目标价空间", "value": f"+{upside:.1f}%" if upside is not None else "N/A",
                      "zone": zone, "score": score, "note": ">20%有吸引力"})

        avg_score = sum(i["score"] for i in items) / len(items) if items else 0
        return {"items": items, "avg_score": avg_score, "weight": 0.25}

    def _eval_technical(self):
        tech = self.m.get("technical", {})
        q = self.m.get("quote", {})
        price = q.get("price")
        items = []

        # 1. MA60 趋势
        ma60 = tech.get("ma60")
        if price and ma60:
            if price > ma60 and ma60 > 0:
                zone, score = "buy", 80
            elif price > ma60 * 0.97:
                zone, score = "observe", 50
            else:
                zone, score = "avoid", 20
        else:
            zone, score = "observe", 50
        items.append({"name": "MA60趋势", "value": f"价{price:.1f} vs MA60 {ma60:.1f}" if price and ma60 else "N/A",
                      "zone": zone, "score": score, "note": "站上MA60=多头"})

        # 2. MA120 趋势
        ma120 = tech.get("ma120")
        if price and ma120:
            if price > ma120:
                zone, score = "buy", 75
            elif price > ma120 * 0.97:
                zone, score = "observe", 50
            else:
                zone, score = "avoid", 25
        else:
            zone, score = "observe", 50
        items.append({"name": "MA120趋势", "value": f"价{price:.1f} vs MA120 {ma120:.1f}" if price and ma120 else "N/A",
                      "zone": zone, "score": score, "note": "站上MA120=中期多头"})

        # 3. MACD
        dif = tech.get("macd_dif")
        dea = tech.get("macd_dea")
        if dif is not None and dea is not None:
            if dif > dea and dif > 0:
                zone, score = "buy", 85
            elif dif > dea:
                zone, score = "observe", 60
            elif dif > 0:
                zone, score = "observe", 40
            else:
                zone, score = "avoid", 20
        else:
            zone, score = "observe", 50
        items.append({"name": "MACD", "value": f"DIF {dif:.2f} / DEA {dea:.2f}" if dif is not None and dea is not None else "N/A",
                      "zone": zone, "score": score, "note": "金叉+零上=强"})

        # 4. RSI
        rsi = tech.get("rsi6")
        if rsi is not None:
            if 40 <= rsi <= 65:
                zone, score = "buy", 80
            elif 30 <= rsi <= 70:
                zone, score = "observe", 55
            elif rsi < 30:
                zone, score = "observe", 45  # 超卖可能是机会
            else:
                zone, score = "avoid", 25  # 超买
        else:
            zone, score = "observe", 50
        items.append({"name": "RSI6", "value": f"{rsi:.1f}" if rsi else "N/A",
                      "zone": zone, "score": score, "note": "40-65健康区间"})

        # 5. 布林位置
        boll_upper = tech.get("boll_upper")
        boll_mid = tech.get("boll_mid")
        boll_lower = tech.get("boll_lower")
        if price and boll_upper and boll_mid and boll_lower:
            boll_range = boll_upper - boll_lower
            if boll_range > 0:
                pos = (price - boll_lower) / boll_range * 100
                if 40 <= pos <= 70:
                    zone, score = "buy", 75
                elif 20 <= pos <= 85:
                    zone, score = "observe", 50
                else:
                    zone, score = "avoid", 30
            else:
                zone, score = "observe", 50
                pos = None
        else:
            zone, score = "observe", 50
            pos = None
        items.append({"name": "布林位置", "value": f"{pos:.0f}%" if pos is not None else "N/A",
                      "zone": zone, "score": score, "note": "中轨附近最佳"})

        avg_score = sum(i["score"] for i in items) / len(items) if items else 0
        return {"items": items, "avg_score": avg_score, "weight": 0.15}

    def _eval_industry(self):
        """
        行业与竞争维度:
        使用综合评分中的基本面评分作为代理指标,
        结合机构覆盖数量（反映市场关注度）和营收增速（反映行业景气度）
        """
        score_data = self.m.get("score", {})
        consensus = self.m.get("consensus", {})
        fin = self.m.get("finance", {}).get("latest", {})
        items = []

        # 1. 基本面综合评分（来自数据终端）
        fund_score = score_data.get("fundamental")
        if fund_score is not None:
            zone, score = self._grade(fund_score, 80, 60, higher_better=True)
        else:
            zone, score = "observe", 50
        items.append({"name": "基本面评分", "value": f"{fund_score:.1f}" if fund_score else "N/A",
                      "zone": zone, "score": score, "note": "终端综合评分>80优秀"})

        # 2. 机构覆盖数量
        analyst_count = consensus.get("analyst_count", 0)
        if analyst_count and analyst_count >= 10:
            zone, score = "buy", 80
        elif analyst_count and analyst_count >= 3:
            zone, score = "observe", 55
        elif analyst_count and analyst_count >= 1:
            zone, score = "observe", 40
        else:
            zone, score = "avoid", 20
        items.append({"name": "机构覆盖数", "value": f"{int(analyst_count)}家" if analyst_count else "0",
                      "zone": zone, "score": score, "note": ">10家说明受关注"})

        # 3. 营收增速（行业景气代理）
        rev_yoy = fin.get("revenue_yoy")
        zone, score = self._grade(rev_yoy, 10, 0, higher_better=True)
        items.append({"name": "营收增速", "value": f"{rev_yoy:+.1f}%" if rev_yoy is not None else "N/A",
                      "zone": zone, "score": score, "note": "反映行业景气度"})

        # 4. 毛利率趋势（竞争力代理）
        gm = fin.get("gross_margin")
        zone, score = self._grade(gm, 40, 20, higher_better=True)
        items.append({"name": "毛利率", "value": f"{gm:.1f}%" if gm else "N/A",
                      "zone": zone, "score": score, "note": ">40%有壁垒, >20%及格"})

        # 5. 风险评分
        risk_score = score_data.get("risk")
        if risk_score is not None:
            zone, score = self._grade(risk_score, 80, 60, higher_better=True)
        else:
            zone, score = "observe", 50
        items.append({"name": "风险评分", "value": f"{risk_score:.1f}" if risk_score else "N/A",
                      "zone": zone, "score": score, "note": "终端风险评分>80低风险"})

        avg_score = sum(i["score"] for i in items) / len(items) if items else 0
        return {"items": items, "avg_score": avg_score, "weight": 0.20}

    def _eval_capital(self):
        chip = self.m.get("chip", {})
        score_data = self.m.get("score", {})
        consensus = self.m.get("consensus", {})
        q = self.m.get("quote", {})
        items = []

        # 1. 资金评分
        cap_score = score_data.get("capital")
        if cap_score is not None:
            zone, score = self._grade(cap_score, 80, 60, higher_better=True)
        else:
            zone, score = "observe", 50
        items.append({"name": "资金评分", "value": f"{cap_score:.1f}" if cap_score else "N/A",
                      "zone": zone, "score": score, "note": "终端资金面评分"})

        # 2. 筹码获利比例
        profit_ratio = chip.get("profit_ratio")
        if profit_ratio is not None:
            if profit_ratio < 30:
                zone, score = "buy", 80  # 大面积套牢=底部
            elif profit_ratio < 60:
                zone, score = "observe", 55
            else:
                zone, score = "avoid", 30  # 获利盘多=高位
        else:
            zone, score = "observe", 50
        items.append({"name": "筹码获利比", "value": f"{profit_ratio:.1f}%" if profit_ratio is not None else "N/A",
                      "zone": zone, "score": score, "note": "<30%底部区域"})

        # 3. 筹码集中度
        conc = chip.get("concentration_90")
        if conc is not None:
            if conc < 15:
                zone, score = "buy", 80  # 高度集中
            elif conc < 30:
                zone, score = "observe", 55
            else:
                zone, score = "avoid", 30
        else:
            zone, score = "observe", 50
        items.append({"name": "筹码集中度", "value": f"{conc:.1f}%" if conc is not None else "N/A",
                      "zone": zone, "score": score, "note": "越低越集中"})

        # 4. 换手率
        turnover = q.get("turnover_rate")
        if turnover is not None:
            if 1 <= turnover <= 5:
                zone, score = "buy", 75  # 活跃适中
            elif turnover < 1:
                zone, score = "observe", 45  # 低迷
            else:
                zone, score = "avoid", 35  # 过度活跃
        else:
            zone, score = "observe", 50
        items.append({"name": "换手率", "value": f"{turnover:.2f}%" if turnover else "N/A",
                      "zone": zone, "score": score, "note": "1-5%健康活跃"})

        # 5. 机构目标价空间
        tp = consensus.get("target_price")
        price = q.get("price")
        if tp and price and price > 0:
            upside = (tp - price) / price * 100
            if upside > 20:
                zone, score = "buy", 85
            elif upside > 0:
                zone, score = "observe", 55
            else:
                zone, score = "avoid", 25
        else:
            zone, score = "observe", 50
        items.append({"name": "机构上行空间", "value": f"+{upside:.1f}%" if tp and price else "N/A",
                      "zone": zone, "score": score, "note": "聪明钱的目标价"})

        avg_score = sum(i["score"] for i in items) / len(items) if items else 0
        return {"items": items, "avg_score": avg_score, "weight": 0.15}

    def _calc_overall(self):
        total = 0
        for key in ["fundamental", "valuation", "technical", "industry", "capital"]:
            d = self.results.get(key, {})
            total += d.get("avg_score", 0) * d.get("weight", 0)
        return round(total, 1)

    def _verdict(self):
        score = self.results["overall"]
        if score >= 70:
            return "BUY", "适合买入/加仓"
        elif score >= 50:
            return "HOLD", "观察名单/持有"
        else:
            return "AVOID", "回避/减仓"


# ============================================================
# HTML 报告生成器（色盲友好）
# ============================================================
class ReportGenerator:
    # 区域 -> (符号, 中文, 背景色, 浅背景, 边框色, CSS pattern)
    ZONE_MAP = {
        "buy": ("\u2713", "买入", COLOR_BUY_BG, COLOR_BUY_LIGHT, COLOR_BUY_BORDER, "buy-pattern"),
        "observe": ("\u26A0", "观察", COLOR_OBSERVE_BG, COLOR_OBSERVE_LIGHT, COLOR_OBSERVE_BORDER, "observe-pattern"),
        "avoid": ("\u2717", "回避", COLOR_AVOID_BG, COLOR_AVOID_LIGHT, COLOR_AVOID_BORDER, "avoid-pattern"),
    }

    def __init__(self, metrics, eval_results, stock_code):
        self.m = metrics
        self.eval = eval_results
        self.code = stock_code
        self.today = datetime.date.today().strftime("%Y-%m-%d")

    def generate(self):
        q = self.m.get("quote", {})
        name = q.get("name", self.code)
        price = q.get("price", 0)
        verdict = self.eval["verdict"]
        overall = self.eval["overall"]

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}({self.code}) 买卖评估报告</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}

/* === 色盲友好纹理图案 === */
.buy-pattern {{
    background-color: {COLOR_BUY_LIGHT};
    background-image: none;
}}
.observe-pattern {{
    background-color: {COLOR_OBSERVE_LIGHT};
    background-image: repeating-linear-gradient(
        45deg,
        transparent,
        transparent 8px,
        rgba(230, 159, 0, 0.15) 8px,
        rgba(230, 159, 0, 0.15) 12px
    );
}}
.avoid-pattern {{
    background-color: {COLOR_AVOID_LIGHT};
    background-image: repeating-linear-gradient(
        45deg,
        transparent,
        transparent 6px,
        rgba(213, 94, 0, 0.12) 6px,
        rgba(213, 94, 0, 0.12) 10px
    ),
    repeating-linear-gradient(
        -45deg,
        transparent,
        transparent 6px,
        rgba(213, 94, 0, 0.12) 6px,
        rgba(213, 94, 0, 0.12) 10px
    );
}}

body {{
    font-family: "PingFang SC", "Microsoft YaHei", -apple-system, sans-serif;
    background: {COLOR_BG};
    color: {COLOR_TEXT};
    line-height: 1.6;
    font-size: 14px;
}}

.container {{ max-width: 920px; margin: 0 auto; padding: 24px 20px; }}

/* Header */
.report-header {{
    background: {COLOR_DARK};
    color: white;
    border-radius: 12px;
    padding: 24px 28px;
    margin-bottom: 20px;
}}
.report-header .ticker {{
    display: inline-block;
    background: rgba(255,255,255,0.15);
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-family: monospace;
    margin-bottom: 8px;
}}
.report-header h1 {{ font-size: 22px; font-weight: 500; margin-bottom: 4px; }}
.report-header .subtitle {{ font-size: 13px; opacity: 0.8; }}
.report-header .metrics-row {{
    display: flex;
    gap: 24px;
    margin-top: 16px;
    flex-wrap: wrap;
}}
.report-header .metric {{ font-size: 13px; }}
.report-header .metric .label {{ opacity: 0.6; margin-right: 4px; }}
.report-header .metric .value {{ font-weight: 500; }}

/* Verdict Box */
.verdict-box {{
    border-radius: 12px;
    padding: 28px;
    margin-bottom: 20px;
    text-align: center;
    border: 2px solid;
}}
.verdict-box.buy {{
    border-color: {COLOR_BUY_BG};
    background: {COLOR_BUY_LIGHT};
}}
.verdict-box.hold {{
    border-color: {COLOR_OBSERVE_BG};
    background: {COLOR_OBSERVE_LIGHT};
    background-image: repeating-linear-gradient(45deg, transparent, transparent 10px, rgba(230,159,0,0.08) 10px, rgba(230,159,0,0.08) 16px);
}}
.verdict-box.avoid {{
    border-color: {COLOR_AVOID_BG};
    background: {COLOR_AVOID_LIGHT};
    background-image: repeating-linear-gradient(45deg, transparent, transparent 8px, rgba(213,94,0,0.08) 8px, rgba(213,94,0,0.08) 14px), repeating-linear-gradient(-45deg, transparent, transparent 8px, rgba(213,94,0,0.08) 8px, rgba(213,94,0,0.08) 14px);
}}
.verdict-box .vlabel {{ font-size: 13px; color: {COLOR_MUTED}; margin-bottom: 4px; }}
.verdict-box .vscore {{ font-size: 48px; font-weight: 500; line-height: 1.2; }}
.verdict-box .vtext {{ font-size: 18px; font-weight: 500; margin-top: 8px; }}

/* Section */
.section {{
    background: white;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 16px;
    border: 1px solid {COLOR_BORDER};
}}
.section-title {{
    font-size: 15px;
    font-weight: 500;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.section-title .weight {{
    font-size: 11px;
    color: {COLOR_MUTED};
    font-weight: 400;
    background: {COLOR_BG};
    padding: 2px 8px;
    border-radius: 4px;
}}
.section-title .dim-score {{
    margin-left: auto;
    font-size: 20px;
    font-weight: 500;
}}

/* Indicator table */
.ind-table {{ width: 100%; border-collapse: collapse; }}
.ind-table th {{
    text-align: left;
    font-size: 11px;
    color: {COLOR_MUTED};
    font-weight: 400;
    padding: 6px 10px;
    border-bottom: 1px solid {COLOR_BORDER};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.ind-table td {{
    padding: 10px;
    font-size: 13px;
    border-bottom: 1px solid {COLOR_BG};
}}
.ind-table td.ind-name {{ font-weight: 500; width: 130px; }}
.ind-table td.ind-value {{ font-family: "SF Mono", Consolas, monospace; font-size: 12.5px; width: 160px; }}
.ind-table td.ind-note {{ color: {COLOR_MUTED}; font-size: 12px; }}

/* Zone badge */
.zone-badge {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 500;
    min-width: 60px;
    justify-content: center;
}}
.zone-badge.buy {{
    background: {COLOR_BUY_BG};
    color: white;
}}
.zone-badge.observe {{
    background: {COLOR_OBSERVE_BG};
    color: white;
}}
.zone-badge.avoid {{
    background: {COLOR_AVOID_BG};
    color: white;
}}
.zone-badge .symbol {{ font-size: 14px; }}

/* Score bar */
.score-bar {{
    height: 6px;
    border-radius: 3px;
    background: {COLOR_BG};
    overflow: hidden;
    position: relative;
    width: 60px;
}}
.score-bar-fill {{
    height: 100%;
    border-radius: 3px;
}}
.score-bar-fill.buy {{ background: {COLOR_BUY_BG}; }}
.score-bar-fill.observe {{ background: {COLOR_OBSERVE_BG}; }}
.score-bar-fill.avoid {{ background: {COLOR_AVOID_BG}; }}

/* Dimension card */
.dim-card {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    border-radius: 8px;
    margin-bottom: 8px;
}}
.dim-card.buy {{ background: {COLOR_BUY_LIGHT}; border-left: 4px solid {COLOR_BUY_BG}; }}
.dim-card.observe {{ background: {COLOR_OBSERVE_LIGHT}; border-left: 4px solid {COLOR_OBSERVE_BG}; }}
.dim-card.avoid {{ background: {COLOR_AVOID_LIGHT}; border-left: 4px solid {COLOR_AVOID_BG}; }}

/* Radar placeholder */
.radar-container {{
    text-align: center;
    padding: 20px 0;
}}
.radar-container svg {{ max-width: 400px; }}

/* Legend */
.legend {{
    display: flex;
    gap: 16px;
    justify-content: center;
    margin-bottom: 16px;
    flex-wrap: wrap;
}}
.legend-item {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: {COLOR_MUTED};
}}
.legend-swatch {{
    width: 24px;
    height: 16px;
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-size: 10px;
}}

/* Footer */
.footer {{
    text-align: center;
    padding: 20px;
    font-size: 12px;
    color: {COLOR_MUTED};
    line-height: 1.8;
}}
.footer hr {{ border: none; border-top: 1px solid {COLOR_BORDER}; margin-bottom: 16px; }}
</style>
</head>
<body>
<div class="container">

<!-- Report Header -->
<div class="report-header">
    <span class="ticker">{self.code}</span>
    <h1>{name}</h1>
    <div class="subtitle">买卖评估报告 | 五维评分 | {self.today}</div>
    <div class="metrics-row">
        <div class="metric"><span class="label">股价</span><span class="value">{price:.2f}</span></div>
        <div class="metric"><span class="label">PE</span><span class="value">{q.get('pe_ttm', 0):.1f}</span></div>
        <div class="metric"><span class="label">PB</span><span class="value">{q.get('pb', 0):.2f}</span></div>
        <div class="metric"><span class="label">股息率</span><span class="value">{q.get('dividend_yield', 0):.2f}%</span></div>
        <div class="metric"><span class="label">52周高</span><span class="value">{q.get('high_52w', 0):.2f}</span></div>
        <div class="metric"><span class="label">52周低</span><span class="value">{q.get('low_52w', 0):.2f}</span></div>
    </div>
</div>

<!-- Legend -->
<div class="legend">
    <div class="legend-item">
        <div class="legend-swatch" style="background:{COLOR_BUY_BG}">&#10003;</div>
        买入区 — 指标优秀
    </div>
    <div class="legend-item">
        <div class="legend-swatch" style="background:{COLOR_OBSERVE_BG}">&#9888;</div>
        观察区 — 中性/持有
    </div>
    <div class="legend-item">
        <div class="legend-swatch" style="background:{COLOR_AVOID_BG}">&#10007;</div>
        回避区 — 风险偏高
    </div>
</div>

<!-- Verdict -->
{self._verdict_html()}

<!-- Radar Chart -->
{self._radar_html()}

<!-- 5 Dimensions -->
{self._dimensions_html()}

<!-- PM Seven Questions -->
{self._pm_questions_html()}

<!-- 判断标准说明书 -->
{eval_guide.generate()}

<!-- Footer -->
<div class="footer">
    <hr>
    <p>数据来源：腾讯自选股行情接口 | 评分模型基于五维评估框架</p>
    <p style="margin-top: 8px; font-weight: 500;">本报告仅供参考，不构成个人投资建议</p>
</div>

</div>
</body>
</html>"""
        return html

    def _zone_class(self, zone):
        return {"buy": "buy", "observe": "observe", "avoid": "avoid"}.get(zone, "observe")

    def _verdict_html(self):
        verdict_code, verdict_text = self.eval["verdict"]
        overall = self.eval["overall"]
        vclass = {"BUY": "buy", "HOLD": "hold", "AVOID": "avoid"}.get(verdict_code, "hold")
        symbol = {"BUY": "\u2713", "HOLD": "\u26A0", "AVOID": "\u2717"}.get(verdict_code, "\u26A0")
        return f"""
<div class="verdict-box {vclass}">
    <div class="vlabel">综合评估结论</div>
    <div class="vscore">{overall}</div>
    <div class="vtext">{symbol} {verdict_text}</div>
</div>"""

    def _radar_html(self):
        dims = [
            ("基本面", self.eval["fundamental"]["avg_score"], 0.25),
            ("估值", self.eval["valuation"]["avg_score"], 0.25),
            ("技术面", self.eval["technical"]["avg_score"], 0.15),
            ("行业", self.eval["industry"]["avg_score"], 0.20),
            ("资金", self.eval["capital"]["avg_score"], 0.15),
        ]
        # Radar chart as SVG pentagon
        import math
        cx, cy, r = 200, 180, 120
        n = len(dims)
        angles = [-math.pi / 2 + i * 2 * math.pi / n for i in range(n)]

        # Grid rings
        grid_svg = ""
        for pct in [0.25, 0.5, 0.75, 1.0]:
            pts = []
            for a in angles:
                px = cx + r * pct * math.cos(a)
                py = cy + r * pct * math.sin(a)
                pts.append(f"{px:.1f},{py:.1f}")
            grid_svg += f'<polygon points="{" ".join(pts)}" fill="none" stroke="{COLOR_BORDER}" stroke-width="0.5"/>'

        # Axis lines
        axis_svg = ""
        for i, a in enumerate(angles):
            px = cx + r * math.cos(a)
            py = cy + r * math.sin(a)
            axis_svg += f'<line x1="{cx}" y1="{cy}" x2="{px:.1f}" y2="{py:.1f}" stroke="{COLOR_BORDER}" stroke-width="0.5"/>'

        # Data polygon
        data_pts = []
        for i, (name, score, _) in enumerate(dims):
            a = angles[i]
            pct = score / 100
            px = cx + r * pct * math.cos(a)
            py = cy + r * pct * math.sin(a)
            data_pts.append(f"{px:.1f},{py:.1f}")

        # Data points
        point_svg = ""
        for i, (name, score, _) in enumerate(dims):
            a = angles[i]
            pct = score / 100
            px = cx + r * pct * math.cos(a)
            py = cy + r * pct * math.sin(a)
            color = self._score_color(score)
            point_svg += f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{color}"/>'

        # Labels
        label_svg = ""
        for i, (name, score, _) in enumerate(dims):
            a = angles[i]
            lx = cx + (r + 30) * math.cos(a)
            ly = cy + (r + 30) * math.sin(a)
            label_svg += f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" dominant-baseline="central" style="font-size:12px;fill:{COLOR_TEXT};font-weight:500">{name}</text>'
            label_svg += f'<text x="{lx:.1f}" y="{ly + 16:.1f}" text-anchor="middle" dominant-baseline="central" style="font-size:14px;fill:{self._score_color(score)};font-weight:500">{score:.0f}</text>'

        return f"""
<div class="section">
    <div class="section-title">五维雷达图</div>
    <div class="radar-container">
        <svg viewBox="0 0 400 360" style="max-width:400px">
            {grid_svg}
            {axis_svg}
            <polygon points="{" ".join(data_pts)}" fill="{COLOR_BUY_BG}" fill-opacity="0.15" stroke="{COLOR_BUY_BG}" stroke-width="1.5"/>
            {point_svg}
            {label_svg}
        </svg>
    </div>
</div>"""

    def _score_color(self, score):
        if score >= 70:
            return COLOR_BUY_BG
        elif score >= 40:
            return COLOR_OBSERVE_BG
        else:
            return COLOR_AVOID_BG

    def _dimensions_html(self):
        sections = []
        dim_configs = [
            ("fundamental", "基本面", "盈利能力与质量", 0.25),
            ("valuation", "估值", "便不便宜", 0.25),
            ("technical", "技术面", "买卖时机", 0.15),
            ("industry", "行业与竞争", "赛道与壁垒", 0.20),
            ("capital", "资金与催化", "聪明钱与拐点", 0.15),
        ]
        for key, title, subtitle, weight in dim_configs:
            data = self.eval[key]
            avg = data["avg_score"]
            avg_zone = "buy" if avg >= 70 else ("observe" if avg >= 40 else "avoid")
            zone_class = self._zone_class(avg_zone)
            symbol, zone_text = self.ZONE_MAP[avg_zone][0], self.ZONE_MAP[avg_zone][1]

            rows_html = ""
            for item in data["items"]:
                izone = item["zone"]
                iclass = self._zone_class(izone)
                isymbol, izone_text = self.ZONE_MAP[izone][0], self.ZONE_MAP[izone][1]
                iscore = item["score"]
                rows_html += f"""
                <tr>
                    <td class="ind-name">{item['name']}</td>
                    <td class="ind-value">{item['value']}</td>
                    <td>
                        <span class="zone-badge {iclass}">
                            <span class="symbol">{isymbol}</span> {izone_text}
                        </span>
                    </td>
                    <td>
                        <div style="display:flex;align-items:center;gap:8px">
                            <div class="score-bar"><div class="score-bar-fill {iclass}" style="width:{iscore:.0f}%"></div></div>
                            <span style="font-size:12px;color:{COLOR_MUTED};min-width:28px">{iscore:.0f}</span>
                        </div>
                    </td>
                    <td class="ind-note">{item['note']}</td>
                </tr>"""

            weight_pct = int(weight * 100)
            sections.append(f"""
<div class="section {zone_class}" style="border-left: 4px solid {self.ZONE_MAP[avg_zone][3]};">
    <div class="section-title">
        {title} — {subtitle}
        <span class="weight">权重 {weight_pct}%</span>
        <span class="dim-score" style="color:{self._score_color(avg)}">{avg:.0f}</span>
    </div>
    <table class="ind-table">
        <thead>
            <tr><th>指标</th><th>当前值</th><th>评级</th><th>得分</th><th>参考标准</th></tr>
        </thead>
        <tbody>{rows_html}
        </tbody>
    </table>
</div>""")
        return "\n".join(sections)

    def _pm_questions_html(self):
        q = self.m.get("quote", {})
        fin = self.m.get("finance", {}).get("latest", {})
        consensus = self.m.get("consensus", {})
        price = q.get("price") or 0
        tp = consensus.get("target_price")
        upside = f"{(tp - price) / price * 100:+.1f}%" if tp and price else "N/A"

        overall = self.eval["overall"]
        verdict_code, verdict_text = self.eval["verdict"]

        answers = [
            ("什么被错误定价了？", self._mispricing_answer()),
            ("当前价格反映了什么？",
             f"PE {q.get('pe_ttm') or 0:.1f} / PB {q.get('pb') or 0:.2f} / 股息率 {q.get('dividend_yield') or 0:.2f}%，市场预期{'乐观' if (q.get('pe_ttm') or 0) > 25 else '中性' if (q.get('pe_ttm') or 0) > 15 else '悲观'}"),
            ("什么能证明论点？", f"营收增速 {fin.get('revenue_yoy') or 0:+.1f}%、ROE {fin.get('roe') or 0:.1f}%、机构目标价上行 {upside}"),
            ("什么能推翻论点？", "营收增速转负、ROE跌破8%、负债率超过65%、技术面跌破MA120"),
            ("为什么是现在？",
             f"综合评分 {overall:.1f}，{'五维共振多数达标' if overall >= 70 else '部分维度达标，需更多确认' if overall >= 50 else '多维度不达标，时机不佳'}"),
            ("什么会改变仓位？", "评分升至80+加仓，降至50以下减仓，基本面恶化清仓"),
            ("还缺少什么证据？", "行业竞争格局深度分析、管理层质量评估、同业对比尚未覆盖"),
        ]

        rows = ""
        for i, (q_text, a_text) in enumerate(answers, 1):
            rows += f"""
            <tr>
                <td style="width:40px;color:{COLOR_MUTED};font-size:12px">{i}</td>
                <td style="width:200px;font-weight:500;font-size:13px">{q_text}</td>
                <td style="font-size:13px;color:{COLOR_TEXT}">{a_text}</td>
            </tr>"""

        return f"""
<div class="section">
    <div class="section-title">PM 七问 — 买入前最终检查</div>
    <table class="ind-table">
        <tbody>{rows}
        </tbody>
    </table>
</div>"""

    def _mispricing_answer(self):
        val = self.eval["valuation"]["avg_score"]
        fund = self.eval["fundamental"]["avg_score"]
        if fund >= 70 and val >= 60:
            return "基本面优秀但估值未充分反映，市场可能低估了盈利可持续性"
        elif val >= 70 and fund >= 50:
            return "估值处于低位，市场对基本面过于悲观"
        elif fund >= 70 and val < 40:
            return "好公司但太贵，当前价格已充分反映基本面"
        else:
            return "未发现明显错误定价，建议继续观察或放弃"


# ============================================================
# 主程序
# ============================================================
def normalize_stock_code(code):
    """自动识别股票代码前缀，支持纯数字输入。
    规则: 6开头->sh, 0/3开头->sz, 8/4开头->bj, 已有前缀则保留。
    """
    code = code.strip().lower()
    for prefix in ("sh", "sz", "bj", "hk", "us"):
        if code.startswith(prefix):
            return code
    if code.startswith("6"):
        return "sh" + code
    elif code.startswith(("0", "3")):
        return "sz" + code
    elif code.startswith(("8", "4")):
        return "bj" + code
    return "us" + code


# ============================================================
# 数据获取
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


def analyze(raw_code):
    code = normalize_stock_code(raw_code)
    if code != raw_code.lower():
        log.info(f"代码识别: {raw_code} → {code}")
    log.info(f"正在获取 {code} 的行情数据...")

    # 1. 获取数据
    try:
        quote = get_quote(code)
    except RuntimeError as e:
        log.info(f"数据获取失败: {e}")
        return None, None
    name = quote.get("name", code)

    # 若今日已经生成报告,则直接返回
    today = time.strftime("%Y-%m-%d", time.localtime())
    filename = f"eval_{code}_{today}.html"
    filepath = os.path.join(REPORT_DIR, filename)
    if os.path.exists(filepath):
        log.info(f"今日已生成报告: {filepath}, 直接返回结果")
        return name, filename

    # 1. 获取数据
    log.info("\n[1/4] 获取数据...")
    fetcher = DataFetcher(code)
    raw_data = fetcher.fetch_all()

    # 2. 解析数据
    log.info("\n[2/4] 解析数据...")
    processor = DataProcessor(raw_data, code)
    metrics = processor.process()

    name = metrics.get("quote", {}).get("name", code)
    price = metrics.get("quote", {}).get("price", 0)
    log.info(f"  股票: {name} ({code})")
    log.info(f"  股价: {price:.2f}")

    # 3. 五维评估
    log.info("\n[3/4] 五维评估...")
    evaluator = StockEvaluator(metrics)
    results = evaluator.evaluate()

    for dim_name, dim_key in [("基本面", "fundamental"), ("估值", "valuation"),
                              ("技术面", "technical"), ("行业", "industry"), ("资金", "capital")]:
        avg = results[dim_key]["avg_score"]
        zone = "买入" if avg >= 70 else ("观察" if avg >= 40 else "回避")
        symbol = "\u2713" if avg >= 70 else ("\u26A0" if avg >= 40 else "\u2717")
        log.info(f"  {dim_name}: {avg:.1f} [{symbol} {zone}]")

    overall = results["overall"]
    verdict_code, verdict_text = results["verdict"]
    log.info(f"\n  综合评分: {overall}")
    log.info(f"  结论: {verdict_code} - {verdict_text}")

    # 4. 生成报告
    log.info("\n[4/4] 生成报告...")
    gen = ReportGenerator(metrics, results, code)
    html = gen.generate()

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"  报告已保存: {filepath}")

    return name, filename
