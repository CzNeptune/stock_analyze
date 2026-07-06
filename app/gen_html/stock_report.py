#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
股票研报自动化生成工具
用法: python stock_report.py <股票代码> [股票代码2 ...]
示例: python stock_report.py 600941
      python stock_report.py 600941 000858  (批量, 纯数字自动识别前缀)
      python stock_report.py sh600941        (也可带前缀)
输出: reports/<代码>_<日期>.html
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
from app.gen_html.utils import normalize_code

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
# Markdown 表格解析器
# ============================================================
def parse_markdown_tables(text):
    """
    从文本中解析所有 Markdown 表格。
    返回: [{headers: [...], rows: [{col: val, ...}, ...], title: str}, ...]
    """
    if not text:
        return []
    results = []
    lines = text.strip().split("\n")
    i = 0
    current_title = ""
    while i < len(lines):
        line = lines[i].strip()
        # 检测标题行 (**xxx** 或 #### xxx)
        if line.startswith("**") and line.endswith("**") and "|" not in line:
            current_title = line.strip("*").strip()
            i += 1
            continue
        if line.startswith("####"):
            current_title = line.replace("#", "").strip()
            i += 1
            continue
        # 检测表格起始 (| 开头且包含多个 |)
        if line.startswith("|") and line.count("|") >= 3:
            headers_line = line
            # 检查下一行是否是分隔符
            if i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|?$", lines[i + 1].strip()):
                headers = [h.strip() for h in headers_line.strip("|").split("|")]
                i += 2  # 跳过分隔符行
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
    """解析单个表格，返回第一行数据（适用于 quote/technical 等单行表格）"""
    tables = parse_markdown_tables(text)
    if tables and tables[0]["rows"]:
        return tables[0]["rows"][0]
    return {}


def parse_table_rows(text):
    """解析表格，返回所有行"""
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


def extract_number(text, pattern=None):
    """从文本中提取数字"""
    if not text:
        return None
    if pattern:
        m = re.search(pattern, text)
        if m:
            return safe_float(m.group(1))
    nums = re.findall(r"[-\d.]+", str(text))
    if nums:
        return safe_float(nums[0])
    return None


