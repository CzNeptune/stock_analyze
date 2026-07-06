#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
判断标准说明书 — 共享模块
被 stock_report.py 和 stock_evaluator.py 调用
生成五维评估框架图、买卖区间图、评判方法详解的 HTML 片段
色盲友好设计：Okabe-Ito 配色 + 纹理图案 + 符号标识三重区分
"""


def generate():
    """返回判断标准说明书的 HTML 片段（含 CSS + SVG + 内容）"""
    return _CSS + _HTML_CONTENT


# ============================================================
# 局部样式（scoped，不影响父页面）
# ============================================================
_CSS = """<style>
/* ====== 判断标准说明书 ====== */
.eval-guide {
  margin-top: 40px;
  padding-top: 24px;
  border-top: 3px solid #2C3E50;
}
.eval-guide h2 {
  font-size: 22px;
  color: #2C3E50;
  margin-bottom: 6px;
}
.eval-guide h3 {
  font-size: 17px;
  color: #34495E;
  margin-top: 28px;
  margin-bottom: 10px;
  padding-left: 10px;
  border-left: 4px solid #0072B2;
}
.eval-guide p {
  line-height: 1.7;
  color: #2C2C2A;
  font-size: 14px;
}
.eval-guide .eg-tip {
  background: #F8F9FA;
  border-left: 4px solid #E69F00;
  padding: 10px 14px;
  margin: 10px 0;
  font-size: 13px;
  color: #555;
  border-radius: 0 4px 4px 0;
}
.eval-guide .eg-tip strong { color: #B9770E; }

/* Zone table — 色盲友好三重区分 */
.eg-zone-table {
  width: 100%;
  border-collapse: collapse;
  margin: 14px 0;
  font-size: 13px;
}
.eg-zone-table th {
  padding: 10px 8px;
  text-align: center;
  font-weight: bold;
  color: white;
  font-size: 14px;
}
.eg-zone-table td {
  padding: 8px;
  text-align: center;
  border: 1px solid #ddd;
}
.eg-zone-table td:first-child {
  font-weight: bold;
  text-align: left;
  background: #F8F9FA;
  color: #2C3E50;
  white-space: nowrap;
}
/* 买入区：蓝色实心 */
.eg-th-buy { background: #0072B2; }
/* 观察区：橙色 + 斜线纹理 */
.eg-th-observe {
  background: #E69F00;
  background-image: repeating-linear-gradient(45deg, transparent, transparent 6px, rgba(255,255,255,0.25) 6px, rgba(255,255,255,0.25) 10px);
}
/* 回避区：朱红 + 网格纹理 */
.eg-th-avoid {
  background: #D55E00;
  background-image:
    repeating-linear-gradient(0deg, transparent, transparent 6px, rgba(255,255,255,0.2) 6px, rgba(255,255,255,0.2) 7px),
    repeating-linear-gradient(90deg, transparent, transparent 6px, rgba(255,255,255,0.2) 6px, rgba(255,255,255,0.2) 7px);
}
.eg-cell-buy { background: #E8F4FD; color: #0072B2; font-weight: bold; }
.eg-cell-observe { background: #FEF5E7; color: #B9770E; }
.eg-cell-avoid { background: #FDEBD0; color: #D55E00; font-weight: bold; }

/* 维度卡片 */
.eg-dim-card {
  background: white;
  border: 1px solid #E0E0E0;
  border-radius: 8px;
  padding: 14px 18px;
  margin: 10px 0;
}
.eg-dim-card h4 {
  font-size: 15px;
  margin: 0 0 4px 0;
  color: #2C3E50;
  display: flex;
  align-items: center;
  gap: 8px;
}
.eg-dim-card .eg-weight {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: bold;
  color: white;
}
.eg-dim-card .eg-sub {
  font-size: 13px;
  color: #888;
  margin: 2px 0 6px;
}
.eg-dim-card ul {
  margin: 6px 0;
  padding-left: 20px;
}
.eg-dim-card li {
  font-size: 13px;
  line-height: 1.8;
  color: #444;
}
.eg-dim-card li strong { color: #2C3E50; }

/* PM七问 */
.eg-pmq {
  counter-reset: pmq;
  list-style: none;
  padding: 0;
}
.eg-pmq li {
  counter-increment: pmq;
  padding: 10px 0 10px 42px;
  position: relative;
  font-size: 14px;
  line-height: 1.6;
  border-bottom: 1px solid #EEE;
}
.eg-pmq li::before {
  content: counter(pmq);
  position: absolute;
  left: 0;
  top: 9px;
  width: 28px;
  height: 28px;
  background: #2C3E50;
  color: white;
  border-radius: 50%;
  text-align: center;
  line-height: 28px;
  font-weight: bold;
  font-size: 13px;
}
.eg-pmq li strong { color: #2C3E50; }

/* 买入vs持有对比表 */
.eg-compare {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
  font-size: 13px;
}
.eg-compare th {
  color: white;
  padding: 10px;
  text-align: center;
  font-size: 14px;
}
.eg-compare th:first-child { background: #2C3E50; }
.eg-compare th:nth-child(2) { background: #0072B2; }
.eg-compare th:nth-child(3) {
  background: #E69F00;
  background-image: repeating-linear-gradient(45deg, transparent, transparent 6px, rgba(255,255,255,0.25) 6px, rgba(255,255,255,0.25) 10px);
}
.eg-compare td {
  padding: 8px 10px;
  border: 1px solid #DDD;
  line-height: 1.6;
}
.eg-compare td:first-child {
  font-weight: bold;
  background: #F8F9FA;
  width: 15%;
  color: #2C3E50;
}

/* 误区卡片 */
.eg-mistake {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  background: #FFF5F5;
  border-left: 4px solid #D55E00;
  padding: 12px 16px;
  margin: 10px 0;
  border-radius: 0 6px 6px 0;
}
.eg-mistake-num {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  background: #D55E00;
  color: white;
  border-radius: 50%;
  text-align: center;
  line-height: 28px;
  font-weight: bold;
  font-size: 13px;
}
.eg-mistake-body strong { color: #D55E00; }
.eg-mistake-body {
  font-size: 13px;
  line-height: 1.7;
  color: #444;
}

/* 评级阈值条 */
.eg-threshold-bar {
  display: flex;
  margin: 14px 0;
  border-radius: 6px;
  overflow: hidden;
  font-size: 13px;
  font-weight: bold;
}
.eg-threshold-bar > div {
  padding: 10px;
  text-align: center;
  color: white;
}
.eg-th-buy { background: #0072B2; flex: 3; }
.eg-th-hold {
  background: #E69F00;
  flex: 2;
  background-image: repeating-linear-gradient(45deg, transparent, transparent 6px, rgba(255,255,255,0.25) 6px, rgba(255,255,255,0.25) 10px);
}
.eg-th-avoid {
  background: #D55E00;
  flex: 2;
  background-image:
    repeating-linear-gradient(0deg, transparent, transparent 6px, rgba(255,255,255,0.2) 6px, rgba(255,255,255,0.2) 7px),
    repeating-linear-gradient(90deg, transparent, transparent 6px, rgba(255,255,255,0.2) 6px, rgba(255,255,255,0.2) 7px);
}
</style>"""


# ============================================================
# HTML 内容（SVG + 表格 + 文字）
# ============================================================
_HTML_CONTENT = """
<!-- ====== 判断标准说明书 ====== -->
<div class="eval-guide">
<h2>判断标准说明书</h2>
<p style="color:#666;font-size:13px;">本说明书解释报告中使用的五维评估框架、指标阈值和评判方法。蓝（买入）/ 橙（观察）/ 朱红（回避）三色 + 纹理图案 + 符号标识三重区分。</p>

<!-- ====== 一、五维评估框架 ====== -->
<h3>一、五维评估框架</h3>
<p>股票评估不依赖单一指标，而是从五个维度综合打分，按权重加权汇总后映射到 BUY / HOLD / AVOID 评级。每个维度满分 100 分。</p>

<svg viewBox="0 0 680 420" style="width:100%;max-width:680px;margin:0 auto;display:block;font-family:'PingFang SC','Microsoft YaHei',sans-serif;">
  <defs>
    <marker id="eg-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
      <path d="M0,0 L8,3 L0,6 Z" fill="#888"/>
    </marker>
  </defs>

  <!-- 虚线五边形 -->
  <polygon points="340,55 464,145 416,290 264,290 216,145"
    fill="none" stroke="#CCC" stroke-width="1.5" stroke-dasharray="4,3"/>

  <!-- 中心到各节点连线 -->
  <line x1="340" y1="185" x2="340" y2="55" stroke="#D5D5D5" stroke-width="1"/>
  <line x1="340" y1="185" x2="464" y2="145" stroke="#D5D5D5" stroke-width="1"/>
  <line x1="340" y1="185" x2="416" y2="290" stroke="#D5D5D5" stroke-width="1"/>
  <line x1="340" y1="185" x2="264" y2="290" stroke="#D5D5D5" stroke-width="1"/>
  <line x1="340" y1="185" x2="216" y2="145" stroke="#D5D5D5" stroke-width="1"/>

  <!-- 中心节点 -->
  <circle cx="340" cy="185" r="40" fill="#2C3E50"/>
  <text x="340" y="182" fill="white" text-anchor="middle" font-size="13" font-weight="bold">综合评分</text>
  <text x="340" y="198" fill="#AAA" text-anchor="middle" font-size="10">加权汇总</text>

  <!-- 节点1: 基本面 (top) -->
  <circle cx="340" cy="55" r="42" fill="#0072B2"/>
  <text x="340" y="50" fill="white" text-anchor="middle" font-size="13" font-weight="bold">基本面</text>
  <text x="340" y="68" fill="white" text-anchor="middle" font-size="15" font-weight="bold">25%</text>

  <!-- 节点2: 估值 (top-right) -->
  <circle cx="464" cy="145" r="42" fill="#E69F00"/>
  <text x="464" y="140" fill="white" text-anchor="middle" font-size="13" font-weight="bold">估值</text>
  <text x="464" y="158" fill="white" text-anchor="middle" font-size="15" font-weight="bold">25%</text>

  <!-- 节点3: 技术面 (bottom-right) -->
  <circle cx="416" cy="290" r="42" fill="#009E73"/>
  <text x="416" y="285" fill="white" text-anchor="middle" font-size="13" font-weight="bold">技术面</text>
  <text x="416" y="303" fill="white" text-anchor="middle" font-size="15" font-weight="bold">15%</text>

  <!-- 节点4: 资金与催化 (bottom-left) -->
  <circle cx="264" cy="290" r="42" fill="#CC79A7"/>
  <text x="264" y="284" fill="white" text-anchor="middle" font-size="11" font-weight="bold">资金与催化</text>
  <text x="264" y="302" fill="white" text-anchor="middle" font-size="15" font-weight="bold">15%</text>

  <!-- 节点5: 行业与竞争 (top-left) -->
  <circle cx="216" cy="145" r="42" fill="#56B4E9"/>
  <text x="216" y="140" fill="white" text-anchor="middle" font-size="11" font-weight="bold">行业竞争</text>
  <text x="216" y="158" fill="white" text-anchor="middle" font-size="15" font-weight="bold">20%</text>

  <!-- 公式 -->
  <rect x="100" y="345" width="480" height="30" rx="6" fill="#F8F9FA" stroke="#DDD" stroke-width="1"/>
  <text x="340" y="365" text-anchor="middle" font-size="12" fill="#555" font-weight="bold">
    综合评分 = 基本面 x 25% + 估值 x 25% + 技术面 x 15% + 行业 x 20% + 资金 x 15%
  </text>

  <!-- 评级阈值条 -->
  <rect x="100" y="390" width="288" height="24" rx="4" fill="#0072B2"/>
  <text x="244" y="407" text-anchor="middle" font-size="12" fill="white" font-weight="bold">>= 70 分  BUY</text>

  <rect x="394" y="390" width="96" height="24" fill="#E69F00"/>
  <rect x="394" y="390" width="96" height="24" fill="url(#eg-stripes-obs)" opacity="0.3"/>
  <text x="442" y="407" text-anchor="middle" font-size="11" fill="white" font-weight="bold">50-70 HOLD</text>

  <rect x="496" y="390" width="84" height="24" rx="4" fill="#D55E00"/>
  <text x="538" y="407" text-anchor="middle" font-size="11" fill="white" font-weight="bold">&lt; 50 AVOID</text>
</svg>

<!-- ====== 二、核心指标买卖区间 ====== -->
<h3>二、核心指标买卖区间速查</h3>
<p>下表列出六个最关键的评估指标及其买入区 / 观察区 / 回避区阈值。<strong style="color:#0072B2;">蓝色实心 + [OK] = 买入</strong> / <strong style="color:#B9770E;">橙色斜纹 + [?] = 观察</strong> / <strong style="color:#D55E00;">朱红网格 + [X] = 回避</strong>。</p>

<table class="eg-zone-table">
  <thead>
    <tr>
      <th style="background:#2C3E50;">核心指标</th>
      <th class="eg-th-buy">[OK] 买入区</th>
      <th class="eg-th-observe">[?] 观察区</th>
      <th class="eg-th-avoid">[X] 回避区</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>ROE</td>
      <td class="eg-cell-buy">&gt; 15%</td>
      <td class="eg-cell-observe">8% - 15%</td>
      <td class="eg-cell-avoid">&lt; 8%</td>
    </tr>
    <tr>
      <td>PEG</td>
      <td class="eg-cell-buy">&lt; 1.0</td>
      <td class="eg-cell-observe">1.0 - 2.0</td>
      <td class="eg-cell-avoid">&gt; 2.0</td>
    </tr>
    <tr>
      <td>PE 历史分位</td>
      <td class="eg-cell-buy">&lt; 30%</td>
      <td class="eg-cell-observe">30% - 60%</td>
      <td class="eg-cell-avoid">&gt; 60%</td>
    </tr>
    <tr>
      <td>营收增速</td>
      <td class="eg-cell-buy">&gt; 10%</td>
      <td class="eg-cell-observe">0% - 10%</td>
      <td class="eg-cell-avoid">&lt; 0%</td>
    </tr>
    <tr>
      <td>经营现金流/净利润</td>
      <td class="eg-cell-buy">&gt; 1.2</td>
      <td class="eg-cell-observe">0.8 - 1.2</td>
      <td class="eg-cell-avoid">&lt; 0.8</td>
    </tr>
    <tr>
      <td>资产负债率</td>
      <td class="eg-cell-buy">&lt; 40%</td>
      <td class="eg-cell-observe">40% - 60%</td>
      <td class="eg-cell-avoid">&gt; 60%</td>
    </tr>
  </tbody>
</table>
<div class="eg-tip"><strong>行业差异提醒</strong>：银行看 PB 不看 PE（利润被拨备调节）；科技/SaaS 看 PS 不看 PE（前期亏损正常）；周期股看 PB 历史分位（PE 低反而是顶部信号）。</div>

<!-- ====== 三、买入与持有 ====== -->
<h3>三、买入与持有的门槛差异</h3>
<p>这是很多人忽略的关键区别 —— <strong>买入的标准远高于持有</strong>。简单说：买入要挑剔，持有要包容。很多人反过来做 —— 买入时随便，持有时苛求 —— 结果买在高点、卖在低点。</p>

<table class="eg-compare">
  <thead>
    <tr>
      <th>对比维度</th>
      <th>适合买入</th>
      <th>适合持有</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>整体要求</td>
      <td>五维至少四维达标，且无硬伤</td>
      <td>基本面 + 估值两维仍成立即可</td>
    </tr>
    <tr>
      <td>估值要求</td>
      <td>必须便宜或合理（DCF 溢价 &gt; 20%）</td>
      <td>合理偏贵可容忍（溢价 &gt; 0%）</td>
    </tr>
    <tr>
      <td>技术面</td>
      <td>最好有买点配合（均线金叉、支撑位）</td>
      <td>技术面差可忽略（甚至逆势加仓）</td>
    </tr>
    <tr>
      <td>催化剂</td>
      <td>需要有明确催化剂（业绩超预期、政策利好）</td>
      <td>不需要，逻辑兑现中即可</td>
    </tr>
    <tr>
      <td>核心逻辑</td>
      <td>"好公司 + 好价格 + 好时机"三好</td>
      <td>"好公司没变坏"就继续拿着</td>
    </tr>
  </tbody>
</table>

<!-- ====== 四、五维详解 ====== -->
<h3>四、五维评估详解</h3>

<div class="eg-dim-card">
  <h4>1. 基本面 <span class="eg-weight" style="background:#0072B2;">权重 25%</span></h4>
  <p class="eg-sub">赚不赚钱 — 这是地基</p>
  <ul>
    <li><strong>盈利能力</strong>：ROE &gt; 15%（连续3年稳定更值钱）、毛利率趋势（稳定或上行）、净利率 vs 同行</li>
    <li><strong>成长性</strong>：营收增速 &gt; 10%、净利润增速 &gt; 营收增速（说明有经营杠杆）、EPS 增速</li>
    <li><strong>财务健康</strong>：经营现金流/净利润 &gt; 1（利润是真金白银不是应收账款）、资产负债率 &lt; 60%、利息保障倍数 &gt; 5</li>
  </ul>
  <div class="eg-tip"><strong>踩坑提醒</strong>：ROE 高可能是高杠杆堆出来的（杜邦分解：ROE = 净利率 x 资产周转率 x 权益乘数），要拆开看是哪种驱动。靠杠杆撑的 ROE 在去杠杆周期会很惨。</div>
</div>

<div class="eg-dim-card">
  <h4>2. 估值 <span class="eg-weight" style="background:#E69F00;">权重 25%</span></h4>
  <p class="eg-sub">便不便宜 — 好公司买贵了也亏钱</p>
  <ul>
    <li><strong>PE (TTM)</strong>：和自己历史比（5年分位数 &lt; 50%）、和同行比（低于行业中位数）</li>
    <li><strong>PEG</strong>：&lt; 1 说明增速撑得起估值，这是成长股最核心的估值锚</li>
    <li><strong>PB</strong>：适用于重资产行业（银行、地产、周期股），&gt; 2 要警惕</li>
    <li><strong>股息率</strong>：&gt; 无风险利率（国债）的 2 倍，且有连续 5 年派息记录</li>
    <li><strong>DCF</strong>：内在价值 vs 当前市价，溢价 &gt; 20% 才有安全边际</li>
  </ul>
</div>

<div class="eg-dim-card">
  <h4>3. 技术面 <span class="eg-weight" style="background:#009E73;">权重 15%</span></h4>
  <p class="eg-sub">何时出手 — 技术面不决定买不买，但决定什么时候买</p>
  <ul>
    <li><strong>趋势</strong>：站上 MA60 且 MA60 上行，不逆趋势交易</li>
    <li><strong>动量</strong>：MACD 金叉或零轴上方、RSI 在 40-65（不追超买）</li>
    <li><strong>量价</strong>：放量突破压力位有效，缩量回调健康</li>
    <li><strong>位置</strong>：布林带中轨以上，距下轨有空间</li>
  </ul>
  <div class="eg-tip"><strong>买入 vs 持有</strong>：买入时技术面权重更高（选个好买点）；持有时技术面权重极低（基本面没坏就不因技术面卖出）。</div>
</div>

<div class="eg-dim-card">
  <h4>4. 行业与竞争 <span class="eg-weight" style="background:#56B4E9;">权重 20%</span></h4>
  <p class="eg-sub">赛道好不好 — 选对赛道事半功倍</p>
  <ul>
    <li><strong>行业增速</strong>：&gt; GDP 增速，处于成长期或成熟期早期</li>
    <li><strong>竞争格局</strong>：CR3 集中度高的行业龙头溢价明显（比如白酒、家电）</li>
    <li><strong>护城河</strong>：品牌（茅台）、规模（宁德）、网络效应（微信）、转换成本（企业软件）、专利（药企）— 至少占一条</li>
    <li><strong>政策环境</strong>：监管友好或中性，避开政策打压方向</li>
  </ul>
</div>

<div class="eg-dim-card">
  <h4>5. 资金与催化 <span class="eg-weight" style="background:#CC79A7;">权重 15%</span></h4>
  <p class="eg-sub">谁在买、何时兑现</p>
  <ul>
    <li><strong>聪明钱动向</strong>：机构持仓增加、北向资金净流入、分析师上调评级</li>
    <li><strong>筹码结构</strong>：筹码集中度提升、获利盘比例合理</li>
    <li><strong>催化剂日历</strong>：业绩超预期、新产品发布、政策利好、行业拐点 — 买入前要想清楚"什么能驱动股价上涨"</li>
    <li><strong>风险信号</strong>：大股东减持、卖空比例飙升、机构集体下调</li>
  </ul>
</div>

<!-- ====== 五、PM七问 ====== -->
<h3>五、PM 七问终极检查</h3>
<p>在所有指标打分之后，用这七个问题做最终过滤 —— 如果回答不上来，就不买：</p>
<ol class="eg-pmq">
  <li><strong>什么被错误定价了？</strong>（没有变异认知 = 放弃，说"监控项"即可）</li>
  <li><strong>当前价格已经反映了什么？</strong>（市场预期在哪）</li>
  <li><strong>什么能证明我的论点？</strong>（验证路径）</li>
  <li><strong>什么能推翻我的论点？</strong>（证伪条件 + 止损线）</li>
  <li><strong>为什么是现在？</strong>（时机判断，不是"好公司"就行）</li>
  <li><strong>什么会改变仓位/评级？</strong>（加仓/减仓触发条件）</li>
  <li><strong>还缺少什么证据？</strong>（研究盲区）</li>
</ol>

<!-- ====== 六、常见误区 ====== -->
<h3>六、三个常见误区</h3>

<div class="eg-mistake">
  <div class="eg-mistake-num">1</div>
  <div class="eg-mistake-body">
    <strong>只看 PE 便宜就买</strong>：PE 低可能是价值陷阱（周期股顶部、衰退行业），必须结合增速看 PEG。PE 低 + 增速高 = 机会；PE 低 + 增速负 = 陷阱。
  </div>
</div>

<div class="eg-mistake">
  <div class="eg-mistake-num">2</div>
  <div class="eg-mistake-body">
    <strong>好公司 = 好股票</strong>：好公司太贵也是坏股票。2019-2021 的核心资产泡沫就是案例 —— 茅台、海天从 60 倍 PE 跌回 20 倍，公司没变坏，但买入的人亏了一半。
  </div>
</div>

<div class="eg-mistake">
  <div class="eg-mistake-num">3</div>
  <div class="eg-mistake-body">
    <strong>忽视现金流</strong>：利润可以调节（折旧政策、收入确认时点），现金流难造假。经营现金流持续低于净利润的公司要警惕 —— 利润可能是应收账款堆出来的纸面富贵。
  </div>
</div>

<div style="text-align:center;margin-top:20px;padding:12px;background:#F8F9FA;border-radius:6px;font-size:12px;color:#888;">
  判断标准说明书 <br>
  本说明书仅供参考，不构成投资建议
</div>

</div>
"""
