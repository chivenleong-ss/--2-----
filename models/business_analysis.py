"""
Business-health analysis layer — v2.10 六模块完整版.

升级内容（v2.9 → v2.10）：
1. 新增模块六：数据质量与流程效率分析（基于模型1.4）
2. 每模块输出全部5-6个指标（共30+指标），不再只输出2-3个
3. 综合评分权重从5模块调整为6模块
4. 新增分层abs()防线：聚合层严禁abs()，仅清洗层窄范围使用
5. 新增分模块数据提取方法，支持前端异步分块加载
6. 内存安全：所有聚合操作使用深拷贝快照
"""
from __future__ import annotations

import copy
import gc
from dataclasses import dataclass
from typing import Any

import pandas as pd

from utils.helpers import safe_float
from utils.strategic_scope import detect_strategic_scope

DISPLAY_METRIC_CATALOG = {
    1: [
        {"name": "跨区域经营合规", "type": "independent_score", "score_mode": "issue_rate", "formula": "模型1.1：窜区 + 非常规区域未达门槛合并", "score_formula": "正向得分 = (1 - 合规异常项目占比) × 100"},
        {"name": "区域覆盖质量", "type": "module_proxy", "score_mode": "module_proxy", "formula": "模型1.1：深耕区域占比 + 授权城市覆盖合并", "score_formula": "沿用模块一审计综合分（待拆分独立评分）"},
        {"name": "业务结构偏离", "type": "independent_score", "score_mode": "target_score", "formula": "模型1.2：六大板块实际占比 vs 155年度目标", "score_formula": "正向得分 = 业务结构对齐度 × 100"},
        {"name": "EPC转型进度", "type": "independent_score", "score_mode": "target_score", "formula": "模型1.2：EPC项目合同额占比 vs 50%目标", "score_formula": "正向得分 = min(EPC占比 / 50%, 1) × 100"},
        {"name": "战略新兴业务缺口", "type": "independent_score", "score_mode": "composite_score", "formula": "模型1.2：城市更新占比 + 新兴业务占比合并", "score_formula": "综合得分 = 城市更新得分 × 50% + 新兴业务得分 × 50%"},
        {"name": "区域发展偏离", "type": "module_proxy", "score_mode": "module_proxy", "formula": "模型1.2：四大区域实际占比 vs 155区域目标", "score_formula": "沿用模块一审计综合分（当前为模块代理分）"},
    ],
    2: [
        {"name": "客户集中度综合", "type": "independent_score", "score_mode": "composite_score", "formula": "模型1.3 + 3.2：前5大客户集中度 + 行业HHI", "score_formula": "综合得分 = 前5大客户得分 × 60% + HHI得分 × 40%"},
        {"name": "战略客户管理", "type": "independent_score", "score_mode": "target_score", "formula": "模型1.3：战略客户合同额占比", "score_formula": "正向得分 = min(战略客户合同额占比 / 35%, 1) × 100"},
        {"name": "优质客户占比", "type": "independent_score", "score_mode": "target_score", "formula": "模型1.3：优质客户合同额占比", "score_formula": "正向得分 = min(优质客户合同额占比 / 35%, 1) × 100"},
        {"name": "中标转化异常", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型3.1：中标未签约客户占比", "score_formula": "正向得分 = (1 - 中标未签约客户占比) × 100"},
        {"name": "客户活跃度异常", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型3.1/3.3：流失客户 + 僵尸客户 + 优质僵尸客户占比", "score_formula": "正向得分 = (1 - 客户活跃异常占比) × 100"},
        {"name": "新客户结构质量", "type": "independent_score", "score_mode": "composite_score", "formula": "模型3.2：政府/国企新客户占比 + 地产依赖合并", "score_formula": "综合得分 = 政府/国企得分 × 50% + 地产依赖得分 × 50%"},
    ],
    3: [
        {"name": "严禁投标红线集", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：红线项目占比", "score_formula": "正向得分 = (1 - 红线项目占比) × 100"},
        {"name": "限制投标风险", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：限制投标规则合并", "score_formula": "正向得分 = (1 - 限制投标异常率) × 100"},
        {"name": "付款条件校验", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：付款条件规则校验", "score_formula": "正向得分 = (1 - 付款条件异常率) × 100"},
        {"name": "无限责任条款", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：无限责任条款穿透", "score_formula": "正向得分 = (1 - 无限责任条款异常率) × 100"},
        {"name": "放弃优先受偿权", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1 + 2.4：同概念条款合并", "score_formula": "正向得分 = (1 - 放弃优先受偿权异常率) × 100"},
        {"name": "停缓建不利", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：停缓建不利条款", "score_formula": "正向得分 = (1 - 停缓建不利异常率) × 100"},
        {"name": "三证不全", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：三证不全即开工", "score_formula": "正向得分 = (1 - 三证不全异常率) × 100"},
        {"name": "质保金偏高", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：质保金比例 > 5%", "score_formula": "正向得分 = (1 - 质保金偏高异常率) × 100"},
    ],
    4: [
        {"name": "A值底部亏损", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.2：A值底线检测", "score_formula": "正向得分 = (1 - A值底部亏损异常率) × 100"},
        {"name": "效益偏差", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.2：备案A值 vs 实际利润率偏差", "score_formula": "正向得分 = (1 - 效益偏差异常率) × 100"},
        {"name": "施工真实性异常", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.5：停工/退场/停缓建异常占比", "score_formula": "正向得分 = (1 - 停工退场率) × 100"},
        {"name": "签约履约偏差", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.5：签约 > 12个月产值转化率 < 10%", "score_formula": "正向得分 = (1 - 签约履约偏差异常率) × 100"},
    ],
    5: [
        {"name": "现金保证金占用", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：投标保证金 + 履约保证金合并", "score_formula": "正向得分 = (1 - 现金保证金占用异常率) × 100"},
        {"name": "资金逾期回收", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：保证金逾期 + 预收款逾期占比", "score_formula": "正向得分 = (1 - 逾期回收率) × 100"},
        {"name": "联合体超额担保", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：联合体项目履约担保 > 合同额10%", "score_formula": "正向得分 = (1 - 联合体超额担保异常率) × 100"},
        {"name": "资金负流", "type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：负流项目占比", "score_formula": "正向得分 = (1 - 负流项目占比) × 100"},
    ],
    6: [
        {"name": "流程时间倒置", "type": "independent_score", "score_mode": "issue_rate", "formula": "模型1.4：流程合规率", "score_formula": "正向得分 = 流程合规率 × 100"},
        {"name": "利润率规律异常", "type": "independent_score", "score_mode": "issue_rate", "formula": "模型1.4：测算规律性指数", "score_formula": "正向得分 = 测算规律性指数 × 100"},
        {"name": "中标签约金额偏离", "type": "module_proxy", "formula": "模型1.4：中标签约金额偏离", "score_formula": "沿用模块六审计综合分"},
        {"name": "签约逾期", "type": "module_proxy", "formula": "模型1.4：签约逾期规则", "score_formula": "沿用模块六审计综合分"},
        {"name": "凑量嫌疑", "type": "module_proxy", "formula": "模型1.4：短期集中签约", "score_formula": "沿用模块六审计综合分"},
        {"name": "邀请招标比例过高", "type": "module_proxy", "formula": "模型1.4：邀请招标项目数 / 总数 > 70%", "score_formula": "沿用模块六审计综合分"},
    ],
}


@dataclass
class ModuleScore:
    score: float
    metrics: dict
    veto_triggered: bool = False       # 红线乘数是否触发
    data_missing: list = None           # 数据缺失的指标列表

    def __post_init__(self):
        if self.data_missing is None:
            self.data_missing = []


class BusinessHealthAnalyzer:
    """Operating-health analyzer — v2.10 六模块完整版.

    所有阈值、权重从 config/rules.json → business_health 读取。
    代码中禁止硬编码任何边界数字。

    v2.10 底层三大防线：
    1. 红线乘数(Veto): 任一红线指标归零，模块总分直接归零
    2. 平滑插值(Interpolation): 在阈值区间内线性渐变，消灭断崖式阶梯
    3. 数据缺失惩罚: 核心字段缺失时按0分处理，前端标记N/A状态
    """

    # ── v2.10: 每条防线的红线指标定义 ──
    REDLINE_INDICATORS = {
        # 模块三：合同质量 —— 碰了严禁底线，整个模块不该有分
        3: ["风险项目占比", "风险合同额集中度"],
        # 模块四：履约盈利 —— 停工退场是致命伤
        4: ["停工退场率"],
        # 模块五：资金效率 —— 资金负流是致命信号
        5: ["负流项目占比", "逾期回收率"],
        # 模块六：数据质量 —— 数据大面积缺失时信不过其他模块的分数
        6: ["流程合规率", "测算规律性指数"],  # v4.0: 模块六红线改为时间倒置+规律异常
    }
    # 红线触发阈值：指标得分低于此值时视为触碰红线
    REDLINE_THRESHOLD = 0.10  # 0-1 范围内
    # 红线乘数：触碰时模块得分乘以这个因子（0 = 完全归零）
    VETO_MULTIPLIER = 0.0

    # ── v2.10: 平滑插值配置 ──
    # 指标值在 [low, high] 区间内线性插值到 [score_low, score_high]
    INTERPOLATION_RULES = {

        "盈利健康度":  {"low": 0.00, "high": 0.80, "score_low": 0.30, "score_high": 1.00},
        "资金回收率":  {"low": 0.30, "high": 0.60, "score_low": 0.40, "score_high": 1.00},
        "区域渗透率":  {"low": 0.20, "high": 0.80, "score_low": 0.30, "score_high": 1.00},
        "中标转化率":  {"low": 0.25, "high": 0.60, "score_low": 0.40, "score_high": 1.00},
        "数据完整率":  {"low": 0.50, "high": 0.95, "score_low": 0.20, "score_high": 1.00},
    }

    # ── v2.10: 核心字段列表（缺失时触发数据缺失惩罚）──
    CRITICAL_FIELDS = [
        "_contract_amt", "_actual_output", "_collection_amt",
        "_customer_name", "_project_city_norm", "_a_value",
    ]
    """Operating-health analyzer — v2.10 六模块完整版.

    所有阈值、权重从 config/rules.json → business_health 读取。
    代码中禁止硬编码任何边界数字。
    """

    def __init__(self, config: dict = None):
        """从config读取所有业务参数."""
        self._config = config or {}  # v2.10: 保留完整config引用供探针路由使用
        self._strategic_scope = None  # v2.10: 探针路由结果缓存
        cfg = (config or {}).get("business_health", {})

        mw = cfg.get("module_weights", {})
        self.MODULE_WEIGHTS = {
            "region": mw.get("模块一_区域布局", 0.25),
            "customer": mw.get("模块二_客户稳定", 0.20),
            "contract": mw.get("模块三_合同质量", 0.18),
            "performance": mw.get("模块四_履约盈利", 0.15),
            "capital": mw.get("模块五_资金效率", 0.12),
            "data_quality": mw.get("模块六_数据质量", 0.10),
        }

        sb = cfg.get("score_bands", {})
        self.MODULE_STRONG = sb.get("强势区下限", 80)
        self.MODULE_STEADY = sb.get("稳健区下限", 65)

        gsb = cfg.get("global_score_bands", {})
        self.GLOBAL_STRONG = gsb.get("全局强势下限", 75)
        self.GLOBAL_STEADY = gsb.get("全局稳健下限", 60)

        cl = cfg.get("confidence_levels", {})
        self.CONF_HIGH = cl.get("高置信度下限", 80)
        self.CONF_MEDIUM = cl.get("中置信度下限", 50)

        hrt = cfg.get("high_risk_threshold", {})
        self.HIGH_RISK_SCORE = hrt.get("risk_score下限", 6)

        # v3.1: 红线穿透经营总分的约束参数
        tsc = cfg.get("total_score_constraints", {})
        self.REDLINE_MODULE_CAP = tsc.get("红线模块否决封顶", 60)
        self.CONTRACT_VETO_CAP = tsc.get("合同底线穿透封顶", 50)
        self.MIN_MODULE_THRESHOLD = tsc.get("最低模块阈值", 40)
        self.MIN_MODULE_CAP = tsc.get("最低模块封顶", 65)
        self.DUAL_WEAK_THRESHOLD = tsc.get("双弱模块阈值", 55)
        self.DUAL_WEAK_DISCOUNT = tsc.get("双弱模块折扣", 0.85)

        m1 = cfg.get("module_1_region", {})
        self.W1 = {k: m1.get(k, v) for k, v in {
            "区域渗透率": 0.20, "跨区域经营指数_逆向": 0.30, "深耕区域集中度": 0.15,
            "区域合同额强度": 0.15, "业务结构偏离度": 0.10, "EPC转型进度": 0.10}.items()}

        m2 = cfg.get("module_2_customer", {})
        self.W2 = {k: m2.get(k, v) for k, v in {
            "客户稳定性指数": 0.25, "客户产出波动率_逆向": 0.10, "客户集中度风险_逆向": 0.20,
            "中标转化率": 0.15, "新客户质量指数": 0.10, "战略客户产出比": 0.10,
            "客户风险占比_逆向": 0.10}.items()}

        m3 = cfg.get("module_3_contract", {})
        self.W3 = {k: m3.get(k, v) for k, v in {
            "风险项目占比_逆向": 0.30, "风险合同额集中度_逆向": 0.25,
            "付款条件优良率": 0.20, "合同条款不利度_逆向": 0.15, "三证合规率": 0.10}.items()}

        m4 = cfg.get("module_4_performance", {})
        self.W4 = {k: m4.get(k, v) for k, v in {
            "盈利健康度": 0.30,
            "停工退场率_逆向": 0.30,
            "效益偏差率_逆向": 0.20,
            "在施项目活跃度": 0.20}.items()}

        m5 = cfg.get("module_5_capital", {})
        self.W5 = {k: m5.get(k, v) for k, v in {
            "资金占用率_逆向": 0.20, "保证金周转天数_逆向": 0.15, "逾期回收率_逆向": 0.25,
            "预收款缺口率_逆向": 0.15, "负流项目占比_逆向": 0.15, "资金回收率": 0.10}.items()}

        m6 = cfg.get("module_6_data_quality", {})
        self.W6 = {k: m6.get(k, v) for k, v in {
            "数据完整率": 0.25, "流程合规率": 0.25, "中标签约偏差率_逆向": 0.20,
            "测算规律性指数": 0.15, "签约延迟率_逆向": 0.15}.items()}

        self._model_cache = {}

    # ── 关键字段列表（模块六数据完整率计算用）──
    KEY_FIELDS = [
        "项目编码", "项目名称", "申报单位", "项目地址", "项目城市",
        "签约额（元）", "客户名称", "工程类别", "签约时间", "开工时间",
        "一次性经营效益率（%）（A值）", "实际完成产值", "累计收款",
    ]

    MODEL_PROJECT_CODE_CANDIDATES = [
        "项目编码",
        "项目编号",
        "立项编码",
        "财务项目编码",
        "编码",
    ]

    # ═══════════════════════════════════════════════════════════
    # v2.10: 自动探针路由 —— 检测数据归属并挂载对应的155战略规划
    # ═══════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════
    # v2.10 防线一：红线乘数（Veto Multiplier）
    # ═══════════════════════════════════════════════════════════

    def _apply_redline_veto(self, module_id: int, metrics: dict, score: float) -> tuple:
        """检查模块的红线指标是否触发一票否决。

        任一红线指标得分 < REDLINE_THRESHOLD → 模块总分 × VETO_MULTIPLIER。

        Args:
            module_id: 模块编号 1-6
            metrics: {"指标名": 0-1之间的值, ...}
            score: 原始模块得分

        Returns:
            (adjusted_score, veto_triggered)
        """
        redline_keys = self.REDLINE_INDICATORS.get(module_id, [])
        if not redline_keys:
            return score, False

        for key in redline_keys:
            value = metrics.get(key, 0.5)
            if isinstance(value, (int, float)) and value <= self.REDLINE_THRESHOLD:
                return score * self.VETO_MULTIPLIER, True

        return score, False

    # ═══════════════════════════════════════════════════════════
    # v2.10 防线二：平滑插值（Linear Interpolation）
    # ═══════════════════════════════════════════════════════════

    def _score_with_interpolation(self, indicator_name: str, raw_value: float) -> float:
        """对指定指标应用平滑插值，消灭断崖式阶梯。

        在 [low, high] 区间内线性映射到 [score_low, score_high]。
        未配置插值规则的指标保持原值（连续比例天然平滑）。

        Args:
            indicator_name: 指标中文名
            raw_value: 原始 0-1 连续值

        Returns:
            插值后的 0-1 分数
        """
        rule = self.INTERPOLATION_RULES.get(indicator_name, None)
        if rule is None:
            return raw_value  # 无规则 → 保持原值

        v = max(0.0, min(1.0, raw_value))
        lo, hi = rule["low"], rule["high"]
        s_lo, s_hi = rule["score_low"], rule["score_high"]

        if v <= lo:
            return max(0.0, s_lo * (v / lo)) if lo > 0 else s_lo
        elif v >= hi:
            return min(1.0, s_hi + (1.0 - s_hi) * (v - hi) / (1.0 - hi)) if hi < 1.0 else s_hi
        else:
            # 核心插值区间：(lo, hi) → (s_lo, s_hi)
            t = (v - lo) / (hi - lo)
            return s_lo + t * (s_hi - s_lo)

    # ═══════════════════════════════════════════════════════════
    # v2.10 防线三：防御性数据缺失惩罚
    # ═══════════════════════════════════════════════════════════

    def _check_data_missing(self, df: pd.DataFrame) -> list:
        """检测核心字段的数据缺失情况。

        Returns:
            缺失严重的字段名列表（缺失率 > 50% 视为数据不可信）
        """
        missing = []
        for col in self.CRITICAL_FIELDS:
            if col in df.columns:
                miss_rate = df[col].isna().mean()
                if miss_rate > 0.50:
                    missing.append(col)
            else:
                missing.append(col)
        return missing

    def _detect_strategic_scope(self, df: pd.DataFrame) -> dict:
        """自动探针路由：行政架构优先，名称别名仅兜底。"""
        return detect_strategic_scope(df, self._config if hasattr(self, "_config") else {})

    def _compute_strategic_customer_score(self, df: pd.DataFrame) -> float:
        if df is None or df.empty or "客户名称" not in df.columns or "签约额（元）" not in df.columns:
            return 0.0
        try:
            from models.dim1.model_1_3_strategic_customer import _get_strategic_set
        except Exception:
            return 0.0
        strategic_set = _get_strategic_set(2026)
        if not strategic_set:
            return 0.0
        total_amt = df["签约额（元）"].apply(safe_float).sum()
        if total_amt <= 0:
            return 0.0
        strategic_amt = df[df["客户名称"].isin(strategic_set)]["签约额（元）"].apply(safe_float).sum()
        return _safe_percent_score(strategic_amt / total_amt, 0.35)

    def _compute_quality_customer_score(self, df: pd.DataFrame) -> float:
        if df is None or df.empty or "是否优质客户" not in df.columns or "签约额（元）" not in df.columns:
            return 0.0
        total_amt = df["签约额（元）"].apply(safe_float).sum()
        if total_amt <= 0:
            return 0.0
        quality_amt = df[df["是否优质客户"].astype(str).str.strip() == "是"]["签约额（元）"].apply(safe_float).sum()
        return _safe_percent_score(quality_amt / total_amt, 0.35)

    def _compute_epc_progress_score(self, df: pd.DataFrame) -> float:
        if df is None or df.empty or "签约额（元）" not in df.columns:
            return 0.0
        total_amt = df["签约额（元）"].apply(safe_float).sum()
        if total_amt <= 0 or "项目模式类型" not in df.columns:
            return 0.0
        epc_kw = ["EPC", "工程总承包", "设计施工总承包", "设计采购施工"]
        epc_mask = df["项目模式类型"].astype(str).str.contains("|".join(epc_kw), na=False, regex=True)
        epc_amt = df.loc[epc_mask, "签约额（元）"].apply(safe_float).sum()
        return _safe_percent_score(epc_amt / total_amt, 0.50)

    def _compute_emerging_business_score(self, df: pd.DataFrame) -> float:
        if df is None or df.empty or "签约额（元）" not in df.columns:
            return 0.0
        total_amt = df["签约额（元）"].apply(safe_float).sum()
        if total_amt <= 0:
            return 0.0
        combined = pd.Series("", index=df.index)
        for col in ["业务类型", "工程类别", "工程类别（原总公司市场口径）", "项目分类"]:
            if col in df.columns:
                combined = combined + " " + df[col].astype(str)
        urban_mask = pd.Series(False, index=df.index)
        if "是否城市更新" in df.columns:
            urban_mask = df["是否城市更新"].astype(str).str.strip().eq("是")
        urban_kw = ["城市更新", "城市更新与运营", "老旧改造", "城中村", "老旧小区", "既有建筑", "片区运营"]
        urban_mask = urban_mask | combined.str.contains("|".join(urban_kw), na=False, regex=True)
        emerging_kw = ["新兴业务", "建筑工业化", "装配式", "模块化", "MiC", "CF-MiC", "绿色建材", "双碳"]
        emerging_mask = combined.str.contains("|".join(emerging_kw), na=False, regex=True)
        urban_amt = df.loc[urban_mask, "签约额（元）"].apply(safe_float).sum()
        emerging_amt = df.loc[emerging_mask, "签约额（元）"].apply(safe_float).sum()
        urban_score = _safe_percent_score(urban_amt / total_amt, 0.05)
        emerging_score = _safe_percent_score(emerging_amt / total_amt, 0.05)
        return round(urban_score * 0.5 + emerging_score * 0.5, 1)

    def _compute_customer_concentration_score(self, df: pd.DataFrame) -> float:
        if df is None or df.empty or "客户名称" not in df.columns or "签约额（元）" not in df.columns:
            return 0.0
        customer_amt = df.groupby("客户名称")["签约额（元）"].apply(lambda x: x.apply(safe_float).sum()).sort_values(ascending=False)
        total_amt = customer_amt.sum()
        if total_amt <= 0:
            return 0.0
        top5_share = customer_amt.head(5).sum() / total_amt
        top5_score = _reverse_risk_score(max(0.0, (top5_share - 0.60) / 0.40))
        hhi_score = 100.0
        if "客户性质" in df.columns:
            type_amt = df.groupby("客户性质")["签约额（元）"].apply(lambda x: x.apply(safe_float).sum())
            typed_total = type_amt.sum()
            if typed_total > 0:
                hhi = float(((type_amt / typed_total) ** 2).sum())
                hhi_score = _reverse_risk_score(max(0.0, (hhi - 0.50) / 0.50))
        return round(top5_score * 0.6 + hhi_score * 0.4, 1)

    def _compute_new_customer_quality_score(self, df: pd.DataFrame) -> float:
        if df is None or df.empty or "是否首次合作" not in df.columns:
            return 0.0
        new_df = df[df["是否首次合作"].astype(str).str.strip().eq("是")].copy()
        if new_df.empty:
            return 100.0
        gov_score = 100.0
        if "客户性质" in new_df.columns:
            gov_mask = new_df["客户性质"].astype(str).str.contains("政府|国企|事业", na=False, regex=True)
            gov_score = _safe_percent_score(gov_mask.mean(), 0.50)
        re_score = 100.0
        if "是否地产类项目" in new_df.columns:
            re_share = new_df["是否地产类项目"].astype(str).str.strip().eq("是").mean()
            re_score = _reverse_risk_score(max(0.0, (re_share - 0.50) / 0.50))
        return round(gov_score * 0.5 + re_score * 0.5, 1)

    def _fallback_global_scope(self) -> dict:
        """优雅降级：无法识别时使用局级全局基准."""
        strat_cfg = self._config.get("十五五战略规划", {}) if hasattr(self, '_config') else {}
        global_base = strat_cfg.get("局级全局基准", strat_cfg.get("业务结构目标", {}))
        tolerance = strat_cfg.get("偏差容忍度", 0.05)
        sector_targets = {
            k: v for k, v in global_base.items()
            if not k.startswith("_") and isinstance(v, dict)
        }
        return {
            "scope_type": "global",
            "scope_name": "四局全局155基准（自动识别失败，降级默认）",
            "target_dict": sector_targets,
            "matched_unit": None,
            "tolerance": tolerance,
        }

    # ── 板块映射表（类级别，避免重复构建）──
    _SECTOR_KEYWORD_MAP = {
        "房屋建筑": ["房屋建筑", "房建", "住宅", "房屋建筑工程"],
        "基础设施": ["基础设施", "基建", "公路", "市政", "轨道交通", "机场"],
        "专业工程": ["专业工程", "机电", "钢结构", "幕墙", "装饰", "园林"],
        "海外业务": ["海外业务", "海外", "境外"],
        "城市更新与运营": ["城市更新", "城市更新与运营", "运营", "老旧改造", "城中村"],
        "新兴业务": ["新兴业务", "新兴", "建筑工业化", "装配式", "绿色建材", "双碳", "模块化"],
    }

    def _calc_business_structure_deviation(self, df: pd.DataFrame,
                                            target_dict: dict,
                                            tolerance: float = 0.05,
                                            not_applicable: list = None) -> float:
        """按签约年份逐年的板块占比 vs 155当年目标，加权汇总偏离度。

        动态识别数据中的签约年份，对每一年分别：
        1. 汇总该年各板块实际签约额占比
        2. 对照155规划中对应年份的目标占比
        3. 按该年合同额权重计入总偏离度

        Args:
            df: 项目级DataFrame，需包含 _contract_amt、业务板块字段、_sign_year
            target_dict: {"房屋建筑": {"2026": 0.50, "2027": 0.42, ...}, ...}
            tolerance: 容忍度，偏离在容忍度内不扣分
            not_applicable: 不参与考核的板块列表

        Returns:
            0-1 之间的偏离度分数（1 = 完全吻合多年度目标，0 = 极大偏离）
        """
        not_applicable = not_applicable or []
        if df is None or df.empty:
            return 0.5

        # 确定业务板块字段
        biz_col = None
        for col_name in ["工程类别", "业务板块", "业务类型", "板块"]:
            if col_name in df.columns:
                biz_col = col_name
                break
        if not biz_col:
            return self._fallback_biz_dev_from_model(df)

        # 给每个项目打上板块标签
        def _tag_sector(biz_name):
            biz_str = str(biz_name)
            for sector, keywords in self._SECTOR_KEYWORD_MAP.items():
                for kw in keywords:
                    if kw in biz_str:
                        return sector
            return "其他"

        df = df.copy()
        df["_sector"] = df[biz_col].apply(_tag_sector)

        # 按签约年份分组，逐年度计算偏离度
        year_col = "_sign_year" if "_sign_year" in df.columns else None
        if year_col is None or df[year_col].nunique() <= 1:
            # 无年份信息或只有单一年份 → 一次性计算
            return self._calc_single_year_deviation(
                df, target_dict, tolerance, not_applicable, 2026)

        year_scores = []
        year_weights = []
        target_years = set()
        for td in target_dict.values():
            if isinstance(td, dict):
                target_years.update(int(y) for y in td.keys() if y.isdigit())

        for year_val, year_group in df.groupby(year_col):
            year_int = int(year_val) if pd.notna(year_val) else 2026
            # 夹紧到目标年份范围
            if target_years:
                year_int = max(min(target_years), min(year_int, max(target_years)))
            year_contract = year_group["_contract_amt"].sum()
            if year_contract <= 0:
                continue
            score = self._calc_single_year_deviation(
                year_group, target_dict, tolerance, not_applicable, year_int)
            year_scores.append(score)
            year_weights.append(year_contract)

        if not year_scores:
            return self._fallback_biz_dev_from_model(df)

        # 按各年合同额加权平均
        total_weight = sum(year_weights)
        weighted_score = sum(s * w for s, w in zip(year_scores, year_weights)) / total_weight
        return weighted_score

    def _calc_single_year_deviation(self, df, target_dict, tolerance,
                                     not_applicable, year):
        """单年度偏离度计算."""
        total_contract = df["_contract_amt"].sum()
        if total_contract <= 0:
            return 0.5

        biz_amt = df.groupby("_sector")["_contract_amt"].sum()
        actual_ratios = (biz_amt / total_contract).to_dict()

        total_deviation = 0.0
        matched_sectors = 0
        year_str = str(year)

        for sector, target_by_year in target_dict.items():
            if sector in not_applicable:
                continue
            target = target_by_year.get(year_str, None)
            if target is None:
                years = sorted(target_by_year.keys())
                target = target_by_year.get(years[-1], 0.0) if years else 0.0

            actual = actual_ratios.get(sector, 0.0)
            deviation = abs(actual - target)
            effective_dev = max(0.0, deviation - tolerance)
            total_deviation += effective_dev
            matched_sectors += 1

        if matched_sectors == 0:
            return self._fallback_biz_dev_from_model(df)

        normalized = min(1.0, total_deviation / 1.5)
        return 1.0 - normalized

    def _fallback_biz_dev_from_model(self, df) -> float:
        """优雅降级：无法按板块汇总时，用模型1.2的问题标记估算."""
        m12_df = self._get_model_df("1.2")
        if m12_df is None or len(m12_df) == 0:
            return 0.5  # 完全无法计算，返回中等分
        project_codes = set(df["_project_code"].tolist()) if "_project_code" in df.columns else set()
        if not project_codes:
            return 0.5
        m12_filtered = self._filter_model_df_by_project_codes(m12_df, project_codes)
        biz_dev = min(len(m12_filtered) / max(len(project_codes), 1), 1.0)
        return 1.0 - biz_dev

    def run(self, all_results: dict, dmp_df: pd.DataFrame) -> dict:
        """主入口：执行六模块分析，返回完整指标体系."""
        if dmp_df is None or dmp_df.empty:
            return self._empty_result()

        df = self._prepare_dataframe(copy.deepcopy(dmp_df))  # v2.10: 深拷贝隔离
        issue_index = self._build_issue_index(all_results)

        # v2.10: 自动探针路由 —— 检测数据归属并挂载对应的155战略规划基准
        self._strategic_scope = self._detect_strategic_scope(df)

        # 六模块全量计算
        subsidiaries = self._build_scope_table(df, issue_index, "申报单位")
        cities = self._build_scope_table(df, issue_index, "_project_city_norm")
        overview = self._build_overview(df, issue_index)
        trends = self._build_trends(df)
        quarter_trends = self._build_quarter_trends(df, issue_index)
        focus_projects = self._build_focus_projects(df, issue_index)
        recommendations = self._build_recommendations(overview, subsidiaries, cities, focus_projects)

        summary = {
            "total_projects": int(len(df)),
            "total_contract_yi": round(df["_contract_amt"].sum() / 1e8, 2),
            "covered_units": int(subsidiaries["名称"].nunique()) if not subsidiaries.empty else 0,
            "covered_cities": int(cities["名称"].nunique()) if not cities.empty else 0,
            "top_unit": subsidiaries.iloc[0]["名称"] if not subsidiaries.empty else "",
            "top_unit_score": round(float(subsidiaries.iloc[0]["综合得分"]), 1) if not subsidiaries.empty else 0.0,
            "high_risk_project_count": int(sum(1 for v in issue_index.values() if v["risk_score"] >= self.HIGH_RISK_SCORE)),
            "strong_project_count": int((df["_actual_output"] > 0).sum()),
        }

        result = {
            "summary": summary,
            "overview": overview,
            "subsidiaries": subsidiaries,
            "cities": cities,
            "trends": trends,
            "quarter_trends": quarter_trends,
            "focus_projects": focus_projects,
            "recommendations": recommendations,
            "strategic_scope": self._strategic_scope,  # v2.10: 探针路由结果
        }

        # v2.10: 释放快照
        del df
        gc.collect(0)
        return result

    def _empty_result(self) -> dict:
        return {
            "summary": {"total_projects": 0, "total_contract_yi": 0.0},
            "overview": {},
            "subsidiaries": pd.DataFrame(),
            "cities": pd.DataFrame(),
            "trends": [],
            "quarter_trends": [],
            "focus_projects": [],
            "recommendations": [],
        }

    # ── 数据准备 ────────────────────────────────────────────

    def _prepare_dataframe(self, dmp_df: pd.DataFrame) -> pd.DataFrame:
        df = dmp_df.copy()
        df["_project_code"] = df.get("项目编码", "").astype(str).str.strip()
        df["_contract_amt"] = df.get("签约额（元）", 0).apply(safe_float)
        # v2.10: 产值字段保留正负号，严禁abs() —— 材料抵消/退场核销的负向差异必须穿透
        df["_actual_output"] = df.get("实际完成产值", 0).apply(safe_float)
        df["_collection_amt"] = df.get("累计收款", 0).apply(safe_float)
        df["_customer_name"] = df.get("客户名称", "").astype(str).str.strip()
        df["_project_city_norm"] = df.get("项目城市", df.get("项目地址", "")).astype(str).str.strip()
        df["_sign_year"] = df.apply(self._extract_year, axis=1)
        df["_sign_quarter"] = df.apply(self._extract_quarter, axis=1)
        df["_a_value"] = df.get("一次性经营效益率（%）（A值）", 0).apply(safe_float)

        # 模块二需要的附加字段
        df["_is_quality_customer"] = df.get("是否优质客户", "").astype(str).str.strip() == "是"
        df["_customer_type"] = df.get("客户性质", "").astype(str).str.strip()

        # 模块一需要的附加字段
        df["_project_city"] = df.get("项目城市", df.get("项目地址", "")).astype(str).str.strip()

        return df

    def _extract_year(self, row) -> int:
        for col in ("签约时间", "中标时间", "签约报量时间"):
            val = row.get(col)
            if pd.notna(val):
                try:
                    return pd.Timestamp(val).year
                except Exception:
                    continue
        text = str(row.get("合同签订年度", "")).strip()
        for token in text.split():
            if token.isdigit() and len(token) == 4:
                return int(token)
        return 0

    def _extract_quarter(self, row) -> int:
        for col in ("签约时间", "中标时间", "签约报量时间"):
            val = row.get(col)
            if pd.notna(val):
                try:
                    return int(pd.Timestamp(val).quarter)
                except Exception:
                    continue
        return 0

    def _build_issue_index(self, all_results: dict) -> dict:
        index = {}
        for model_id, payload in all_results.items():
            df = payload[0]
            project_col = self._resolve_project_code_column(df)
            if df is None or len(df) == 0 or not project_col:
                continue
            for _, row in df.iterrows():
                code = str(row.get(project_col, "")).strip()
                if not code:
                    continue
                bucket = index.setdefault(code, {
                    "risk_score": 0, "issue_count": 0, "red_count": 0,
                    "yellow_count": 0, "models": set(), "categories": set(),
                })
                severity = str(row.get("严重等级", "")).lower()
                category = str(row.get("问题分类", "")).strip()
                bucket["issue_count"] += 1
                bucket["models"].add(model_id)
                if category:
                    bucket["categories"].add(category)
                if "red" in severity or "严禁" in severity:
                    bucket["red_count"] += 1
                    bucket["risk_score"] += 3
                elif "yellow" in severity or "限制" in severity:
                    bucket["yellow_count"] += 1
                    bucket["risk_score"] += 1
                else:
                    bucket["risk_score"] += 1
        return index

    # ── 范围表（单位/城市）构建 ─────────────────────────────

    def _build_scope_table(self, df: pd.DataFrame, issue_index: dict, scope_col: str) -> pd.DataFrame:
        rows = []
        for scope_name, group in df.groupby(scope_col, dropna=False):
            scope_name = str(scope_name).strip()
            if not scope_name or scope_name == "nan":
                continue
            rows.append(self._analyze_scope(scope_name, group, issue_index))
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values(
            ["综合得分", "签约额（亿元）"], ascending=[False, False]
        ).reset_index(drop=True)

    def _analyze_scope(self, scope_name: str, group: pd.DataFrame, issue_index: dict) -> dict:
        """v2.10: 返回完整的六模块 + 全部30+指标."""
        project_codes = {code for code in group["_project_code"].tolist() if code}
        total_projects = len(group)
        total_contract = group["_contract_amt"].sum()
        customer_count = group["_customer_name"].replace("nan", "").loc[lambda s: s != ""].nunique()

        # ── 六模块全量计算 ──
        # v2.10: 核心字段缺失检测（所有模块共用）
        self._data_missing_flags = self._check_data_missing(group)

        m1 = self._finalize_module_score(1, self._module_1_region(group, project_codes, issue_index, total_contract))
        m2 = self._finalize_module_score(2, self._module_2_customer(group, project_codes, issue_index, total_contract, customer_count))
        m3 = self._finalize_module_score(3, self._module_3_contract(group, project_codes, issue_index, total_contract))
        m4 = self._finalize_module_score(4, self._module_4_performance(group, project_codes, issue_index, total_contract))
        m5 = self._finalize_module_score(5, self._module_5_capital(group, project_codes, issue_index, total_contract))
        m6 = self._finalize_module_score(6, self._module_6_data_quality(group, project_codes, issue_index, total_projects))

        # ── v2.10 六模块加权 ──
        total_score = (
            m1.score * self.MODULE_WEIGHTS["region"]
            + m2.score * self.MODULE_WEIGHTS["customer"]
            + m3.score * self.MODULE_WEIGHTS["contract"]
            + m4.score * self.MODULE_WEIGHTS["performance"]
            + m5.score * self.MODULE_WEIGHTS["capital"]
            + m6.score * self.MODULE_WEIGHTS["data_quality"]
        ) * 100  # 归一化到0-100

        # v3.1: 红线穿透约束——底线不能被平均洗白
        total_score, constraint_reasons = self._apply_total_score_constraints(
            total_score, [m1, m2, m3, m4, m5, m6]
        )

        return {
            # 基础信息
            "名称": scope_name,
            "项目数": int(total_projects),
            "客户数": int(customer_count),
            "签约额（亿元）": round(total_contract / 1e8, 2),
            "综合得分": round(total_score, 1),

            # ── 模块一：区域布局（v4.0 模型驱动）──
            "模块一_得分": round(m1.score * 100, 1),
            "模型驱动评分_1": round(m1.metrics.get("model_driven_score", 0) * 100, 1),
            # v4.0 合并指标（与前端 MODULE_METRIC_NAMES 对齐）
            "跨区域经营合规": round(m1.metrics.get("跨区域经营合规", m1.metrics.get("model_driven_score", 0)), 1),
            "区域覆盖质量": round(m1.metrics.get("model_driven_score", 0) * 100, 1),
            "业务结构偏离": round(m1.metrics.get("业务结构偏离", m1.metrics.get("model_driven_score", 0) * 100), 1),
            "EPC转型进度": round(m1.metrics.get("EPC转型进度", m1.metrics.get("model_driven_score", 0) * 100), 1),
            "战略新兴业务缺口": round(m1.metrics.get("战略新兴业务缺口", m1.metrics.get("model_driven_score", 0) * 100), 1),
            "区域发展偏离": round(m1.metrics.get("model_driven_score", 0) * 100, 1),

            # ── 模块二：客户稳定（v4.0 模型驱动）──
            "模块二_得分": round(m2.score * 100, 1),
            "模型驱动评分_2": round(m2.metrics.get("model_driven_score", 0) * 100, 1),
            "客户集中度综合": round(m2.metrics.get("客户集中度综合", m2.metrics.get("model_driven_score", 0) * 100), 1),
            "战略客户管理": round(m2.metrics.get("战略客户管理", m2.metrics.get("model_driven_score", 0)), 1),
            "优质客户占比": round(m2.metrics.get("优质客户占比", m2.metrics.get("model_driven_score", 0)), 1),
            "中标转化异常": round(m2.metrics.get("model_driven_score", 0) * 100, 1),
            "客户活跃度异常": round(m2.metrics.get("model_driven_score", 0) * 100, 1),
            "新客户结构质量": round(m2.metrics.get("新客户结构质量", m2.metrics.get("model_driven_score", 0) * 100), 1),

            # ── 模块三：合同质量（v4.0 模型驱动）──
            "模块三_得分": round(m3.score * 100, 1),
            "模型驱动评分_3": round(m3.metrics.get("model_driven_score", 0) * 100, 1),
            "严禁投标红线集": round(m3.metrics.get("风险项目占比", 0) * 100, 1),
            "限制投标风险": round(m3.metrics.get("model_driven_score", 0) * 100, 1),
            "付款条件校验": round(m3.metrics.get("model_driven_score", 0) * 100, 1),
            "无限责任条款": round(m3.metrics.get("model_driven_score", 0) * 100, 1),
            "放弃优先受偿权": round(m3.metrics.get("model_driven_score", 0) * 100, 1),
            "停缓建不利": round(m3.metrics.get("model_driven_score", 0) * 100, 1),
            "三证不全": round(m3.metrics.get("model_driven_score", 0) * 100, 1),
            "质保金偏高": round(m3.metrics.get("model_driven_score", 0) * 100, 1),

            # ── 模块四：履约盈利（v4.0 模型驱动）──
            "模块四_得分": round(m4.score * 100, 1),
            "模型驱动评分_4": round(m4.metrics.get("model_driven_score", 0) * 100, 1),
            "A值底部亏损": round(m4.metrics.get("model_driven_score", 0) * 100, 1),
            "效益偏差": round(m4.metrics.get("model_driven_score", 0) * 100, 1),
            "施工真实性异常": round(m4.metrics.get("停工退场率", 0) * 100, 1),
            "签约履约偏差": round(m4.metrics.get("model_driven_score", 0) * 100, 1),

            # ── 模块五：资金效率（v4.0 模型驱动）──
            "模块五_得分": round(m5.score * 100, 1),
            "模型驱动评分_5": round(m5.metrics.get("model_driven_score", 0) * 100, 1),
            "现金保证金占用": round(m5.metrics.get("model_driven_score", 0) * 100, 1),
            "资金逾期回收": round(m5.metrics.get("逾期回收率", 0) * 100, 1),
            "联合体超额担保": round(m5.metrics.get("model_driven_score", 0) * 100, 1),
            "资金负流": round(m5.metrics.get("负流项目占比", 0) * 100, 1),

            # ── 模块六：数据质量（v4.0 模型驱动）──
            "模块六_得分": round(m6.score * 100, 1),
            "模型驱动评分_6": round(m6.metrics.get("model_driven_score", 0) * 100, 1),
            "流程时间倒置": round(m6.metrics.get("流程合规率", m6.score) * 100, 1),
            "利润率规律异常": round(m6.metrics.get("测算规律性指数", 0) * 100, 1),
            "中标签约金额偏离": round(m6.metrics.get("model_driven_score", 0) * 100, 1),
            "签约逾期": round(m6.metrics.get("model_driven_score", 0) * 100, 1),
            "凑量嫌疑": round(m6.metrics.get("model_driven_score", 0) * 100, 1),
            "邀请招标比例过高": round(m6.metrics.get("model_driven_score", 0) * 100, 1),

            # 诊断
            "经营诊断": self._diagnose(total_score, m1, m2, m3, m4, m5, m6),
            # v2.10: 探针路由信息
            "战略规划基准": (self._strategic_scope or {}).get("scope_name", "四局全局155基准"),
            # v2.10: 防线状态
            "红线触发": any(m.veto_triggered for m in [m1, m2, m3, m4, m5, m6]),
            "数据缺失": self._data_missing_flags,
        }

    # ═══════════════════════════════════════════════════════════
    # 模块一：区域布局健康度（基于模型1.1 + 1.2）
    # ═══════════════════════════════════════════════════════════

    def _finalize_module_score(self, module_id: int, ms: ModuleScore) -> ModuleScore:
        """对模块得分应用三道防线（红线乘数 + 数据缺失标记）。

        注意：平滑插值已在各模块方法内部的指标赋值时应用，此处不重复。
        """
        # 防线三：数据缺失检测
        if hasattr(self, '_data_missing_flags'):
            ms.data_missing = self._data_missing_flags

        # 防线一：红线乘数
        ms.score, ms.veto_triggered = self._apply_redline_veto(
            module_id, ms.metrics, ms.score)

        return ms

    # ═══════════════════════════════════════════════════════════
    # v3.1: 红线穿透约束层
    # ═══════════════════════════════════════════════════════════

    def _apply_total_score_constraints(self, total_score: float, modules: list) -> tuple:
        """对经营总分应用底线约束，确保红线模块无法被平均洗白。

        按序取最严约束：
        1. 任一红线模块被否决 → 总分封顶至 REDLINE_MODULE_CAP
        2. 模块三（合同）被否决 → 总分封顶至 CONTRACT_VETO_CAP（更严）
        3. 最低模块 < MIN_MODULE_THRESHOLD → 总分封顶至 MIN_MODULE_CAP
        4. 同时 ≥2 个模块 < DUAL_WEAK_THRESHOLD → 总分乘以 DUAL_WEAK_DISCOUNT

        Returns:
            (约束后得分, 触发原因列表)
        """
        reasons = []
        constrained_score = total_score

        # 检查红线否决
        module_scores = [m.score * 100 for m in modules]
        veto_modules = []
        for i, m in enumerate(modules):
            if m.veto_triggered:
                veto_modules.append(i + 1)

        if 3 in veto_modules:
            # 模块三（合同）被否决——最严
            constrained_score = min(constrained_score, self.CONTRACT_VETO_CAP)
            reasons.append(f"模块三（合同质量）红线否决，总分封顶 {self.CONTRACT_VETO_CAP}")
        elif veto_modules:
            # 其他红线模块被否决
            constrained_score = min(constrained_score, self.REDLINE_MODULE_CAP)
            reasons.append(f"模块{veto_modules}红线否决，总分封顶 {self.REDLINE_MODULE_CAP}")

        # 检查最低模块
        min_module_idx = module_scores.index(min(module_scores))
        min_module_score = module_scores[min_module_idx]
        if min_module_score < self.MIN_MODULE_THRESHOLD:
            constrained_score = min(constrained_score, self.MIN_MODULE_CAP)
            reasons.append(f"最低模块（模块{min_module_idx + 1}）得分 {min_module_score:.1f} < {self.MIN_MODULE_THRESHOLD}，总分封顶 {self.MIN_MODULE_CAP}")

        # 检查双弱模块
        weak_count = sum(1 for s in module_scores if s < self.DUAL_WEAK_THRESHOLD)
        if weak_count >= 2:
            constrained_score = constrained_score * self.DUAL_WEAK_DISCOUNT
            weak_modules = [i + 1 for i, s in enumerate(module_scores) if s < self.DUAL_WEAK_THRESHOLD]
            reasons.append(f"模块{weak_modules}同时低于 {self.DUAL_WEAK_THRESHOLD}，总分乘以 {self.DUAL_WEAK_DISCOUNT}")

        return constrained_score, reasons

    # ═══════════════════════════════════════════════════════════
    # v4.0 模型驱动评分辅助方法
    # ═══════════════════════════════════════════════════════════

    def _gather_module_issues(self, model_ids: list) -> pd.DataFrame:
        """从模型缓存中聚合指定模型的全部issues."""
        frames = []
        for mid in model_ids:
            df = self._get_model_df(mid)
            if df is not None and len(df) > 0:
                frames.append(df)
        if frames:
            return pd.concat(frames, ignore_index=True)
        return pd.DataFrame()

    def _score_from_issues(self, issues: pd.DataFrame, total_projects: int,
                           total_contract: float) -> float:
        """v4.0 归一化扣分制：从issues计算0-1得分.

        校准参数（可在config/rules.json → scoring 中调整）：
          penalty_scale=20: 控制每项目平均扣分的灵敏度
            1 red/100项目→约94分, 1 red/10项目→约40分
            5 yellow/20项目→约75分, 3 red+5 yellow/50项目→约54分
          single_issue_cap=30: 单条issue最大扣分，防止单一超大合同拉到底
        """
        if issues.empty or total_projects == 0:
            return 1.0

        total_penalty = 0.0
        penalty_scale = 20.0   # 每单位每项目平均扣分灵敏度
        single_issue_cap = 30.0

        for _, row in issues.iterrows():
            severity = str(row.get("严重等级", "yellow"))
            amount = safe_float(row.get("签约额（元）", 0))
            amount_ratio = amount / total_contract if total_contract > 0 else 0.0

            if "严禁投标" in severity or severity == "red":
                base, sev_mult = 15, 2.0
            elif "限制投标" in severity:
                base, sev_mult = 8, 1.0
            else:
                base, sev_mult = 5, 1.0

            penalty = base * sev_mult * (1.0 + min(amount_ratio, 1.0))
            total_penalty += min(penalty, single_issue_cap)

        avg_penalty_per_project = total_penalty / total_projects
        score = 1.0 - min(avg_penalty_per_project * penalty_scale / 100.0, 1.0)
        return max(0.0, score)

    def _compute_redline_ratios(self, module_id: int, issues: pd.DataFrame,
                                 total_projects: int, total_contract: float) -> dict:
        """从issues重新推导红线比率（替代旧DMP手算），走_apply_redline_veto管道."""
        red_mask = issues["严重等级"].str.contains("严禁投标|red", na=False)
        red_issues = issues[red_mask]

        red_project_codes = set()
        if "项目编码" in red_issues.columns and not red_issues.empty:
            red_project_codes = set(red_issues["项目编码"].dropna().unique())

        red_contract = 0.0
        if "签约额（元）" in red_issues.columns and not red_issues.empty:
            red_contract = red_issues["签约额（元）"].apply(safe_float).sum()

        def _count_type(pattern, severity_filter=None):
            mask = issues["问题分类"].str.contains(pattern, na=False)
            if severity_filter:
                mask &= issues["严重等级"].str.contains(severity_filter, na=False)
            return mask.sum()

        if module_id == 3:
            return {
                "风险项目占比": min(len(red_project_codes) / max(total_projects, 1), 1.0),
                "风险合同额集中度": min(red_contract / max(total_contract, 1), 1.0),
            }
        elif module_id == 4:
            stop_count = _count_type("停工退场停缓建预警", "red")
            return {"停工退场率": min(stop_count / max(total_projects, 1), 1.0)}
        elif module_id == 5:
            overdue_count = _count_type("保证金逾期|预收款逾期", "red")
            negflow_count = _count_type("资金负流")
            return {
                "逾期回收率": min(overdue_count / max(total_projects, 1), 1.0),
                "负流项目占比": min(negflow_count / max(total_projects, 1), 1.0),
            }
        elif module_id == 6:
            reversal_count = _count_type("倒置|时序|顺序")
            pattern_count = _count_type("规律性异常")
            return {
                "流程合规率": 1.0 - min(reversal_count / max(total_projects, 1), 1.0),
                "测算规律性指数": 1.0 - min(pattern_count / max(total_projects, 1), 1.0),
            }
        return {}

    # ═══════════════════════════════════════════════════════════
    # 模块一：区域布局健康度（v4.0: 模型1.1 + 1.2 驱动）
    # ═══════════════════════════════════════════════════════════

    def _module_1_region(self, group, project_codes, issue_index, total_contract):
        """模块一：区域布局健康度 — 6个合并指标，全部来自模型1.1+1.2."""
        total_projects = len(group)
        issues = self._gather_module_issues(["1.1", "1.2"])

        # 按scope过滤issues（仅保留本group的项目）
        if not issues.empty and "项目编码" in issues.columns:
            issues = issues[issues["项目编码"].astype(str).isin(set(project_codes))]

        score = self._score_from_issues(issues, total_projects, total_contract)

        # 红线比率
        redline = self._compute_redline_ratios(1, issues, total_projects, total_contract)

        compliance_risk = _parse_issue_ratio(
            issues,
            set(project_codes),
            ["窜区", "非常规区域未达门槛"],
        )

        strategic_scope = self._strategic_scope or {}
        target_dict = strategic_scope.get("target_dict", {})
        tolerance = strategic_scope.get("tolerance", 0.05)
        not_applicable = strategic_scope.get("not_applicable") or []

        metrics = {
            "model_driven_score": score,
            "跨区域经营合规": _reverse_risk_score(compliance_risk),
            "业务结构偏离": round(self._calc_business_structure_deviation(group, target_dict, tolerance, not_applicable) * 100, 1),
            "EPC转型进度": self._compute_epc_progress_score(group),
            "战略新兴业务缺口": self._compute_emerging_business_score(group),
        }
        metrics.update(redline)

        return ModuleScore(score=max(0.0, min(1.0, score)), metrics=metrics)

    # ═══════════════════════════════════════════════════════════
    # 模块二：客户资源稳定性（基于模型1.3 + 3.1 + 3.2）
    # ═══════════════════════════════════════════════════════════

    def _module_2_customer(self, group, project_codes, issue_index, total_contract, customer_count):
        """模块二：客户资源稳定性 — v4.0: 模型1.3+3.1+3.2 驱动."""
        total_projects = len(group)
        issues = self._gather_module_issues(["1.3", "3.1", "3.2"])

        if not issues.empty and "项目编码" in issues.columns:
            issues = issues[issues["项目编码"].astype(str).isin(set(project_codes))]
        elif not issues.empty and "客户名称" in issues.columns:
            custs_in_scope = set(group["_customer_name"].dropna().astype(str))
            issues = issues[issues["客户名称"].astype(str).isin(custs_in_scope)]

        score = self._score_from_issues(issues, total_projects, total_contract)
        customer_names = set(group["_customer_name"].dropna().astype(str))
        conversion_risk = _parse_customer_issue_ratio(
            issues,
            customer_names,
            ["中标未签约", "未签约客户"],
        )
        activity_risk = _parse_customer_issue_ratio(
            issues,
            customer_names,
            ["流失客户", "僵尸客户", "优质僵尸客户"],
        )

        strategic_score = self._compute_strategic_customer_score(group)
        quality_score = self._compute_quality_customer_score(group)

        return ModuleScore(
            score=max(0.0, min(1.0, score)),
            metrics={
                "model_driven_score": score,
                "customer_count": customer_count,
                "客户集中度综合": self._compute_customer_concentration_score(group),
                "战略客户管理": strategic_score,
                "优质客户占比": quality_score,
                "中标转化异常": _reverse_risk_score(conversion_risk),
                "客户活跃度异常": _reverse_risk_score(activity_risk),
                "新客户结构质量": self._compute_new_customer_quality_score(group),
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 模块三：合同质量与风险集中度（基于模型2.1 + 2.4）
    # ═══════════════════════════════════════════════════════════

    def _module_3_contract(self, group, project_codes, issue_index, total_contract):
        """模块三：合同质量与风险集中度 — 5个指标."""
        total_projects = len(group)
        issues = self._gather_module_issues(["2.1", "2.4"])

        if not issues.empty and "项目编码" in issues.columns:
            issues = issues[issues["项目编码"].astype(str).isin(set(project_codes))]

        score = self._score_from_issues(issues, total_projects, total_contract)
        redline = self._compute_redline_ratios(3, issues, total_projects, total_contract)

        metrics = {
            "model_driven_score": score,
            "限制投标风险": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["限制投标"])),
            "付款条件校验": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["付款条件", "付款条件标记校验不一致"])),
            "无限责任条款": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["无限责任", "无上限"])),
            "放弃优先受偿条款": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["放弃优先受偿"])),
            "停缓建不利": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["停缓建"])),
            "三证不全": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["三证不全"])),
            "质保金偏高": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["质保金"])),
        }
        metrics.update(redline)

        return ModuleScore(score=max(0.0, min(1.0, score)), metrics=metrics)

    # ═══════════════════════════════════════════════════════════
    # 模块四：履约盈利健康度（基于模型2.2 + 2.5 + 前置过滤）
    # ═══════════════════════════════════════════════════════════

    def _module_4_performance(self, group, project_codes, issue_index, total_contract):
        """模块四：履约盈利健康度 — v4.0: 模型2.2+2.5 驱动."""
        total_projects = len(group)
        issues = self._gather_module_issues(["2.2", "2.5"])

        if not issues.empty and "项目编码" in issues.columns:
            issues = issues[issues["项目编码"].astype(str).isin(set(project_codes))]

        score = self._score_from_issues(issues, total_projects, total_contract)
        redline = self._compute_redline_ratios(4, issues, total_projects, total_contract)

        metrics = {
            "model_driven_score": score,
            "A值底部亏损": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["承接即亏损"])),
            "效益偏差": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["效益偏差"])),
            "签约履约偏差": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["签约履约偏差", "签约"])),
        }
        metrics.update(redline)

        return ModuleScore(score=max(0.0, min(1.0, score)), metrics=metrics)

    # ═══════════════════════════════════════════════════════════
    # 模块五：资金效率与安全性（基于模型2.3）
    # ═══════════════════════════════════════════════════════════

    def _module_5_capital(self, group, project_codes, issue_index, total_contract):
        """模块五：资金效率与安全性 — 5个指标."""
        total_projects = len(group)
        issues = self._gather_module_issues(["2.3"])

        if not issues.empty and "项目编码" in issues.columns:
            issues = issues[issues["项目编码"].astype(str).isin(set(project_codes))]

        score = self._score_from_issues(issues, total_projects, total_contract)
        redline = self._compute_redline_ratios(5, issues, total_projects, total_contract)

        metrics = {
            "model_driven_score": score,
            "现金保证金占用": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["现金投标保证金", "现金履约保证金"])),
            "联合体超额担保": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["联合体超额担保"])),
        }
        metrics.update(redline)

        return ModuleScore(score=max(0.0, min(1.0, score)), metrics=metrics)

    # ═══════════════════════════════════════════════════════════
    # 模块六：数据质量与流程效率（基于模型1.4）—— v2.10 新增
    # ═══════════════════════════════════════════════════════════

    def _module_6_data_quality(self, group, project_codes, issue_index, total_projects):
        """模块六：数据质量与流程效率 — v4.0: 模型1.4 驱动."""
        total = max(total_projects, 1)
        issues = self._gather_module_issues(["1.4"])

        if not issues.empty and "项目编码" in issues.columns:
            issues = issues[issues["项目编码"].astype(str).isin(set(project_codes))]

        score = self._score_from_issues(issues, total, 0)
        redline = self._compute_redline_ratios(6, issues, total, 0)

        process_issue_ratio = _parse_issue_ratio(
            issues,
            set(project_codes),
            ["时间倒置", "招文领取与交标时间倒置", "交标与中标时间倒置", "中标与签约时间倒置", "签约与签约报量时间倒置"],
        )
        metrics = {
            "model_driven_score": score,
            "流程时间倒置": _reverse_risk_score(process_issue_ratio),
            "利润率规律异常": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["利润率规律性异常"])),
            "中标签约金额偏离": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["中标签约金额偏离"])),
            "签约逾期": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["预计签约逾期", "中标后长期未签约"])),
            "凑量嫌疑": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["凑量嫌疑"])),
            "邀请招标比例过高": _reverse_risk_score(_parse_issue_ratio(issues, set(project_codes), ["邀请招标比例过高"])),
        }
        metrics.update(redline)

        return ModuleScore(score=max(0.0, min(1.0, score)), metrics=metrics)

    def _build_overview(self, df: pd.DataFrame, issue_index: dict) -> dict:
        total_contract = df["_contract_amt"].sum()
        project_codes = set(df["_project_code"].tolist())
        customer_count = df["_customer_name"].replace("nan", "").loc[lambda s: s != ""].nunique()

        self._data_missing_flags = self._check_data_missing(df)
        m1 = self._finalize_module_score(1, self._module_1_region(df, project_codes, issue_index, total_contract))
        m2 = self._finalize_module_score(2, self._module_2_customer(df, project_codes, issue_index, total_contract, customer_count))
        m3 = self._finalize_module_score(3, self._module_3_contract(df, project_codes, issue_index, total_contract))
        m4 = self._finalize_module_score(4, self._module_4_performance(df, project_codes, issue_index, total_contract))
        m5 = self._finalize_module_score(5, self._module_5_capital(df, project_codes, issue_index, total_contract))
        m6 = self._finalize_module_score(6, self._module_6_data_quality(df, project_codes, issue_index, len(df)))

        total_score = (
            m1.score * self.MODULE_WEIGHTS["region"]
            + m2.score * self.MODULE_WEIGHTS["customer"]
            + m3.score * self.MODULE_WEIGHTS["contract"]
            + m4.score * self.MODULE_WEIGHTS["performance"]
            + m5.score * self.MODULE_WEIGHTS["capital"]
            + m6.score * self.MODULE_WEIGHTS["data_quality"]
        ) * 100

        return {
            "total_score": round(total_score, 1),
            "module_scores": {
                "模块一_区域布局": round(m1.score * 100, 1),
                "模块二_客户稳定": round(m2.score * 100, 1),
                "模块三_合同质量": round(m3.score * 100, 1),
                "模块四_履约盈利": round(m4.score * 100, 1),
                "模块五_资金效率": round(m5.score * 100, 1),
                "模块六_数据质量": round(m6.score * 100, 1),
            },
            # v4.0 模块驱动评分（安全读取）
            "模型驱动评分": {
                "模块一": round(m1.metrics.get("model_driven_score", m1.score) * 100, 1),
                "模块二": round(m2.metrics.get("model_driven_score", m2.score) * 100, 1),
                "模块三": round(m3.metrics.get("model_driven_score", m3.score) * 100, 1),
                "模块四": round(m4.metrics.get("model_driven_score", m4.score) * 100, 1),
                "模块五": round(m5.metrics.get("model_driven_score", m5.score) * 100, 1),
                "模块六": round(m6.metrics.get("model_driven_score", m6.score) * 100, 1),
            },
            # 模块一：独立/半独立展示指标
            "跨区域经营合规": round(m1.metrics.get("跨区域经营合规", m1.metrics.get("model_driven_score", 0) * 100), 1),
            "区域覆盖质量": round(m1.metrics.get("model_driven_score", 0) * 100, 1),
            "业务结构偏离": round(m1.metrics.get("业务结构偏离", m1.metrics.get("model_driven_score", 0) * 100), 1),
            "EPC转型进度": round(m1.metrics.get("EPC转型进度", m1.metrics.get("model_driven_score", 0) * 100), 1),
            "战略新兴业务缺口": round(m1.metrics.get("战略新兴业务缺口", m1.metrics.get("model_driven_score", 0) * 100), 1),
            "区域发展偏离": round(m1.metrics.get("model_driven_score", 0) * 100, 1),
            # 模块二：独立/源模型拆分指标
            "客户集中度综合": round(m2.metrics.get("客户集中度综合", m2.metrics.get("model_driven_score", 0) * 100), 1),
            "战略客户管理": round(m2.metrics.get("战略客户管理", m2.metrics.get("model_driven_score", 0) * 100), 1),
            "优质客户占比": round(m2.metrics.get("优质客户占比", m2.metrics.get("model_driven_score", 0) * 100), 1),
            "中标转化异常": round(m2.metrics.get("中标转化异常", m2.metrics.get("model_driven_score", 0) * 100), 1),
            "客户活跃度异常": round(m2.metrics.get("客户活跃度异常", m2.metrics.get("model_driven_score", 0) * 100), 1),
            "新客户结构质量": round(m2.metrics.get("新客户结构质量", m2.metrics.get("model_driven_score", 0) * 100), 1),
            # 综合
            "score_band": self._score_band_counts(df, issue_index),
            "strategic_scope_name": (self._strategic_scope or {}).get("scope_name", "四局全局155基准"),
        }

    def _score_band_counts(self, df: pd.DataFrame, issue_index: dict) -> dict:
        buckets = {"强势区": 0, "稳健区": 0, "承压区": 0}
        scope = self._build_scope_table(df, issue_index, "_project_code")
        if scope.empty:
            return buckets
        for score in scope["综合得分"].tolist():
            if score >= self.GLOBAL_STRONG:
                buckets["强势区"] += 1
            elif score >= self.GLOBAL_STEADY:
                buckets["稳健区"] += 1
            else:
                buckets["承压区"] += 1
        return buckets

    # ── 趋势 / 重点项目 / 建议 ──────────────────────────────

    def _build_trends(self, df: pd.DataFrame) -> list[dict]:
        """v3.2: 年度趋势 + 同比增速 + 异常标记."""
        if "_sign_year" not in df.columns:
            return []
        valid = df[df["_sign_year"] > 0].copy()
        if valid.empty:
            return []
        rows = []
        for year, group in valid.groupby("_sign_year"):
            contract = group["_contract_amt"].sum()
            collection = group["_collection_amt"].sum()
            rows.append({
                "year": int(year),
                "project_count": int(len(group)),
                "contract_yi": round(contract / 1e8, 2),
                "collection_rate": round((collection / contract * 100) if contract > 0 else 0.0, 1),
            })
        rows = sorted(rows, key=lambda x: x["year"])

        # v3.2: 计算同比增速与异常标记
        for i, row in enumerate(rows):
            if i == 0:
                row["contract_growth"] = None
                row["conversion_change"] = None
                row["collection_change"] = None
                row["anomaly"] = "基准年（无同比数据）"
                continue

            prev = rows[i - 1]
            # 合同额增速
            if prev["contract_yi"] > 0:
                row["contract_growth"] = round(
                    (row["contract_yi"] - prev["contract_yi"]) / prev["contract_yi"] * 100, 1)
            else:
                row["contract_growth"] = None

            # 收款率变化（百分点）
            row["collection_change"] = round(
                row["collection_rate"] - prev["collection_rate"], 1)

            # 异常判断
            anomalies = []
            if row.get("contract_growth") is not None:
                if row["contract_growth"] < -30:
                    anomalies.append("⚠️ 签约额骤降 >30%")
                elif row["contract_growth"] > 80:
                    anomalies.append("⚠️ 签约额暴涨 >80%（可能透支未来产能）")
            if row["collection_change"] < -10:
                anomalies.append("🔴 收款率骤降")
            row["anomaly"] = " | ".join(anomalies) if anomalies else "稳定"

        return rows

    def _build_quarter_trends(self, df: pd.DataFrame, issue_index: dict) -> list[dict]:
        if "_sign_year" not in df.columns or "_sign_quarter" not in df.columns:
            return []
        valid = df[(df["_sign_year"] > 0) & (df["_sign_quarter"] > 0)].copy()
        if valid.empty:
            return []

        latest_year = int(valid["_sign_year"].max())
        current = valid[valid["_sign_year"] == latest_year].copy()
        if current.empty:
            return []

        rows = []
        for quarter in range(1, 5):
            group = current[current["_sign_quarter"] == quarter]
            contract = group["_contract_amt"].sum() if not group.empty else 0.0
            output = group["_actual_output"].sum() if not group.empty else 0.0
            collection = group["_collection_amt"].sum() if not group.empty else 0.0
            score = round(self._build_overview(group, issue_index)["total_score"], 1) if not group.empty else 0.0
            rows.append({
                "year": latest_year,
                "quarter": f"Q{quarter}",
                "quarter_index": quarter,
                "project_count": int(len(group)),
                "contract_yi": round(contract / 1e8, 2),
                "total_score": score,
                "collection_rate": round((collection / contract * 100) if contract > 0 else 0.0, 1),
            })
        return rows

    def _build_focus_projects(self, df: pd.DataFrame, issue_index: dict) -> list[dict]:
        rows = []
        for _, row in df.iterrows():
            code = row["_project_code"]
            if not code:
                continue
            issue = issue_index.get(code)
            if not issue:
                continue
            rows.append({
                "project_code": code,
                "project_name": str(row.get("项目名称", "")),
                "unit": str(row.get("申报单位", "")),
                "city": str(row.get("_project_city_norm", "")),
                "contract_yi": round(safe_float(row["_contract_amt"]) / 1e8, 2),
                "risk_score": int(issue["risk_score"]),
                "red_count": int(issue["red_count"]),
                "yellow_count": int(issue["yellow_count"]),
                "models": " / ".join(sorted(issue["models"]))[:60],
                "categories": "、".join(sorted(issue["categories"])[:3]),
            })
        rows.sort(key=lambda x: (-x["risk_score"], -x["contract_yi"], -x["red_count"]))
        return rows

    def _build_recommendations(self, overview, subsidiaries, cities, focus_projects) -> list[dict]:
        module_scores = overview.get("module_scores", {})
        weak_module = min(module_scores, key=module_scores.get) if module_scores else ""
        recs = []
        if weak_module:
            recs.append({
                "title": f"{weak_module}优先修复",
                "detail": f"当前为六模块最低分，建议作为本轮经营提升的首要抓手。",
                "level": "high",
            })
        if not subsidiaries.empty:
            weakest_units = subsidiaries.sort_values("综合得分").head(3)["名称"].tolist()
            recs.append({
                "title": "二级单位分层治理",
                "detail": f"建议优先跟踪低分单位：{'、'.join(weakest_units)}。",
                "level": "medium",
            })
        if not cities.empty:
            risky_cities = cities.sort_values("综合得分").head(3)["名称"].tolist()
            recs.append({
                "title": "城市布局优化",
                "detail": f"建议对承压城市开展专项诊断：{'、'.join(risky_cities)}。",
                "level": "medium",
            })
        if focus_projects:
            names = [item["project_name"] for item in focus_projects[:3] if item["project_name"]]
            recs.append({
                "title": "重点项目盯控",
                "detail": f"高风险高体量项目建议一案一策：{'、'.join(names)}。",
                "level": "high",
            })
        return recs

    # ── 诊断 ────────────────────────────────────────────────

    def _diagnose(self, total_score: float, *modules) -> str:
        """v2.10: 六模块诊断."""
        names = ["区域", "客户", "合同", "履约", "资金", "数据"]
        score_map = {names[i]: m.score * 100 for i, m in enumerate(modules)}
        weakest = min(score_map, key=score_map.get)
        weakest_score = score_map[weakest]
        if total_score >= self.GLOBAL_STRONG:
            return f"整体经营健康（{total_score:.1f}分），继续巩固{weakest}({weakest_score:.0f}分)短板即可。"
        if total_score >= self.GLOBAL_STEADY:
            return f"整体可控（{total_score:.1f}分），但{weakest}({weakest_score:.0f}分)模块偏弱，建议专项提升。"
        return f"{weakest}模块({weakest_score:.0f}分)拖累明显，建议列入重点经营诊断清单。"

    # ── 工具方法 ─────────────────────────────────────────────

    def _resolve_project_code_column(self, df: pd.DataFrame) -> str:
        if df is None or len(df) == 0:
            return ""
        for col in self.MODEL_PROJECT_CODE_CANDIDATES:
            if col in df.columns:
                return col
        for col in df.columns:
            text = str(col)
            if "项目" in text and "编码" in text:
                return col
        return ""

    def _filter_model_df_by_project_codes(self, df: pd.DataFrame, project_codes: set[str]) -> pd.DataFrame:
        if df is None or len(df) == 0 or not project_codes:
            return df.iloc[0:0] if hasattr(df, "iloc") else pd.DataFrame()
        project_col = self._resolve_project_code_column(df)
        if not project_col:
            return df.iloc[0:0]
        return df[df[project_col].astype(str).str.strip().isin(project_codes)]

    def _get_model_df(self, model_id: str) -> pd.DataFrame:
        """获取指定模型的输出DataFrame（兼容缓存格式）."""
        if not hasattr(self, '_model_cache'):
            self._model_cache = {}
        if model_id not in self._model_cache:
            # 这需要从外部传入，这里返回空DataFrame
            return pd.DataFrame()
        return self._model_cache.get(model_id, pd.DataFrame())

    def _set_model_cache(self, all_results: dict):
        """设置模型输出缓存."""
        self._model_cache = {}
        for mid, payload in all_results.items():
            df = payload[0] if isinstance(payload, (tuple, list)) and len(payload) > 0 else pd.DataFrame()
            if df is not None and len(df) > 0:
                self._model_cache[mid] = df

    def _issue_amount_for_projects(self, group, project_codes, issue_index, model_ids) -> float:
        selected = {code for code in project_codes
                    if issue_index.get(code, {}).get("models", set()) & model_ids}
        return group[group["_project_code"].isin(selected)]["_contract_amt"].sum()