# ============================================================
# 数据获取层
# ============================================================
class DataFetcher:
    """通过 westock-data CLI 获取全量股票数据"""

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
            "kline_day": ["kline", self.code, "--period", "day", "--limit", "120"],
            "kline_week": ["kline", self.code, "--period", "week", "--limit", "60"],
        }
        for key, args in commands.items():
            log.info(f"  [fetch] {key} ...", end=" ", flush=True)
            self.data[key] = self._fetch(args)
            ok = bool(self.data[key])
            log.info("ok" if ok else "empty")
        return self.data

    def _fetch(self, args):
        cmd = [NODE_BIN, SCRIPT_PATH] + args
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=45, encoding="utf-8"
            )
            output = result.stdout.strip() if result.stdout else ""
            if output:
                return output
            return ""
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
    """解析原始 Markdown 输出，提取关键指标"""

    def __init__(self, raw_data, stock_code):
        self.raw = raw_data
        self.code = stock_code
        self.metrics = {}

    def process(self):
        self._parse_quote()
        self._parse_finance()
        self._parse_technical()
        self._parse_consensus()
        self._parse_chip()
        self._parse_score()
        self._parse_dividend()
        self._parse_kline()
        return self.metrics

    # ---------- 解析各模块 ----------
    def _parse_quote(self):
        row = parse_table_single(self.raw.get("quote", ""))
        m = {}
        m["name"] = row.get("name", "未知")
        m["price"] = safe_float(row.get("price"))
        m["change_pct"] = safe_float(row.get("change_percent"))
        m["market_cap"] = safe_float(row.get("total_market_cap"))
        m["pe_ttm"] = safe_float(row.get("pe_ratio"))
        m["pe_fwd"] = safe_float(row.get("pe_fwd"))
        m["pb"] = safe_float(row.get("pb_ratio"))
        m["ps_ttm"] = safe_float(row.get("ps_ratio")) or safe_float(row.get("ps"))
        m["total_share"] = safe_float(row.get("total_shares"))
        m["float_share"] = safe_float(row.get("float_shares"))
        m["turnover_rate"] = safe_float(row.get("turnover_rate"))
        m["volume"] = safe_float(row.get("volume"))
        m["amount"] = safe_float(row.get("amount"))
        m["high"] = safe_float(row.get("high"))
        m["low"] = safe_float(row.get("low"))
        m["open"] = safe_float(row.get("open"))
        m["pre_close"] = safe_float(row.get("prev_close"))
        m["dividend_yield"] = safe_float(row.get("dividend_ratio_ttm"))
        m["high_52w"] = safe_float(row.get("high_52week"))
        m["low_52w"] = safe_float(row.get("low_52week"))
        m["chg_5d"] = safe_float(row.get("chg_5d"))
        m["chg_20d"] = safe_float(row.get("chg_20d"))
        m["chg_60d"] = safe_float(row.get("chg_60d"))
        m["chg_ytd"] = safe_float(row.get("chg_ytd"))
        self.metrics["quote"] = m

    def _parse_finance(self):
        raw = self.raw.get("finance", "")
        tables = parse_markdown_tables(raw)
        m = {"periods": []}

        # 找到利润表、资产负债表、现金流量表
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

        # 按日期合并三表数据
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
            operating_cost = safe_float(lrb.get("OperatingCost")) or safe_float(lrb.get("TotalOperatingCost"))
            gross_profit_ttm = safe_float(lrb.get("GrossProfitTTM"))

            gross_margin = None
            if ttm_revenue and gross_profit_ttm and ttm_revenue != 0:
                gross_margin = gross_profit_ttm / ttm_revenue * 100
            net_margin = None
            if revenue and net_profit and revenue != 0:
                net_margin = net_profit / revenue * 100

            total_equity = safe_float(zcfz.get("TotalShareholderEquity"))
            total_liab = safe_float(zcfz.get("TotalLiability"))
            total_assets = (total_equity or 0) + (total_liab or 0) if total_equity and total_liab else None

            roe = None
            if ttm_profit and total_equity and total_equity != 0:
                roe = ttm_profit / total_equity * 100

            ocf = safe_float(xjll.get("NetOperateCashFlow"))
            icf = safe_float(xjll.get("NetInvestCashFlow"))
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
                "total_assets": total_assets,
                "total_liab": total_liab,
                "total_equity": total_equity,
                "operating_cf": ocf,
                "investing_cf": icf,
                "fcf": fcf,
            }
            periods.append(period)

        # 计算同比
        for i in range(len(periods)):
            d = periods[i]["date"]
            # 找去年同期 (YYYY-MM -> 同月去年)
            try:
                dt = datetime.datetime.strptime(d[:10], "%Y-%m-%d")
                prev_year_d = d.replace(str(dt.year), str(dt.year - 1), 1)
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
            latest = periods[-1]
            prev = periods[-2]
            if latest["revenue"] and prev["revenue"] and prev["revenue"] != 0:
                m["revenue_qoq"] = (latest["revenue"] - prev["revenue"]) / abs(prev["revenue"]) * 100
            if latest["net_profit"] and prev["net_profit"] and prev["net_profit"] != 0:
                m["profit_qoq"] = (latest["net_profit"] - prev["net_profit"]) / abs(prev["net_profit"]) * 100
        elif periods:
            m["latest"] = periods[-1]
            m["prev"] = {}
        else:
            m["latest"] = {}
            m["prev"] = {}
        self.metrics["finance"] = m

    def _parse_technical(self):
        m = {}
        # MA
        ma_row = parse_table_single(self.raw.get("tech_ma", ""))
        m["ma5"] = safe_float(ma_row.get("ma.MA_5"))
        m["ma10"] = safe_float(ma_row.get("ma.MA_10"))
        m["ma20"] = safe_float(ma_row.get("ma.MA_20"))
        m["ma30"] = safe_float(ma_row.get("ma.MA_30"))
        m["ma60"] = safe_float(ma_row.get("ma.MA_60"))
        m["ma120"] = safe_float(ma_row.get("ma.MA_120"))
        m["ma250"] = safe_float(ma_row.get("ma.MA_250"))
        # MACD
        macd_row = parse_table_single(self.raw.get("tech_macd", ""))
        m["macd_dif"] = safe_float(macd_row.get("macd.DIF"))
        m["macd_dea"] = safe_float(macd_row.get("macd.DEA"))
        m["macd_hist"] = safe_float(macd_row.get("macd.MACD"))
        # BOLL
        boll_row = parse_table_single(self.raw.get("tech_boll", ""))
        m["boll_upper"] = safe_float(boll_row.get("boll.BOLL_UPPER"))
        m["boll_mid"] = safe_float(boll_row.get("boll.BOLL_MID"))
        m["boll_lower"] = safe_float(boll_row.get("boll.BOLL_LOWER"))
        # RSI
        rsi_row = parse_table_single(self.raw.get("tech_rsi", ""))
        m["rsi6"] = safe_float(rsi_row.get("rsi.RSI_6"))
        m["rsi12"] = safe_float(rsi_row.get("rsi.RSI_12"))
        m["rsi24"] = safe_float(rsi_row.get("rsi.RSI_24"))
        self.metrics["technical"] = m

    def _parse_consensus(self):
        raw = self.raw.get("consensus", "")
        m = {}
        # 提取目标价
        tp_match = re.search(r"目标价[:\s]*([\d.]+)", raw)
        m["target_price"] = safe_float(tp_match.group(1)) if tp_match else None
        # 解析预测表
        rows = parse_table_rows(raw)
        m["forecasts"] = []
        for r in rows:
            m["forecasts"].append({
                "year": r.get("year", ""),
                "eps": safe_float(r.get("eps")),
                "revenue": safe_float(r.get("revenue")),
                "net_profit": safe_float(r.get("netProfit")),
                "pe": safe_float(r.get("pe")),
                "pb": safe_float(r.get("pb")),
                "ps": safe_float(r.get("ps")),
                "revenue_yoy": safe_float(r.get("revenueYoy")),
                "profit_yoy": safe_float(r.get("netProfitYoy")),
                "analyst_count": safe_float(r.get("institutionCnt")),
            })
        if m["forecasts"]:
            f = m["forecasts"][0]
            m["eps_forecast"] = f.get("eps")
            m["revenue_forecast"] = f.get("revenue")
            m["profit_forecast"] = f.get("net_profit")
            m["analyst_count"] = f.get("analyst_count") or 0
        else:
            m["eps_forecast"] = None
            m["revenue_forecast"] = None
            m["profit_forecast"] = None
            m["analyst_count"] = 0
        self.metrics["consensus"] = m

    def _parse_chip(self):
        row = parse_table_single(self.raw.get("chip", ""))
        m = {}
        m["profit_ratio"] = safe_float(row.get("chipProfitRate"))
        m["cost_avg"] = safe_float(row.get("chipAvgCost"))
        m["concentration_90"] = safe_float(row.get("chipConcentration90"))
        m["concentration_70"] = safe_float(row.get("chipConcentration70"))
        self.metrics["chip"] = m

    def _parse_score(self):
        row = parse_table_single(self.raw.get("score", ""))
        m = {}

        # 评分格式: "77.46 (周↑+3.92 ...)" -> 提取第一个数字
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
        self.metrics["score"] = m

    def _parse_dividend(self):
        raw = self.raw.get("dividend", "")
        rows = parse_table_rows(raw)
        m = {"history": []}
        for r in rows:
            cash = safe_float(r.get("cashDiviRMB"))
            # cashDiviRMB 是 "每10股派X元"，需要转换
            if cash:
                per_share = cash / 10
            else:
                per_share = None
            m["history"].append({
                "year": r.get("reportEndDate", ""),
                "amount": per_share,
                "total_cash": safe_float(r.get("totalCashDiviComRMB")),
                "plan": r.get("dividendPlan", ""),
                "ex_date": r.get("exDiviDate", ""),
            })
        # 计算平均股息率
        q = self.metrics.get("quote", {})
        price = q.get("price")
        if m["history"] and price and price > 0:
            yields = []
            for d in m["history"]:
                if d["amount"]:
                    yields.append(d["amount"] / price * 100)
            m["avg_yield"] = sum(yields) / len(yields) if yields else None
        else:
            m["avg_yield"] = None
        self.metrics["dividend"] = m

    def _parse_kline(self):
        m = {}
        # 日K
        day_rows = parse_table_rows(self.raw.get("kline_day", ""))
        day_klines = []
        for r in day_rows:
            day_klines.append({
                "date": r.get("date", ""),
                "open": safe_float(r.get("open")),
                "close": safe_float(r.get("last")),
                "high": safe_float(r.get("high")),
                "low": safe_float(r.get("low")),
                "volume": safe_float(r.get("volume")),
            })
        # 日K数据是倒序的（最新在前），需要反转
        day_klines.reverse()
        m["day"] = day_klines[-60:] if len(day_klines) > 60 else day_klines

        # 周K
        week_rows = parse_table_rows(self.raw.get("kline_week", ""))
        week_klines = []
        for r in week_rows:
            week_klines.append({
                "date": r.get("date", ""),
                "open": safe_float(r.get("open")),
                "close": safe_float(r.get("last")),
                "high": safe_float(r.get("high")),
                "low": safe_float(r.get("low")),
                "volume": safe_float(r.get("volume")),
            })
        week_klines.reverse()
        m["week"] = week_klines[-30:] if len(week_klines) > 30 else week_klines
        self.metrics["kline"] = m


# ============================================================
# 估值引擎
# ============================================================
class ValuationEngine:
    """DCF 三情景 + 相对估值法"""

    def __init__(self, metrics, stock_code):
        self.m = metrics
        self.code = stock_code

    def run(self):
        result = {}
        result["dcf"] = self._dcf_valuation()
        result["relative"] = self._relative_valuation()
        result["summary"] = self._valuation_summary(result["dcf"], result["relative"])
        return result

    def _dcf_valuation(self):
        fin = self.m.get("finance", {})
        quote = self.m.get("quote", {})
        periods = fin.get("periods", [])
        latest = fin.get("latest", {})

        # 优先使用 TTM 数据（避免季度数据导致基数偏低）
        base_profit = latest.get("ttm_profit") or latest.get("net_profit") or 0
        base_revenue = latest.get("ttm_revenue") or latest.get("revenue") or 0

        # 历史增长率
        growth_rates = []
        for p in periods:
            yoy = p.get("revenue_yoy")
            if yoy is not None:
                growth_rates.append(yoy)
        avg_growth = sum(growth_rates) / len(growth_rates) if growth_rates else 5.0

        # FCF 估算：优先使用最近年报数据，避免季度数据偏低
        annual_period = None
        for p in reversed(periods):
            if p.get("date", "").endswith("12-31"):
                annual_period = p
                break
        if annual_period and annual_period.get("fcf"):
            base_fcf = annual_period["fcf"]
        elif annual_period:
            ocf = annual_period.get("operating_cf") or 0
            icf = annual_period.get("investing_cf") or 0
            base_fcf = ocf + icf if (ocf and icf) else base_profit * 0.8
        else:
            # 退化：TTM经营现金流近似
            base_fcf = latest.get("fcf") or base_profit * 0.8
        if base_fcf <= 0:
            base_fcf = base_profit * 0.8 if base_profit > 0 else 1e8

        # WACC
        total_equity = latest.get("total_equity") or 0
        total_liab = latest.get("total_liab") or 0
        total_capital = total_equity + total_liab
        if total_capital > 0:
            w_eq = total_equity / total_capital
            w_debt = total_liab / total_capital
        else:
            w_eq = 0.8
            w_debt = 0.2
        cost_equity = RISK_FREE_RATE + 1.0 * EQUITY_RISK_PREMIUM
        cost_debt = 0.035 * (1 - 0.25)
        wacc = w_eq * cost_equity + w_debt * cost_debt
        wacc = max(wacc, 0.06)

        total_shares = quote.get("total_share") or 0

        scenarios = {
            "乐观": {"growth": max(avg_growth * 1.3, 8.0), "terminal": 3.0},
            "中性": {"growth": max(avg_growth * 0.9, 3.0), "terminal": 2.0},
            "悲观": {"growth": min(avg_growth * 0.5, 2.0), "terminal": 1.0},
        }

        results = {}
        for name, params in scenarios.items():
            proj_fcfs = []
            curr_fcf = base_fcf
            for year in range(1, 6):
                growth = params["growth"] / 100
                if year > 3:
                    growth *= max(1 - 0.15 * (year - 3), 0.3)
                curr_fcf = curr_fcf * (1 + growth)
                proj_fcfs.append(curr_fcf)

            terminal_growth = params["terminal"] / 100
            terminal_value = proj_fcfs[-1] * (1 + terminal_growth) / (wacc - terminal_growth)

            pv_fcfs = sum(fcf / (1 + wacc) ** (i + 1) for i, fcf in enumerate(proj_fcfs))
            pv_terminal = terminal_value / (1 + wacc) ** 5
            enterprise_value = pv_fcfs + pv_terminal

            # 净负债调整
            net_debt = max(total_liab * 0.3, 0)
            equity_value = enterprise_value - net_debt

            per_share = 0
            if total_shares and total_shares > 0:
                per_share = equity_value / total_shares

            results[name] = {
                "enterprise_value": enterprise_value,
                "equity_value": equity_value,
                "per_share": per_share,
                "wacc": wacc * 100,
                "growth_rate": params["growth"],
                "terminal_growth": params["terminal"],
            }

        return {
            "scenarios": results,
            "wacc": wacc * 100,
            "base_fcf": base_fcf,
            "base_profit": base_profit,
            "base_revenue": base_revenue,
            "avg_growth": avg_growth,
            "total_shares": total_shares,
        }

    def _relative_valuation(self):
        quote = self.m.get("quote", {})
        fin = self.m.get("finance", {})
        latest = fin.get("latest", {})

        price = quote.get("price") or 0
        pe = quote.get("pe_ttm")
        pb = quote.get("pb")
        ps = quote.get("ps_ttm")
        div_yield = quote.get("dividend_yield") or self.m.get("dividend", {}).get("avg_yield") or 0

        eps = latest.get("eps") or 0
        # TTM EPS 更准确
        ttm_profit = latest.get("ttm_profit")
        total_shares = quote.get("total_share") or 0
        if ttm_profit and total_shares and total_shares > 0:
            eps_ttm = ttm_profit / total_shares
        else:
            eps_ttm = eps

        roe = latest.get("roe") or 0
        net_margin = latest.get("net_margin") or 0

        # PE 估值
        target_pe = {"low": 10, "mid": 15, "high": 20}
        pe_vals = {k: eps_ttm * v for k, v in target_pe.items()} if eps_ttm else {"low": 0, "mid": 0, "high": 0}

        # PB 估值
        bps = 0
        total_equity = latest.get("total_equity") or 0
        if total_equity and total_shares and total_shares > 0:
            bps = total_equity / total_shares
        target_pb = {"low": 1.0, "mid": 1.5, "high": 2.5}
        pb_vals = {k: bps * v for k, v in target_pb.items()} if bps else {"low": 0, "mid": 0, "high": 0}

        # 股息率估值
        div_history = self.m.get("dividend", {}).get("history", [])
        # 年化股息（最近一次分红 * 2 假设半年一次，或取最近4次之和）
        annual_div = 0
        if div_history:
            # 取最近2条（假设每半年分红一次）
            recent = div_history[:2]
            annual_div = sum(d["amount"] or 0 for d in recent)
        target_yield = {"low": 0.04, "mid": 0.05, "high": 0.07}
        div_vals = {}
        for k, v in target_yield.items():
            div_vals[k] = annual_div / v if annual_div and v else 0

        return {
            "current_price": price,
            "current_pe": pe,
            "current_pb": pb,
            "current_ps": ps,
            "current_div_yield": div_yield,
            "eps": eps_ttm,
            "eps_reported": eps,
            "bps": bps,
            "roe": roe,
            "net_margin": net_margin,
            "annual_div": annual_div,
            "pe": pe_vals,
            "pb": pb_vals,
            "dividend": div_vals,
        }

    def _valuation_summary(self, dcf, rel):
        price = rel.get("current_price") or 0
        all_vals = [
            dcf["scenarios"]["乐观"]["per_share"],
            dcf["scenarios"]["中性"]["per_share"],
            dcf["scenarios"]["悲观"]["per_share"],
            rel["pe"]["low"], rel["pe"]["mid"], rel["pe"]["high"],
            rel["pb"]["low"], rel["pb"]["mid"], rel["pb"]["high"],
            rel["dividend"]["low"], rel["dividend"]["mid"], rel["dividend"]["high"],
        ]
        valid_vals = [v for v in all_vals if v and v > 0]
        val_low = min(valid_vals) if valid_vals else 0
        val_high = max(valid_vals) if valid_vals else 0
        val_mid = sum(valid_vals) / len(valid_vals) if valid_vals else 0
        upside = ((val_mid - price) / price * 100) if price and price > 0 else 0
        return {
            "price": price,
            "val_low": val_low,
            "val_mid": val_mid,
            "val_high": val_high,
            "upside": upside,
            "dcf_opt": dcf["scenarios"]["乐观"]["per_share"],
            "dcf_base": dcf["scenarios"]["中性"]["per_share"],
            "dcf_pess": dcf["scenarios"]["悲观"]["per_share"],
        }