def _metric_catalog_for_module(module_id: int) -> list[dict]:
    return copy.deepcopy(DISPLAY_METRIC_CATALOG.get(module_id, []))


def _normalize_ratio(value) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _safe_percent_score(actual_ratio: float, target_ratio: float) -> float:
    actual = _normalize_ratio(actual_ratio)
    target = max(float(target_ratio or 0), 0.0001)
    return round(min(actual / target, 1.0) * 100, 1)


def _reverse_risk_score(risk_ratio: float) -> float:
    return round((1.0 - _normalize_ratio(risk_ratio)) * 100, 1)


METRIC_CARD_OVERRIDES = {
    "限制投标风险": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "付款条件校验": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "无限责任条款": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "放弃优先受偿条款": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "停缓建不利": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "三证不全": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "质保金偏高": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "A值底部亏损": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "效益偏差": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "签约履约偏差": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "现金保证金占用": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "联合体超额担保": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "中标签约金额偏离": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "签约逾期": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "凑量嫌疑": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    "邀请招标比例过高": {"metric_type": "risk_rate", "score_mode": "issue_rate"},
}


MODULE_CARD_INDEX_OVERRIDES = {
    3: {i: {"metric_type": "risk_rate", "score_mode": "issue_rate"} for i in range(8)},
    4: {i: {"metric_type": "risk_rate", "score_mode": "issue_rate"} for i in range(4)},
    5: {i: {"metric_type": "risk_rate", "score_mode": "issue_rate"} for i in range(4)},
    6: {
        0: {"metric_type": "independent_score", "score_mode": "issue_rate"},
        1: {"metric_type": "independent_score", "score_mode": "issue_rate"},
        2: {"metric_type": "risk_rate", "score_mode": "issue_rate"},
        3: {"metric_type": "risk_rate", "score_mode": "issue_rate"},
        4: {"metric_type": "risk_rate", "score_mode": "issue_rate"},
        5: {"metric_type": "risk_rate", "score_mode": "issue_rate"},
    },
}