# ============================================================
# HTML 报告生成器
# ============================================================
class ReportGenerator:
    """生成符合卖方研报标准的 HTML 报告"""

    def __init__(self, metrics, valuation, stock_code):
        self.m = metrics
        self.val = valuation
        self.code = stock_code
        self.today = datetime.date.today().strftime("%Y-%m-%d")

    def generate(self):
        return self._build_html()

    @staticmethod
    def _fmt(val, decimals=2, suffix="", default="--"):
        if val is None:
            return default
        if isinstance(val, (int, float)):
            if abs(val) >= 1e8:
                return f"{val / 1e8:.{decimals}f}亿{suffix}"
            elif abs(val) >= 1e4:
                return f"{val / 1e4:.{decimals}f}万{suffix}"
            return f"{val:.{decimals}f}{suffix}"
        return str(val)

    @staticmethod
    def _fmt_pct(val, decimals=1, default="--"):
        if val is None:
            return default
        return f"{val:.{decimals}f}%"

    @staticmethod
    def _color_val(val, prefix=""):
        if val is None:
            return '<span class="neutral">--</span>'
        if val > 0:
            return f'<span class="pos">{prefix}{val:.2f}%</span>'
        elif val < 0:
            return f'<span class="neg">{val:.2f}%</span>'
        return f'<span class="neutral">{prefix}{val:.2f}%</span>'

    def _signal_ma(self, price, ma):
        if not price or not ma:
            return '<span class="neutral">--</span>'
        if price > ma:
            return '<span class="pos">上方</span>'
        return '<span class="neg">下方</span>'

    @staticmethod
    def _signal_macd(dif, dea):
        if dif is None or dea is None:
            return '<span class="neutral">--</span>'
        if dif > dea:
            return '<span class="pos">金叉</span>'
        return '<span class="neg">死叉</span>'

    @staticmethod
    def _signal_boll(price, upper, lower):
        if not price or not upper or not lower:
            return '<span class="neutral">--</span>'
        if price <= lower:
            return '<span class="pos">触下轨，超卖</span>'
        elif price >= upper:
            return '<span class="neg">触上轨，超买</span>'
        return '<span class="neutral">中轨附近</span>'

    @staticmethod
    def _signal_rsi(rsi6):
        if rsi6 is None:
            return '<span class="neutral">--</span>'
        if rsi6 < 30:
            return '<span class="pos">超卖区</span>'
        elif rsi6 > 70:
            return '<span class="neg">超买区</span>'
        elif rsi6 < 45:
            return '<span style="color:#f39c12;">偏弱</span>'
        elif rsi6 > 55:
            return '<span style="color:#2ecc71;">偏强</span>'
        return '<span class="neutral">中性</span>'

    def _score_bar(self, label, score, max_val=100):
        if score is None:
            pct, display = 0, "--"
        else:
            pct = min(score / max_val * 100, 100) if max_val > 0 else 0
            display = f"{score:.0f}"
        return f"""<div style="margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
            <span style="font-size:13px;">{label}</span>
            <span style="font-size:13px;font-weight:600;color:var(--primary);">{display}</span>
          </div>
          <div class="score-bar"><div class="fill" style="width:{pct:.0f}%;"></div></div>
        </div>"""

    def _build_html(self):
        q = self.m.get("quote", {})
        fin = self.m.get("finance", {})
        tech = self.m.get("technical", {})
        cons = self.m.get("consensus", {})
        score = self.m.get("score", {})
        div = self.m.get("dividend", {})
        chip = self.m.get("chip", {})
        kline = self.m.get("kline", {})
        val = self.val

        name = q.get("name", "未知")
        price = q.get("price")
        mkt_cap = q.get("market_cap")
        pe = q.get("pe_ttm")
        pb = q.get("pb")
        div_yield = q.get("dividend_yield") or div.get("avg_yield")

        latest = fin.get("latest", {})
        periods = fin.get("periods", [])
        # 取最近4期
        recent_periods = periods[-4:] if len(periods) >= 4 else periods

        upside = val["summary"]["upside"]
        if upside > 20:
            rating, rating_color = "买入 (Buy)", "#c0392b"
        elif upside > 5:
            rating, rating_color = "增持 (Overweight)", "#e67e22"
        elif upside > -10:
            rating, rating_color = "持有 (Hold)", "#7f8c8d"
        elif upside > -20:
            rating, rating_color = "减持 (Underweight)", "#2980b9"
        else:
            rating, rating_color = "卖出 (Sell)", "#2c3e50"

        target_price = val["summary"]["val_mid"]
        stop_loss = (price * 0.92) if price else None

        # 安全的价格比较
        price_pos = "内"
        if price and val["summary"]["val_low"] and price < val["summary"]["val_low"]:
            price_pos = "下方"
        elif price and val["summary"]["val_high"] and price > val["summary"]["val_high"]:
            price_pos = "上方"

        # 营收净利润图表数据
        chart_labels = json.dumps([p.get("date", "")[:10] for p in recent_periods])
        chart_revenues = json.dumps([p.get("revenue") or 0 for p in recent_periods])
        chart_profits = json.dumps([p.get("net_profit") or 0 for p in recent_periods])

        # 评级分布
        cons_forecasts = cons.get("forecasts", [])
        analyst_count = int(cons.get("analyst_count", 0))

        # K线数据
        day_klines = kline.get("day", [])[-30:]
        kline_labels = json.dumps([d.get("date", "")[-5:] for d in day_klines])
        kline_closes = json.dumps([d.get("close") or 0 for d in day_klines])
        kline_volumes = json.dumps([d.get("volume") or 0 for d in day_klines])

        # 分红历史
        div_history = div.get("history", [])[:5]

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}({self.code}) 深度研报 - {self.today}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{{--primary:#8b0000;--accent:#d4af37;--bg:#fafafa;--card-bg:#fff;--text:#2c3e50;--text-light:#7f8c8d;--border:#e8e8e8;--pos:#27ae60;--neg:#e74c3c}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"PingFang SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;font-size:14px}}
.container{{max-width:920px;margin:0 auto;padding:24px 20px}}
h1,h2,h3{{color:var(--primary)}}
h2{{font-size:20px;margin:28px 0 16px;padding-bottom:8px;border-bottom:2px solid var(--accent);display:flex;align-items:center;gap:8px}}
h2 .num{{display:inline-block;width:28px;height:28px;background:var(--primary);color:#fff;border-radius:50%;text-align:center;line-height:28px;font-size:14px}}
.card{{background:var(--card-bg);border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,0.06)}}
table{{width:100%;border-collapse:collapse;margin:8px 0}}
th,td{{padding:8px 12px;text-align:right;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}}
th{{color:var(--text-light);font-weight:600;font-size:13px}}
td:first-child,th:first-child{{text-align:left}}
.pos{{color:var(--pos);font-weight:600}}
.neg{{color:var(--neg);font-weight:600}}
.neutral{{color:var(--text-light)}}
.metric-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0}}
.metric-item{{background:var(--card-bg);border-radius:8px;padding:14px;text-align:center;border:1px solid var(--border)}}
.metric-item .label{{font-size:12px;color:var(--text-light);margin-bottom:4px}}
.metric-item .value{{font-size:20px;font-weight:700;color:var(--primary);font-variant-numeric:tabular-nums}}
.thesis-box{{border-radius:8px;padding:14px 16px;margin:8px 0}}
.thesis-box.bull{{background:#eafaf1;border-left:4px solid var(--pos)}}
.thesis-box.bear{{background:#fdedec;border-left:4px solid var(--neg)}}
.thesis-box h4{{margin-bottom:6px;font-size:14px}}
.thesis-box.bull h4{{color:var(--pos)}}
.thesis-box.bear h4{{color:var(--neg)}}
.thesis-box ul{{padding-left:18px;font-size:13px}}
.thesis-box li{{margin-bottom:4px}}
.rating-box{{background:linear-gradient(135deg,var(--primary),#a02020);color:#fff;border-radius:12px;padding:24px;display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px;text-align:center;margin-bottom:20px}}
.rating-box .item .label{{font-size:12px;opacity:.8;margin-bottom:4px}}
.rating-box .item .value{{font-size:22px;font-weight:700}}
.rating-box .item .value.rating{{font-size:26px}}
.chart-container{{position:relative;height:280px;margin:12px 0}}
.score-bar{{height:24px;background:#eee;border-radius:12px;overflow:hidden;margin:6px 0;position:relative}}
.score-bar .fill{{height:100%;background:linear-gradient(90deg,var(--primary),var(--accent));border-radius:12px}}
.support-resist{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:12px 0}}
.sr-card{{border-radius:8px;padding:14px;text-align:center}}
.sr-card.support{{background:#eafaf1;border:1px solid #27ae60}}
.sr-card.resist{{background:#fdedec;border:1px solid #e74c3c}}
.sr-card .label{{font-size:12px;color:var(--text-light);margin-bottom:4px}}
.sr-card .price{{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}}
.sr-card.support .price{{color:var(--pos)}}
.sr-card.resist .price{{color:var(--neg)}}
.val-range{{height:32px;border-radius:16px;background:linear-gradient(90deg,#2ecc71,#f1c40f,#e74c3c);margin:16px 0 8px;position:relative}}
.val-range .marker{{position:absolute;top:-4px;width:3px;height:40px;background:var(--primary);border-radius:2px}}
.disclaimer{{margin-top:32px;padding:16px;background:#f5f5f5;border-radius:8px;font-size:12px;color:var(--text-light);line-height:1.8}}
.disclaimer h3{{font-size:14px;margin-bottom:8px;color:var(--text-light)}}
.report-header{{background:linear-gradient(135deg,var(--primary),#6b0000);color:#fff;border-radius:12px;padding:28px;margin-bottom:20px}}
.report-header .ticker{{display:inline-block;background:rgba(255,255,255,.2);padding:2px 10px;border-radius:4px;font-size:12px;margin-bottom:8px}}
.report-header h1{{color:#fff;font-size:28px;margin-bottom:4px}}
.report-header .subtitle{{font-size:14px;opacity:.85;margin-bottom:16px}}
.report-header .key-metrics{{display:flex;gap:24px;flex-wrap:wrap}}
.report-header .key-metrics .km .label{{font-size:11px;opacity:.7}}
.report-header .key-metrics .km .val{{font-size:18px;font-weight:700}}
@media(max-width:600px){{.metric-grid{{grid-template-columns:repeat(2,1fr)}}.rating-box{{grid-template-columns:1fr 1fr}}}}
</style>
</head>
<body>
<div class="container">

<!-- Report Header -->
<div class="report-header">
  <span class="ticker">{self.code}</span>
  <h1>{name}</h1>
  <div class="subtitle">业绩分析 · 估值建模 · 技术研判 | {self.today}</div>
  <div class="key-metrics">
    <div class="km"><div class="label">股价</div><div class="val">{self._fmt(price, 2, "元")}</div></div>
    <div class="km"><div class="label">市值</div><div class="val">{self._fmt(mkt_cap)}</div></div>
    <div class="km"><div class="label">PE(TTM)</div><div class="val">{self._fmt(pe, 1)}</div></div>
    <div class="km"><div class="label">PB</div><div class="val">{self._fmt(pb, 2)}</div></div>
    <div class="km"><div class="label">股息率</div><div class="val">{self._fmt_pct(div_yield)}</div></div>
  </div>
</div>

<!-- Rating Box -->
<div class="rating-box">
  <div class="item"><div class="label">投资评级</div><div class="value rating" style="color:{rating_color};">{rating}</div></div>
  <div class="item"><div class="label">目标价</div><div class="value">{self._fmt(target_price, 2, "元")}</div></div>
  <div class="item"><div class="label">预期涨幅</div><div class="value" style="color:{'#2ecc71' if upside > 0 else '#ff6b6b'};">{upside:+.1f}%</div></div>
  <div class="item"><div class="label">风险等级</div><div class="value" style="font-size:18px;">{"中等" if abs(upside) < 30 else "较高"}</div></div>
</div>

<!-- 1. 投资摘要 -->
<h2><span class="num">1</span>投资摘要</h2>
<div class="card">
  <p style="margin-bottom:12px;">{name}（{self.code}）当前股价 <strong>{self._fmt(price, 2, "元")}</strong>，对应 PE(TTM) {self._fmt(pe, 1)}，PB {self._fmt(pb, 2)}，股息率 {self._fmt_pct(div_yield)}。综合估值模型测算，合理价值区间为 <strong>{self._fmt(val['summary']['val_low'], 2, "元")}</strong> ~ <strong>{self._fmt(val['summary']['val_high'], 2, "元")}</strong>，中位估值 <strong>{self._fmt(val['summary']['val_mid'], 2, "元")}</strong>，对应预期涨幅 <strong style="color:{'var(--pos)' if upside > 0 else 'var(--neg)'};">{upside:+.1f}%</strong>。</p>
  <div class="thesis-box bull">
    <h4>多头逻辑</h4>
    <ul>
      <li>估值处于低位区间，PE {self._fmt(pe, 1)} / PB {self._fmt(pb, 2)} 提供安全边际</li>
      <li>股息率 {self._fmt_pct(div_yield)} 高于市场平均，防御属性突出</li>
      <li>机构一致目标价 {self._fmt(cons.get('target_price'), 2, "元")}，{analyst_count} 家机构覆盖</li>
    </ul>
  </div>
  <div class="thesis-box bear">
    <h4>空头逻辑</h4>
    <ul>
      <li>营收同比 {self._fmt_pct(latest.get('revenue_yoy'))}，增长承压</li>
      <li>净利润同比 {self._fmt_pct(latest.get('profit_yoy'))}，盈利能力边际变化需关注</li>
      <li>行业竞争格局变化及宏观环境不确定性</li>
    </ul>
  </div>
</div>

<!-- 2. 业绩深度分析 -->
<h2><span class="num">2</span>业绩深度分析</h2>
<div class="card">
  <h3 style="font-size:15px;margin-bottom:8px;">最近报告期财务数据</h3>
  <table>
    <thead><tr><th>指标</th>{"".join(f"<th>{p['date'][:10]}</th>" for p in recent_periods)}</tr></thead>
    <tbody>
      <tr><td>营业收入</td>{"".join(f"<td>{self._fmt(p.get('revenue'))}</td>" for p in recent_periods)}</tr>
      <tr><td>归母净利润</td>{"".join(f"<td>{self._fmt(p.get('net_profit'))}</td>" for p in recent_periods)}</tr>
      <tr><td>EPS</td>{"".join(f"<td>{self._fmt(p.get('eps'), 2, '元')}</td>" for p in recent_periods)}</tr>
      <tr><td>毛利率</td>{"".join(f"<td>{self._fmt_pct(p.get('gross_margin'))}</td>" for p in recent_periods)}</tr>
      <tr><td>净利率</td>{"".join(f"<td>{self._fmt_pct(p.get('net_margin'))}</td>" for p in recent_periods)}</tr>
      <tr><td>ROE</td>{"".join(f"<td>{self._fmt_pct(p.get('roe'))}</td>" for p in recent_periods)}</tr>
      <tr><td>营收同比</td>{"".join(f"<td>{self._color_val(p.get('revenue_yoy'))}</td>" for p in recent_periods)}</tr>
      <tr><td>净利同比</td>{"".join(f"<td>{self._color_val(p.get('profit_yoy'))}</td>" for p in recent_periods)}</tr>
    </tbody>
  </table>
  <div class="chart-container" style="height:300px;"><canvas id="chartRevenue"></canvas></div>
  <div class="metric-grid" style="margin-top:16px;">
    <div class="metric-item"><div class="label">毛利率</div><div class="value">{self._fmt_pct(latest.get('gross_margin'))}</div></div>
    <div class="metric-item"><div class="label">净利率</div><div class="value">{self._fmt_pct(latest.get('net_margin'))}</div></div>
    <div class="metric-item"><div class="label">ROE</div><div class="value">{self._fmt_pct(latest.get('roe'))}</div></div>
    <div class="metric-item"><div class="label">股息率</div><div class="value">{self._fmt_pct(div_yield)}</div></div>
  </div>
</div>

<!-- 分红历史 -->
<div class="card">
  <h3 style="font-size:15px;margin-bottom:8px;">分红历史</h3>
  <table>
    <thead><tr><th>报告期</th><th>每股股利(元)</th><th>分红方案</th></tr></thead>
    <tbody>
      {"".join(f"<tr><td>{d.get('year', '')[:10]}</td><td>{self._fmt(d.get('amount'), 4)}</td><td style='text-align:left;'>{d.get('plan', '')}</td></tr>" for d in div_history)}
    </tbody>
  </table>
</div>

<!-- 3. 机构共识与预期 -->
<h2><span class="num">3</span>机构共识与预期</h2>
<div class="card">
  <div class="metric-grid">
    <div class="metric-item"><div class="label">机构目标价</div><div class="value">{self._fmt(cons.get('target_price'), 2, "元")}</div></div>
    <div class="metric-item"><div class="label">覆盖机构</div><div class="value">{analyst_count}</div></div>
    <div class="metric-item"><div class="label">预测EPS(2026)</div><div class="value">{self._fmt(cons.get('eps_forecast'), 2, "元")}</div></div>
    <div class="metric-item"><div class="label">预测净利</div><div class="value">{self._fmt(cons.get('profit_forecast'))}</div></div>
  </div>
  <table>
    <thead><tr><th>年度</th><th>EPS</th><th>营收</th><th>净利润</th><th>PE</th><th>营收增速</th><th>净利增速</th></tr></thead>
    <tbody>
      {"".join(f"<tr><td>{f['year']}</td><td>{self._fmt(f.get('eps'), 2)}</td><td>{self._fmt(f.get('revenue'))}</td><td>{self._fmt(f.get('net_profit'))}</td><td>{self._fmt(f.get('pe'), 1)}</td><td>{self._color_val(f.get('revenue_yoy'))}</td><td>{self._color_val(f.get('profit_yoy'))}</td></tr>" for f in cons_forecasts)}
    </tbody>
  </table>
</div>

<!-- 4. 估值分析 -->
<h2><span class="num">4</span>估值分析</h2>
<div class="card">
  <h3 style="font-size:15px;margin-bottom:8px;">DCF 三情景估值</h3>
  <table>
    <thead><tr><th>情景</th><th>增速假设</th><th>永续增长</th><th>WACC</th><th>每股价值</th></tr></thead>
    <tbody>
      <tr><td>乐观</td><td>{self._fmt_pct(val['dcf']['scenarios']['乐观']['growth_rate'])}</td><td>{self._fmt_pct(val['dcf']['scenarios']['乐观']['terminal_growth'])}</td><td>{self._fmt(val['dcf']['scenarios']['乐观']['wacc'], 1, "%")}</td><td class="pos">{self._fmt(val['dcf']['scenarios']['乐观']['per_share'], 2, "元")}</td></tr>
      <tr><td>中性</td><td>{self._fmt_pct(val['dcf']['scenarios']['中性']['growth_rate'])}</td><td>{self._fmt_pct(val['dcf']['scenarios']['中性']['terminal_growth'])}</td><td>{self._fmt(val['dcf']['scenarios']['中性']['wacc'], 1, "%")}</td><td>{self._fmt(val['dcf']['scenarios']['中性']['per_share'], 2, "元")}</td></tr>
      <tr><td>悲观</td><td>{self._fmt_pct(val['dcf']['scenarios']['悲观']['growth_rate'])}</td><td>{self._fmt_pct(val['dcf']['scenarios']['悲观']['terminal_growth'])}</td><td>{self._fmt(val['dcf']['scenarios']['悲观']['wacc'], 1, "%")}</td><td class="neg">{self._fmt(val['dcf']['scenarios']['悲观']['per_share'], 2, "元")}</td></tr>
    </tbody>
  </table>
  <p style="font-size:12px;color:var(--text-light);margin-top:8px;">* 基于最近报告期净利润 {self._fmt(val['dcf']['base_profit'])}，历史均增 {self._fmt(val['dcf']['avg_growth'], 1, "%")}，基期FCF {self._fmt(val['dcf']['base_fcf'])}，WACC {self._fmt(val['dcf']['wacc'], 1, "%")}</p>

  <h3 style="font-size:15px;margin:16px 0 8px;">相对估值法</h3>
  <table>
    <thead><tr><th>方法</th><th>悲观</th><th>中性</th><th>乐观</th></tr></thead>
    <tbody>
      <tr><td>PE估值</td><td>{self._fmt(val['relative']['pe']['low'], 2, "元")}</td><td>{self._fmt(val['relative']['pe']['mid'], 2, "元")}</td><td>{self._fmt(val['relative']['pe']['high'], 2, "元")}</td></tr>
      <tr><td>PB估值</td><td>{self._fmt(val['relative']['pb']['low'], 2, "元")}</td><td>{self._fmt(val['relative']['pb']['mid'], 2, "元")}</td><td>{self._fmt(val['relative']['pb']['high'], 2, "元")}</td></tr>
      <tr><td>股息率估值</td><td>{self._fmt(val['relative']['dividend']['low'], 2, "元")}</td><td>{self._fmt(val['relative']['dividend']['mid'], 2, "元")}</td><td>{self._fmt(val['relative']['dividend']['high'], 2, "元")}</td></tr>
    </tbody>
  </table>

  <h3 style="font-size:15px;margin:16px 0 8px;">估值区间汇总</h3>
  <div class="val-range">
    <div class="marker" style="left:0%;" ></div>
    <div class="marker" style="left:50%;" ></div>
    <div class="marker" style="left:100%;" ></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-light);">
    <span>低:{self._fmt(val['summary']['val_low'], 2, "元")}</span>
    <span>中:{self._fmt(val['summary']['val_mid'], 2, "元")}</span>
    <span>高:{self._fmt(val['summary']['val_high'], 2, "元")}</span>
  </div>
  <p style="margin-top:12px;font-size:13px;">当前价 <strong>{self._fmt(price, 2, "元")}</strong> 处于估值区间{price_pos}，{"存在低估" if price_pos == "下方" else "估值偏高" if price_pos == "上方" else "合理区间内"}。</p>
</div>

<!-- 5. 技术面分析 -->
<h2><span class="num">5</span>技术面分析</h2>
<div class="card">
  <table>
    <thead><tr><th>指标</th><th>数值</th><th>信号</th></tr></thead>
    <tbody>
      <tr><td>MA5</td><td>{self._fmt(tech.get('ma5'), 2)}</td><td>{self._signal_ma(price, tech.get('ma5'))}</td></tr>
      <tr><td>MA10</td><td>{self._fmt(tech.get('ma10'), 2)}</td><td>{self._signal_ma(price, tech.get('ma10'))}</td></tr>
      <tr><td>MA20</td><td>{self._fmt(tech.get('ma20'), 2)}</td><td>{self._signal_ma(price, tech.get('ma20'))}</td></tr>
      <tr><td>MA60</td><td>{self._fmt(tech.get('ma60'), 2)}</td><td>{self._signal_ma(price, tech.get('ma60'))}</td></tr>
      <tr><td>MA120</td><td>{self._fmt(tech.get('ma120'), 2)}</td><td>{self._signal_ma(price, tech.get('ma120'))}</td></tr>
      <tr><td>MACD(DIF/DEA)</td><td>{self._fmt(tech.get('macd_dif'), 3)} / {self._fmt(tech.get('macd_dea'), 3)}</td><td>{self._signal_macd(tech.get('macd_dif'), tech.get('macd_dea'))}</td></tr>
      <tr><td>BOLL(上/中/下)</td><td>{self._fmt(tech.get('boll_upper'), 2)} / {self._fmt(tech.get('boll_mid'), 2)} / {self._fmt(tech.get('boll_lower'), 2)}</td><td>{self._signal_boll(price, tech.get('boll_upper'), tech.get('boll_lower'))}</td></tr>
      <tr><td>RSI(6/12/24)</td><td>{self._fmt(tech.get('rsi6'), 1)} / {self._fmt(tech.get('rsi12'), 1)} / {self._fmt(tech.get('rsi24'), 1)}</td><td>{self._signal_rsi(tech.get('rsi6'))}</td></tr>
    </tbody>
  </table>
  <div class="support-resist">
    <div class="sr-card support"><div class="label">支撑位</div><div class="price">{self._fmt(tech.get('boll_lower') or (price * 0.92 if price else None), 2, "元")}</div><div style="font-size:12px;color:var(--text-light);">布林下轨</div></div>
    <div class="sr-card resist"><div class="label">压力位</div><div class="price">{self._fmt(tech.get('boll_upper') or (price * 1.08 if price else None), 2, "元")}</div><div style="font-size:12px;color:var(--text-light);">布林上轨</div></div>
  </div>
  <div class="chart-container" style="height:300px;"><canvas id="chartKline"></canvas></div>
</div>

<!-- 6. 综合评分 -->
<h2><span class="num">6</span>综合评分</h2>
<div class="card">
  {self._score_bar("综合评分", score.get('overall'))}
  {self._score_bar("基本面", score.get('fundamental'))}
  {self._score_bar("技术面", score.get('technical'))}
  {self._score_bar("风险", score.get('risk'))}
  {self._score_bar("资金面", score.get('capital'))}
</div>

<!-- 7. 投资评级与操作建议 -->
<h2><span class="num">7</span>投资评级与操作建议</h2>
<div class="card">
  <table>
    <thead><tr><th>评估维度</th><th>评估结果</th></tr></thead>
    <tbody>
      <tr><td>估值水平</td><td>PE {self._fmt(pe, 1)} / PB {self._fmt(pb, 2)} / 股息率 {self._fmt_pct(div_yield)}</td></tr>
      <tr><td>盈利能力</td><td>ROE {self._fmt_pct(latest.get('roe'))} / 净利率 {self._fmt_pct(latest.get('net_margin'))}</td></tr>
      <tr><td>成长性</td><td>营收同比 {self._fmt_pct(latest.get('revenue_yoy'))} / 净利同比 {self._fmt_pct(latest.get('profit_yoy'))}</td></tr>
      <tr><td>技术面</td><td>{self._signal_rsi(tech.get('rsi6'))}</td></tr>
      <tr><td>机构观点</td><td>{analyst_count} 家覆盖，目标价 {self._fmt(cons.get('target_price'), 2, "元")}</td></tr>
      <tr><td>筹码集中度</td><td>90集中度 {self._fmt_pct(chip.get('concentration_90'))} / 获利比例 {self._fmt_pct(chip.get('profit_ratio'))}</td></tr>
    </tbody>
  </table>
  <div style="margin-top:16px;padding:16px;background:#f9f9f9;border-radius:8px;border-left:4px solid var(--primary);">
    <strong>评级：{rating}</strong> | 目标价：<strong>{self._fmt(target_price, 2, "元")}</strong> | 预期涨幅：<strong style="color:{'var(--pos)' if upside > 0 else 'var(--neg)'};">{upside:+.1f}%</strong><br>
    <span style="font-size:13px;color:var(--text-light);margin-top:4px;display:block;">操作建议：{"逢低关注，分批建仓" if upside > 10 else "持有观望，等待催化" if upside > 0 else "控制仓位，谨慎对待"} | 止损参考：{self._fmt(stop_loss, 2, "元")}（-8%）</span>
  </div>
</div>

<!-- 8. 风险提示 -->
<h2><span class="num">8</span>风险提示</h2>
<div class="card">
  <table>
    <thead><tr><th>风险类型</th><th>说明</th></tr></thead>
    <tbody>
      <tr><td>行业风险</td><td style="text-align:left;">行业竞争加剧，市场份额变化风险</td></tr>
      <tr><td>政策风险</td><td style="text-align:left;">监管政策变化对公司经营的影响</td></tr>
      <tr><td>财务风险</td><td style="text-align:left;">营收/利润下滑，盈利能力边际弱化</td></tr>
      <tr><td>市场风险</td><td style="text-align:left;">宏观经济波动、利率变化对估值的影响</td></tr>
      <tr><td>模型风险</td><td style="text-align:left;">DCF 估值依赖假设参数，实际偏差可能较大</td></tr>
    </tbody>
  </table>
</div>

<!-- 判断标准说明书 -->
{eval_guide.generate()}

<!-- 免责声明 -->
<div class="disclaimer">
  <h3>免责声明</h3>
  <p>本报告由自动化工具生成，数据来源于腾讯自选股行情接口及公司公告。报告中的估值模型基于特定假设，实际结果可能与预测存在重大差异。</p>
  <p>本报告仅供参考，不构成任何投资建议。投资者应根据自身风险承受能力独立做出投资决策，盈亏自负。</p>
  <p style="margin-top:8px;">报告生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | 数据来源：腾讯自选股</p>
</div>

</div>

<script>
Chart.defaults.font.family='"PingFang SC","Microsoft YaHei",sans-serif';
Chart.defaults.color='#7f8c8d';

// 营收净利润趋势
new Chart(document.getElementById('chartRevenue'),{{
  type:'bar',
  data:{{
    labels:{chart_labels},
    datasets:[
      {{label:'营业收入',data:{chart_revenues},backgroundColor:'rgba(139,0,0,0.7)',yAxisID:'y',order:2}},
      {{label:'归母净利润',data:{chart_profits},type:'line',borderColor:'#d4af37',backgroundColor:'#d4af37',borderWidth:2,pointRadius:4,yAxisID:'y1',order:1}}
    ]
  }},
  options:{{
    responsive:true,maintainAspectRatio:false,
    interaction:{{mode:'index',intersect:false}},
    plugins:{{legend:{{position:'top'}}}},
    scales:{{
      y:{{type:'log',position:'left',title:{{display:true,text:'营收'}}}},
      y1:{{type:'log',position:'right',title:{{display:true,text:'净利润'}},grid:{{drawOnChartArea:false}}}}
    }}
  }}
}});

// K线走势
new Chart(document.getElementById('chartKline'),{{
  type:'line',
  data:{{
    labels:{kline_labels},
    datasets:[
      {{label:'收盘价',data:{kline_closes},borderColor:'#8b0000',backgroundColor:'rgba(139,0,0,0.1)',borderWidth:2,pointRadius:0,fill:true,yAxisID:'y'}},
      {{label:'成交量',data:{kline_volumes},type:'bar',backgroundColor:'rgba(212,175,55,0.4)',yAxisID:'y1'}}
    ]
  }},
  options:{{
    responsive:true,maintainAspectRatio:false,
    interaction:{{mode:'index',intersect:false}},
    plugins:{{legend:{{position:'top'}}}},
    scales:{{
      y:{{position:'left',title:{{display:true,text:'价格'}}}},
      y1:{{position:'right',title:{{display:true,text:'成交量'}},grid:{{drawOnChartArea:false}}}}
    }}
  }}
}});
</script>
</body>
</html>"""
        return html


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
    code = normalize_code(raw_code)
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
    filename = f"{code}_report_{today}.html"
    filepath = os.path.join(REPORT_DIR, filename)
    if os.path.exists(filepath):
        log.info(f"今日已生成报告: {filepath}, 直接返回结果")
        return name, filename

    log.info(f"\n{'=' * 60}")
    log.info(f"  正在生成 {code} 研报...")
    log.info(f"{'=' * 60}")

    try:
        log.info("\n[1/4] 获取行情数据...")
        fetcher = DataFetcher(code)
        raw_data = fetcher.fetch_all()

        log.info("\n[2/4] 解析财务指标...")
        processor = DataProcessor(raw_data, code)
        metrics = processor.process()
        name = metrics["quote"].get("name", code)
        log.info(f"  公司: {name}")
        log.info(f"  股价: {metrics['quote'].get('price', '--')}")

        log.info("\n[3/4] 运行估值模型...")
        engine = ValuationEngine(metrics, code)
        valuation = engine.run()
        log.info(f"  DCF中性估值: {valuation['summary']['val_mid']:.2f}")
        log.info(f"  预期涨幅: {valuation['summary']['upside']:+.1f}%")

        log.info("\n[4/4] 生成HTML报告...")
        generator = ReportGenerator(metrics, valuation, code)
        html = generator.generate()

        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        log.info(f"\n  报告已保存: {filepath}")
        log.info(f"  文件大小: {os.path.getsize(filepath) / 1024:.1f} KB")
        return name, filename

    except Exception as e:
        log.warning(f"\n  [ERROR] 生成失败: {e}")
        return None, None