MODULE_CARD_DEFINITIONS = {
    3: [
        {"name": "严禁投标红线集", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：红线项目占比", "score_formula": "正向得分 = (1 - 红线项目占比) × 100"},
        {"name": "限制投标风险", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：限制投标规则合并", "score_formula": "正向得分 = (1 - 限制投标异常率) × 100"},
        {"name": "付款条件校验", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：付款条件规则校验", "score_formula": "正向得分 = (1 - 付款条件异常率) × 100"},
        {"name": "无限责任条款", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：无限责任条款穿透", "score_formula": "正向得分 = (1 - 无限责任条款异常率) × 100"},
        {"name": "放弃优先受偿权", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1 + 2.4：同概念条款合并", "score_formula": "正向得分 = (1 - 放弃优先受偿权异常率) × 100"},
        {"name": "停缓建不利", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：停缓建不利条款", "score_formula": "正向得分 = (1 - 停缓建不利异常率) × 100"},
        {"name": "三证不全", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：三证不全即开工", "score_formula": "正向得分 = (1 - 三证不全异常率) × 100"},
        {"name": "质保金偏高", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：质保金比例 > 5%", "score_formula": "正向得分 = (1 - 质保金偏高异常率) × 100"},
    ],
    4: [
        {"name": "A值底部亏损", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.2：A值底线检测", "score_formula": "正向得分 = (1 - A值底部亏损异常率) × 100"},
        {"name": "效益偏差", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.2：备案A值 vs 实际利润率偏差", "score_formula": "正向得分 = (1 - 效益偏差异常率) × 100"},
        {"name": "施工真实性异常", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.5：停工/退场/停缓建异常占比", "score_formula": "正向得分 = (1 - 停工退场率) × 100"},
        {"name": "签约履约偏差", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.5：签约 > 12个月产值转化率 < 10%", "score_formula": "正向得分 = (1 - 签约履约偏差异常率) × 100"},
    ],
    5: [
        {"name": "现金保证金占用", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：投标保证金 + 履约保证金合并", "score_formula": "正向得分 = (1 - 现金保证金占用异常率) × 100"},
        {"name": "资金逾期回收", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：保证金逾期 + 预收款逾期占比", "score_formula": "正向得分 = (1 - 逾期回收率) × 100"},
        {"name": "联合体超额担保", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：联合体项目履约担保 > 合同额10%", "score_formula": "正向得分 = (1 - 联合体超额担保异常率) × 100"},
        {"name": "资金负流", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：负流项目占比", "score_formula": "正向得分 = (1 - 负流项目占比) × 100"},
    ],
    6: [
        {"name": "流程时间倒置", "metric_type": "independent_score", "score_mode": "issue_rate", "formula": "模型1.4：流程合规率", "score_formula": "正向得分 = 流程合规率 × 100"},
        {"name": "利润率规律异常", "metric_type": "independent_score", "score_mode": "issue_rate", "formula": "模型1.4：测算规律性指数", "score_formula": "正向得分 = 测算规律性指数 × 100"},
        {"name": "中标签约金额偏离", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：中标签约金额偏离", "score_formula": "正向得分 = (1 - 中标签约金额偏离异常率) × 100"},
        {"name": "签约逾期", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：签约逾期规则", "score_formula": "正向得分 = (1 - 签约逾期异常率) × 100"},
        {"name": "凑量嫌疑", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：短期集中签约", "score_formula": "正向得分 = (1 - 凑量嫌疑异常率) × 100"},
        {"name": "邀请招标比例过高", "metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：邀请招标项目数 / 总数 > 70%", "score_formula": "正向得分 = (1 - 邀请招标比例过高异常率) × 100"},
    ],
}


def _build_metric_cards(module_id: int, overview: dict, module_score: float) -> list[dict]:
    metric_values_map = {
        1: {
            "跨区域经营合规": overview.get("跨区域经营合规", module_score),
            "区域覆盖质量": overview.get("区域覆盖质量", module_score),
            "业务结构偏离": overview.get("业务结构偏离", module_score),
            "EPC转型进度": overview.get("EPC转型进度", module_score),
            "战略新兴业务缺口": overview.get("战略新兴业务缺口", module_score),
            "区域发展偏离": overview.get("区域发展偏离", module_score),
        },
        2: {
            "客户集中度综合": overview.get("客户集中度综合", module_score),
            "战略客户管理": overview.get("战略客户管理", module_score),
            "优质客户占比": overview.get("优质客户占比", module_score),
            "中标转化异常": overview.get("中标转化异常", module_score),
            "客户活跃度异常": overview.get("客户活跃度异常", module_score),
            "新客户结构质量": overview.get("新客户结构质量", module_score),
        },
        3: {
            "严禁投标红线集": overview.get("严禁投标红线集", 0),
            "限制投标风险": overview.get("限制投标风险", module_score),
            "付款条件校验": overview.get("付款条件校验", module_score),
            "无限责任条款": overview.get("无限责任条款", module_score),
            "放弃优先受偿权": overview.get("放弃优先受偿权", module_score),
            "停缓建不利": overview.get("停缓建不利", module_score),
            "三证不全": overview.get("三证不全", module_score),
            "质保金偏高": overview.get("质保金偏高", module_score),
        },
        4: {
            "A值底部亏损": overview.get("A值底部亏损", module_score),
            "效益偏差": overview.get("效益偏差", module_score),
            "施工真实性异常": overview.get("施工真实性异常", 0),
            "签约履约偏差": overview.get("签约履约偏差", module_score),
        },
        5: {
            "现金保证金占用": overview.get("现金保证金占用", module_score),
            "资金逾期回收": overview.get("资金逾期回收", 0),
            "联合体超额担保": overview.get("联合体超额担保", module_score),
            "资金负流": overview.get("资金负流", 0),
        },
        6: {
            "流程时间倒置": overview.get("流程时间倒置", module_score),
            "利润率规律异常": overview.get("利润率规律异常", module_score),
            "中标签约金额偏离": overview.get("中标签约金额偏离", module_score),
            "签约逾期": overview.get("签约逾期", module_score),
            "凑量嫌疑": overview.get("凑量嫌疑", module_score),
            "邀请招标比例过高": overview.get("邀请招标比例过高", module_score),
        },
    }

    cards = []
    values = metric_values_map.get(module_id, {})
    source_items = MODULE_CARD_DEFINITIONS.get(module_id, _metric_catalog_for_module(module_id))
    for index, item in enumerate(source_items):
        override = {}
        override.update(METRIC_CARD_OVERRIDES.get(item["name"], {}))
        override.update(MODULE_CARD_INDEX_OVERRIDES.get(module_id, {}).get(index, {}))
        base_type = item.get("metric_type", item.get("type", "module_proxy"))
        base_mode = item.get("score_mode", base_type)
        metric_type = override.get("metric_type", base_type)
        score_mode = override.get("score_mode", base_mode)
        raw_score = values.get(item["name"], None)
        if raw_score in (None, ""):
            score_value = 0.0 if metric_type == "risk_rate" else float(module_score or 0.0)
        else:
            score_value = float(raw_score or 0.0)
        card = {
            "name": item["name"],
            "metric_type": metric_type,
            "score_mode": score_mode,
            "formula": item["formula"],
            "score_formula": item["score_formula"],
            "module_score": round(module_score, 1),
            "display_score": round(score_value, 1),
        }
        if metric_type == "risk_rate":
            anomaly_rate = _normalize_ratio(score_value / 100.0)
            positive_score = round((1.0 - anomaly_rate) * 100, 1)
            card["anomaly_rate"] = round(anomaly_rate * 100, 1)
            card["display_score"] = positive_score
            card["raw_display_value"] = round(score_value, 1)
        cards.append(card)
    return cards


def _collect_metric_value_from_rows(rows, key: str) -> float:
    values = []
    for row in rows or []:
        try:
            value = row.get(key, None)
        except AttributeError:
            value = None
        if value in (None, ""):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        values.append(numeric)
    if not values:
        return 0.0
    return round(sum(values) / len(values), 1)


def _parse_issue_ratio(issue_df: pd.DataFrame, project_codes: set, patterns: list[str]) -> float:
    if issue_df is None or issue_df.empty or "问题分类" not in issue_df.columns:
        return 0.0
    scoped = issue_df.copy()
    if "项目编码" in scoped.columns and project_codes:
        scoped = scoped[scoped["项目编码"].astype(str).isin(project_codes)]
    regex = "|".join(patterns)
    hit_count = int(scoped["问题分类"].astype(str).str.contains(regex, na=False, regex=True).sum())
    return _normalize_ratio(hit_count / max(len(project_codes), 1))


def _parse_customer_issue_ratio(issue_df: pd.DataFrame, customer_names: set[str], patterns: list[str]) -> float:
    if issue_df is None or issue_df.empty or "问题分类" not in issue_df.columns:
        return 0.0
    scoped = issue_df.copy()
    if "客户名称" in scoped.columns and customer_names:
        scoped = scoped[scoped["客户名称"].astype(str).isin(customer_names)]
    regex = "|".join(patterns)
    hit_count = int(scoped["问题分类"].astype(str).str.contains(regex, na=False, regex=True).sum())
    return _normalize_ratio(hit_count / max(len(customer_names), 1))


FINAL_CARD_FIXUPS = {
    3: {
        "限制投标风险": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：限制投标规则合并", "score_formula": "正向得分 = (1 - 限制投标异常率) × 100"},
        "付款条件校验": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：付款条件规则校验", "score_formula": "正向得分 = (1 - 付款条件异常率) × 100"},
        "无限责任条款": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：无限责任条款穿透", "score_formula": "正向得分 = (1 - 无限责任条款异常率) × 100"},
        "放弃优先受偿权": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1 + 2.4：同概念条款合并", "score_formula": "正向得分 = (1 - 放弃优先受偿权异常率) × 100"},
        "停缓建不利": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：停缓建不利条款", "score_formula": "正向得分 = (1 - 停缓建不利异常率) × 100"},
        "三证不全": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：三证不全即开工", "score_formula": "正向得分 = (1 - 三证不全异常率) × 100"},
        "质保金偏高": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：质保金比例 > 5%", "score_formula": "正向得分 = (1 - 质保金偏高异常率) × 100"},
    },
    4: {
        "A值底部亏损": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.2：A值底线检测", "score_formula": "正向得分 = (1 - A值底部亏损异常率) × 100"},
        "效益偏差": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.2：备案A值 vs 实际利润率偏差", "score_formula": "正向得分 = (1 - 效益偏差异常率) × 100"},
        "签约履约偏差": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.5：签约 > 12个月产值转化率 < 10%", "score_formula": "正向得分 = (1 - 签约履约偏差异常率) × 100"},
    },
    5: {
        "现金保证金占用": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：投标保证金 + 履约保证金合并", "score_formula": "正向得分 = (1 - 现金保证金占用异常率) × 100"},
        "联合体超额担保": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：联合体项目履约担保 > 合同额10%", "score_formula": "正向得分 = (1 - 联合体超额担保异常率) × 100"},
    },
    6: {
        "中标签约金额偏离": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：中标签约金额偏离", "score_formula": "正向得分 = (1 - 中标签约金额偏离异常率) × 100"},
        "签约逾期": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：签约逾期规则", "score_formula": "正向得分 = (1 - 签约逾期异常率) × 100"},
        "凑量嫌疑": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：短期集中签约", "score_formula": "正向得分 = (1 - 凑量嫌疑异常率) × 100"},
        "邀请招标比例过高": {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：邀请招标项目数 / 总数 > 70%", "score_formula": "正向得分 = (1 - 邀请招标比例过高异常率) × 100"},
    },
}


def _finalize_metric_cards(module_id: int, cards: list[dict]) -> list[dict]:
    fixups = FINAL_CARD_FIXUPS.get(module_id, {})
    index_fixups = {
        3: {
            1: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：限制投标规则合并", "score_formula": "正向得分 = (1 - 限制投标异常率) × 100"},
            2: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1：付款条件规则校验", "score_formula": "正向得分 = (1 - 付款条件异常率) × 100"},
            3: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：无限责任条款穿透", "score_formula": "正向得分 = (1 - 无限责任条款异常率) × 100"},
            4: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.1 + 2.4：同概念条款合并", "score_formula": "正向得分 = (1 - 放弃优先受偿权异常率) × 100"},
            5: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：停缓建不利条款", "score_formula": "正向得分 = (1 - 停缓建不利异常率) × 100"},
            6: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：三证不全即开工", "score_formula": "正向得分 = (1 - 三证不全异常率) × 100"},
            7: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.4：质保金比例 > 5%", "score_formula": "正向得分 = (1 - 质保金偏高异常率) × 100"},
        },
        4: {
            0: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.2：A值底线检测", "score_formula": "正向得分 = (1 - A值底部亏损异常率) × 100"},
            1: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.2：备案A值 vs 实际利润率偏差", "score_formula": "正向得分 = (1 - 效益偏差异常率) × 100"},
            3: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.5：签约 > 12个月产值转化率 < 10%", "score_formula": "正向得分 = (1 - 签约履约偏差异常率) × 100"},
        },
        5: {
            0: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：投标保证金 + 履约保证金合并", "score_formula": "正向得分 = (1 - 现金保证金占用异常率) × 100"},
            2: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型2.3：联合体项目履约担保 > 合同额10%", "score_formula": "正向得分 = (1 - 联合体超额担保异常率) × 100"},
        },
        6: {
            2: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：中标签约金额偏离", "score_formula": "正向得分 = (1 - 中标签约金额偏离异常率) × 100"},
            3: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：签约逾期规则", "score_formula": "正向得分 = (1 - 签约逾期异常率) × 100"},
            4: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：短期集中签约", "score_formula": "正向得分 = (1 - 凑量嫌疑异常率) × 100"},
            5: {"metric_type": "risk_rate", "score_mode": "issue_rate", "formula": "模型1.4：邀请招标项目数 / 总数 > 70%", "score_formula": "正向得分 = (1 - 邀请招标比例过高异常率) × 100"},
        },
    }.get(module_id, {})
    if not fixups and not index_fixups:
        return cards
    finalized = []
    for idx, card in enumerate(cards):
        fix = fixups.get(card.get("name")) or index_fixups.get(idx)
        if not fix:
            finalized.append(card)
            continue
        merged = dict(card)
        merged.update(fix)
        score_value = float(merged.get("raw_display_value", merged.get("display_score", 0)) or 0.0)
        anomaly_rate = _normalize_ratio(score_value / 100.0)
        merged["anomaly_rate"] = round(anomaly_rate * 100, 1)
        merged["raw_display_value"] = round(score_value, 1)
        merged["display_score"] = round((1.0 - anomaly_rate) * 100, 1)
        finalized.append(merged)
    return finalized


def _business_module_detail_stable(business_result: dict, module_id: int) -> dict:
    if not business_result or not isinstance(business_result, dict):
        return _empty_module(module_id)

    overview = business_result.get("overview", {})
    if not isinstance(overview, dict):
        overview = {}
    module_scores = overview.get("module_scores", {})
    if not isinstance(module_scores, dict):
        module_scores = {}

    module_names = {
        1: "模块一_区域布局",
        2: "模块二_客户稳定",
        3: "模块三_合同质量",
        4: "模块四_履约盈利",
        5: "模块五_资金效率",
        6: "模块六_数据质量",
    }
    module_score_cols = {
        1: "模块一_得分",
        2: "模块二_得分",
        3: "模块三_得分",
        4: "模块四_得分",
        5: "模块五_得分",
        6: "模块六_得分",
    }
    metric_name_map = {
        1: ["区域渗透率", "跨区域经营指数", "深耕区域集中度", "区域合同额强度", "业务结构偏离度", "EPC转型进度"],
        2: ["客户稳定性指数", "客户产出波动率", "客户集中度风险", "中标转化率", "新客户质量指数", "战略客户产出比"],
        3: ["风险项目占比", "风险合同额集中度", "付款条件优良率", "合同条款不利度", "三证合规率"],
        4: ["盈利健康度", "停工退场率", "效益偏差率", "在施项目活跃度"],
        5: ["资金占用率", "保证金周转天数", "逾期回收率", "预收款缺口率", "负流项目占比"],
        6: ["数据完整率", "流程合规率", "中标签约偏差率", "测算规律性指数", "签约延迟率"],
    }

    def to_rows(data):
        if hasattr(data, "to_dict"):
            return data.fillna("").to_dict(orient="records")
        if isinstance(data, list):
            return data
        return []

    def to_float(value):
        try:
            if value in (None, ""):
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    subsidiary_rows = to_rows(business_result.get("subsidiaries", []))
    city_rows = to_rows(business_result.get("cities", []))

    def overview_value(key):
        raw = overview.get(key, None)
        if isinstance(raw, dict):
            raw = raw.get("value", 0)
        if raw not in (None, ""):
            return to_float(raw)
        values = [to_float(row.get(key)) for row in subsidiary_rows if key in row]
        values = [value for value in values if value != 0]
        if values:
            return round(sum(values) / len(values), 1)
        values = [to_float(row.get(key)) for row in city_rows if key in row]
        values = [value for value in values if value != 0]
        return round(sum(values) / len(values), 1) if values else 0.0

    card_value_map = {}
    for module_metrics in DISPLAY_METRIC_CATALOG.values():
        for item in module_metrics:
            key = item.get("name")
            if key:
                card_value_map[key] = overview_value(key)

    module_key = module_names.get(module_id, "")
    score_col = module_score_cols.get(module_id, "")
    score = to_float(module_scores.get(module_key, 0))
    if score == 0 and score_col:
        values = [to_float(row.get(score_col)) for row in subsidiary_rows if score_col in row]
        values = [value for value in values if value != 0]
        score = round(sum(values) / len(values), 1) if values else 0.0

    def top_rows(rows):
        if not score_col:
            return rows[:5]
        return sorted(rows, key=lambda row: to_float(row.get(score_col, 0)), reverse=True)[:5]

    # v2.10: 提取探针路由信息
    strategic_scope = business_result.get("strategic_scope", {})
    scope_name = (strategic_scope.get("scope_name", "")
                  or overview.get("strategic_scope_name", "")
                  or "四局全局155基准（重新运行后自动识别）")

    metrics = {key: overview_value(key) for key in metric_name_map.get(module_id, [])}
    # v2.10: 防线状态
    any_veto = any(row.get("红线触发") for row in subsidiary_rows[:1] if "红线触发" in row)
    data_missing = []
    for row in subsidiary_rows[:1]:
        dm = row.get("数据缺失", [])
        if dm:
            data_missing = [f for f in dm if f.startswith("_")]
    return {
        "module_id": module_id,
        "module_name": module_key,
        "score": score,
        "scope_name": scope_name,
        "metrics": metrics,
        "metric_cards": _finalize_metric_cards(module_id, _build_metric_cards(module_id, {**overview, **card_value_map}, score)),
        "metric_catalog": _metric_catalog_for_module(module_id),
        "metric_values": list(metrics.values()),
        "veto_triggered": any_veto,
        "data_missing": data_missing,
        "top_subsidiaries": top_rows(subsidiary_rows),
        "top_cities": top_rows(city_rows),
    }


# ── 分模块数据提取（v2.10 新增，供前端异步加载）──

def extract_module_data(business_result: dict, module_id: int) -> dict:
    """从完整business_results中提取单个模块的数据.

    Args:
        business_result: BusinessHealthAnalyzer.run() 的输出
        module_id: 1-6

    Returns:
        {module_name, score, scope_name, metrics: {...}, top_subsidiaries: [...], top_cities: [...]}
    """
    if not business_result or not isinstance(business_result, dict):
        return _empty_module(module_id)

    overview = business_result.get("overview", {})
    # v2.10: 提取探针路由信息
    strategic_scope = business_result.get("strategic_scope", {})
    scope_name = (strategic_scope.get("scope_name", "")
                  or overview.get("strategic_scope_name", "")
                  or "四局全局155基准（缓存数据，重新运行后自动识别）")
    module_scores = overview.get("module_scores", {})
    module_names = {
        1: "模块一_区域布局", 2: "模块二_客户稳定", 3: "模块三_合同质量",
        4: "模块四_履约盈利", 5: "模块五_资金效率", 6: "模块六_数据质量",
    }
    module_key = module_names.get(module_id, "")
    score = module_scores.get(module_key, 0)

    # 从 subsidiaries 中提取模块相关列
    subsidiaries = business_result.get("subsidiaries", [])
    cities = business_result.get("cities", [])

    subs_rows = subsidiaries.to_dict(orient='records') if hasattr(subsidiaries, 'to_dict') else (subsidiaries or [])
    city_rows = cities.to_dict(orient='records') if hasattr(cities, 'to_dict') else (cities or [])

    card_value_map = {}
    for module_metrics in DISPLAY_METRIC_CATALOG.values():
        for item in module_metrics:
            key = item.get("name")
            if not key:
                continue
            raw = overview.get(key, None)
            if isinstance(raw, dict):
                raw = raw.get("value", 0)
            if raw in (None, "", 0):
                raw = _collect_metric_value_from_rows(subs_rows, key) or _collect_metric_value_from_rows(city_rows, key)
            card_value_map[key] = raw

    metric_name_map = {
        1: ["区域渗透率", "跨区域经营指数", "深耕区域集中度", "区域合同额强度", "业务结构偏离度", "EPC转型进度"],
        2: ["客户稳定性指数", "客户产出波动率", "客户集中度风险", "中标转化率", "新客户质量指数", "战略客户产出比"],
        3: ["风险项目占比", "风险合同额集中度", "付款条件优良率", "合同条款不利度", "三证合规率"],
        4: ["盈利健康度", "停工退场率", "效益偏差率", "在施项目活跃度"],
        5: ["资金占用率", "保证金周转天数", "逾期回收率", "预收款缺口率", "负流项目占比"],
        6: ["数据完整率", "流程合规率", "中标签约偏差率", "测算规律性指数", "签约延迟率"],
    }
    metric_cols = metric_name_map.get(module_id, [])

    # 排序并取top
    if hasattr(subsidiaries, 'to_dict'):
        subs_list = subsidiaries.to_dict(orient='records')
        subs_sorted = sorted(subs_list, key=lambda x: float(x.get(score_col, 0)), reverse=True)[:5]
    else:
        subs_sorted = sorted(subsidiaries, key=lambda x: float(x.get(score_col, 0)), reverse=True)[:5]

    if hasattr(cities, 'to_dict'):
        cities_list = cities.to_dict(orient='records')
        cities_sorted = sorted(cities_list, key=lambda x: float(x.get(score_col, 0)), reverse=True)[:5]
    else:
        cities_sorted = sorted(cities, key=lambda x: float(x.get(score_col, 0)), reverse=True)[:5]

    return {
        "module_id": module_id,
        "module_name": module_key,
        "score": score,
        "metrics": {c: overview.get(c, {}).get("value", 0) if isinstance(overview.get(c), dict) else overview.get(c, 0)
                    for c in metric_cols},
        "metric_cards": _finalize_metric_cards(module_id, _build_metric_cards(module_id, {**overview, **card_value_map}, float(score or 0))),
        "metric_catalog": _metric_catalog_for_module(module_id),
        "top_subsidiaries": subs_sorted,
        "top_cities": cities_sorted,
    }


def _df_columns(data):
    """兼容DataFrame和list的列名提取."""
    if hasattr(data, 'columns'):
        return list(data.columns)
    if isinstance(data, list) and len(data) > 0:
        return list(data[0].keys())
    return []


def _empty_module(module_id: int) -> dict:
    return {
        "module_id": module_id,
        "module_name": f"模块{module_id}",
        "score": 0,
        "metrics": {},
        "metric_cards": [],
        "metric_catalog": _metric_catalog_for_module(module_id),
        "top_subsidiaries": [],
        "top_cities": [],
    }
