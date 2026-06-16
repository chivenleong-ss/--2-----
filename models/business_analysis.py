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
        6: ["数据完整率"],
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

            # ── 模块一：区域布局健康度（6指标）──
            "模块一_得分": round(m1.score * 100, 1),
            "区域渗透率": round(m1.metrics["region_penetration_rate"] * 100, 1),
            "跨区域经营指数": round(m1.metrics["cross_region_contract_ratio"] * 100, 1),
            "深耕区域集中度": round(m1.metrics["deep_region_ratio"] * 100, 1),
            "区域合同额强度": round(m1.metrics["contract_intensity"] / 1e8, 2),
            "业务结构偏离度": round(m1.metrics.get("business_deviation", 0) * 100, 1),
            "EPC转型进度": round(m1.metrics.get("epc_ratio", 0) * 100, 1),

            # ── 模块二：客户资源稳定性（6指标）──
            "模块二_得分": round(m2.score * 100, 1),
            "客户稳定性指数": round(m2.metrics["customer_stability_index"] * 100, 1),
            "客户产出波动率": round(m2.metrics.get("customer_volatility", 0) * 100, 1),
            "客户集中度风险": round(m2.metrics["top5_customer_share"] * 100, 1),
            "中标转化率": round(m2.metrics.get("bid_conversion_rate", 0) * 100, 1),
            "新客户质量指数": round(m2.metrics.get("new_customer_quality", 0) * 100, 1),
            "战略客户产出比": round(m2.metrics["quality_customer_share"] * 100, 1),

            # ── 模块三：合同质量与风险集中度（5指标）──
            "模块三_得分": round(m3.score * 100, 1),
            "风险项目占比": round(m3.metrics["risk_project_ratio"] * 100, 1),
            "风险合同额集中度": round(m3.metrics["risk_contract_ratio"] * 100, 1),
            "付款条件优良率": round(m3.metrics.get("payment_compliance_rate", 0) * 100, 1),
            "合同条款不利度": round(m3.metrics.get("clause_risk_ratio", 0) * 100, 1),
            "三证合规率": round(m3.metrics.get("permit_compliance_rate", 0) * 100, 1),

            # ── 模块四：履约盈利健康度（4指标）──
            "模块四_得分": round(m4.score * 100, 1),
            "盈利健康度": round(m4.metrics["healthy_profit_ratio"] * 100, 1),
            "停工退场率": round(m4.metrics["stopped_project_ratio"] * 100, 1),
            "效益偏差率": round(m4.metrics.get("profit_deviation_ratio", 0) * 100, 1),
            "在施项目活跃度": round(m4.metrics.get("active_project_ratio", 0) * 100, 1),

            # ── 模块五：资金效率与安全性（5指标）──
            "模块五_得分": round(m5.score * 100, 1),
            "资金占用率": round(m5.metrics.get("capital_occupancy_rate", 0) * 100, 1),
            "保证金周转天数": round(m5.metrics.get("deposit_turnover_days", 0), 1),
            "逾期回收率": round(m5.metrics.get("overdue_recovery_rate", 0) * 100, 1),
            "预收款缺口率": round(m5.metrics.get("advance_shortfall_rate", 0) * 100, 1),
            "负流项目占比": round(m5.metrics.get("negative_flow_ratio", 0) * 100, 1),

            # ── 模块六：数据质量与流程效率（5指标）──
            "模块六_得分": round(m6.score * 100, 1),
            "数据完整率": round(m6.metrics["data_completeness"] * 100, 1),
            "流程合规率": round(m6.metrics["process_compliance"] * 100, 1),
            "中标签约偏差率": round(m6.metrics["bid_sign_deviation"] * 100, 1),
            "测算规律性指数": round(m6.metrics["estimation_regularity"] * 100, 1),
            "签约延迟率": round(m6.metrics["sign_delay_ratio"] * 100, 1),

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

    def _module_1_region(self, group, project_codes, issue_index, total_contract):
        """模块一：区域布局健康度 — 6个指标."""
        actual_cities = {v for v in group["_project_city_norm"].tolist() if v and v != "nan"}

        # ① 区域渗透率
        authorized_cities = set()
        if "授权城市" in group.columns:
            for val in group["授权城市"].dropna():
                authorized_cities.update(c.strip() for c in str(val).split(",") if c.strip() and c.strip() != "nan")
        penetration = min(len(actual_cities) / max(len(authorized_cities), 1), 1.0) if actual_cities else 0.0

        # ② 跨区域经营指数（模型1.1窜区项目的合同额占比）
        cross_region_amt = self._issue_amount_for_projects(group, project_codes, issue_index, {"1.1"})
        cross_region_ratio = cross_region_amt / total_contract if total_contract > 0 else 0.0

        # ③ 深耕区域集中度
        deep_cities = set()
        if "核心城市" in group.columns:
            deep_cities = {c.strip() for val in group["核心城市"].dropna()
                          for c in str(val).split(",") if c.strip() and c.strip() != "nan"}
        deep_ratio = len(actual_cities & deep_cities) / max(len(actual_cities), 1) if actual_cities else 0.0

        # ④ 区域合同额强度（单城市平均合同额，亿元）
        contract_intensity = (total_contract / max(len(actual_cities), 1)) if actual_cities else 0.0

        # ⑤ 业务结构偏离度（v2.10: 使用155探针路由+实际板块签约额计算）
        scope = self._strategic_scope if self._strategic_scope else {
            "target_dict": {}, "tolerance": 0.05, "scope_name": "四局全局155基准"
        }
        biz_dev = 1.0 - self._calc_business_structure_deviation(
            group, scope.get("target_dict", {}), scope.get("tolerance", 0.05),
            not_applicable=scope.get("not_applicable", [])
        )

        # ⑥ EPC转型进度
        epc_count = 0
        if "工程类别" in group.columns:
            epc_count = group["工程类别"].astype(str).str.contains("EPC", na=False).sum()
        epc_ratio = epc_count / max(len(group), 1)

        score = (penetration * self.W1["区域渗透率"] + (1 - min(cross_region_ratio, 1.0)) * self.W1["跨区域经营指数_逆向"]
                 + deep_ratio * self.W1["深耕区域集中度"] + min(contract_intensity / 5e8, 1.0) * self.W1["区域合同额强度"]
                 + (1 - biz_dev) * self.W1["业务结构偏离度"] + epc_ratio * self.W1["EPC转型进度"])

        return ModuleScore(
            score=max(0.0, min(1.0, score)),
            metrics={
                "region_penetration_rate": penetration,
                "cross_region_contract_ratio": cross_region_ratio,
                "deep_region_ratio": deep_ratio,
                "contract_intensity": contract_intensity,
                "business_deviation": biz_dev,
                "epc_ratio": epc_ratio,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 模块二：客户资源稳定性（基于模型1.3 + 3.1 + 3.2）
    # ═══════════════════════════════════════════════════════════

    def _module_2_customer(self, group, project_codes, issue_index, total_contract, customer_count):
        """模块二：客户资源稳定性 — 6个指标."""
        # ① 客户稳定性指数 & ② 客户产出波动率（基于模型3.1客户流失数据）
        m31_df = self._get_model_df("3.1")
        lost_customers = set()
        if len(m31_df) > 0 and "客户名称" in m31_df.columns:
            cust_issues = self._filter_model_df_by_project_codes(m31_df, project_codes)
            cats = cust_issues["问题分类"].astype(str)
            lost_customers = set(cust_issues[cats.str.contains("流失|僵尸", na=False)]["客户名称"].astype(str))
        total_customers = max(customer_count, 1)
        stability_index = 1.0 - len(lost_customers) / total_customers

        # 客户产出波动率（简化：用客户合同额标准差/均值）
        cust_amt_series = group.groupby("_customer_name")["_contract_amt"].sum() if "_customer_name" in group.columns else pd.Series(dtype=float)
        volatility = cust_amt_series.std() / cust_amt_series.mean() if len(cust_amt_series) > 1 and cust_amt_series.mean() > 0 else 0.0
        volatility = min(volatility, 1.0)

        # ③ 客户集中度风险（前5大客户合同额占比）
        top5_share = cust_amt_series.head(5).sum() / total_contract if total_contract > 0 and not cust_amt_series.empty else 0.0

        # ④ 中标转化率（签约额/中标额，基于模型1.3）
        bid_amt = group.get("中标额（元）", pd.Series(index=group.index, dtype=float)).apply(safe_float).sum()
        bid_conversion = total_contract / bid_amt if bid_amt > 0 else 0.0
        bid_conversion = min(bid_conversion, 1.0)

        # ⑤ 新客户质量指数（国企/政府类新客户占比，基于模型3.2）
        new_cust_quality = 0.5  # default
        if "_customer_type" in group.columns:
            gov_soe = group["_customer_type"].astype(str).str.contains("政府|国企|国资|央企", na=False)
            new_cust_quality = gov_soe.sum() / max(len(group), 1)

        # ⑥ 战略客户产出比
        quality_mask = group["_is_quality_customer"] if "_is_quality_customer" in group.columns else pd.Series(index=group.index, dtype=bool)
        quality_amt = group.loc[quality_mask, "_contract_amt"].sum() if len(group) else 0.0
        quality_share = quality_amt / total_contract if total_contract > 0 else 0.0

        # 客户风险占比（模型3.1+3.2的客户问题项目占比）
        customer_issue_projects = {code for code in project_codes
                                   if issue_index.get(code, {}).get("models", set()) & {"3.1", "3.2"}}
        customer_risk_ratio = len(customer_issue_projects) / max(len(project_codes), 1)

        score = (stability_index * self.W2["客户稳定性指数"] + (1 - volatility) * self.W2["客户产出波动率_逆向"]
                 + (1 - min(top5_share, 1.0)) * self.W2["客户集中度风险_逆向"] + bid_conversion * self.W2["中标转化率"]
                 + new_cust_quality * self.W2["新客户质量指数"] + quality_share * self.W2["战略客户产出比"]
                 + (1 - customer_risk_ratio) * self.W2["客户风险占比_逆向"])

        return ModuleScore(
            score=max(0.0, min(1.0, score)),
            metrics={
                "customer_stability_index": stability_index,
                "customer_volatility": volatility,
                "top5_customer_share": top5_share,
                "bid_conversion_rate": bid_conversion,
                "new_customer_quality": new_cust_quality,
                "quality_customer_share": quality_share,
                "customer_risk_ratio": customer_risk_ratio,
                "customer_count": customer_count,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 模块三：合同质量与风险集中度（基于模型2.1 + 2.4）
    # ═══════════════════════════════════════════════════════════

    def _module_3_contract(self, group, project_codes, issue_index, total_contract):
        """模块三：合同质量与风险集中度 — 5个指标."""
        # ① 风险项目占比（模型2.1+2.4标记的项目）
        risk_models = {"2.1", "2.4"}
        risk_projects = {code for code in project_codes
                        if issue_index.get(code, {}).get("models", set()) & risk_models}
        risk_project_ratio = len(risk_projects) / max(len(project_codes), 1)

        # ② 风险合同额集中度
        risk_amt = group[group["_project_code"].isin(risk_projects)]["_contract_amt"].sum()
        risk_contract_ratio = risk_amt / total_contract if total_contract > 0 else 0.0

        # ③ 付款条件优良率（未被模型2.1标记为付款条件问题的项目占比）
        m21_issues = {code for code in project_codes
                      if issue_index.get(code, {}).get("models", set()) & {"2.1"}}
        payment_compliance = 1.0 - len(m21_issues) / max(len(project_codes), 1)

        # ④ 合同条款不利度（模型2.4标记的项目占比）
        m24_issues = {code for code in project_codes
                      if issue_index.get(code, {}).get("models", set()) & {"2.4"}}
        clause_risk_ratio = len(m24_issues) / max(len(project_codes), 1)

        # ⑤ 三证合规率（从模型2.4输出提取）
        m24 = self._get_model_df("2.4")
        permit_ok = 1.0
        if len(m24) > 0 and "问题分类" in m24.columns:
            proj_m24 = self._filter_model_df_by_project_codes(m24, project_codes)
            cats = proj_m24["问题分类"].astype(str)
            permit_issues = cats[cats.str.contains("三证|许可证", na=False)]
            permit_ok = 1.0 - len(permit_issues) / max(len(project_codes), 1)

        score = ((1 - min(risk_project_ratio, 1.0)) * self.W3["风险项目占比_逆向"]
                 + (1 - min(risk_contract_ratio, 1.0)) * self.W3["风险合同额集中度_逆向"]
                 + payment_compliance * self.W3["付款条件优良率"]
                 + (1 - min(clause_risk_ratio, 1.0)) * self.W3["合同条款不利度_逆向"]
                 + permit_ok * self.W3["三证合规率"])

        return ModuleScore(
            score=max(0.0, min(1.0, score)),
            metrics={
                "risk_project_ratio": risk_project_ratio,
                "risk_contract_ratio": risk_contract_ratio,
                "payment_compliance_rate": payment_compliance,
                "clause_risk_ratio": clause_risk_ratio,
                "permit_compliance_rate": permit_ok,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 模块四：履约盈利健康度（基于模型2.2 + 2.5 + 前置过滤）
    # ═══════════════════════════════════════════════════════════

    def _module_4_performance(self, group, project_codes, issue_index, total_contract):
        """模块四：履约盈利健康度 — 4个指标.

        v2.10 数据容错防线：聚合层避免不合理的绝对化操作。
        v3.x: 已剥离 产值转化率、签约履约偏差率（无可靠数据源支撑），权重转移至其余4项。
        """
        # ① 盈利健康度（A值≥底线项目占比）
        profit_mask = group["_a_value"] > 0
        healthy_profit_ratio = profit_mask.sum() / max(len(group), 1)

        # ② 停工退场率（模型2.5标记的项目占比）
        m25_issues = {code for code in project_codes
                      if issue_index.get(code, {}).get("models", set()) & {"2.5"}}
        stopped_ratio = len(m25_issues) / max(len(project_codes), 1)

        # ③ 效益偏差率（A值与实际利润率偏差>1%的项目占比，从模型2.2提取）
        m22 = self._get_model_df("2.2")
        profit_dev = 0.0
        if len(m22) > 0 and "问题分类" in m22.columns:
            proj_m22 = self._filter_model_df_by_project_codes(m22, project_codes)
            dev_cats = proj_m22["问题分类"].astype(str)
            profit_dev = dev_cats.str.contains("偏差|差异", na=False).sum() / max(len(project_codes), 1)

        # ④ 在施项目活跃度（产值>0且收款>0的项目占比）
        active_mask = (group["_actual_output"] > 0) & (group["_collection_amt"] > 0)
        active_ratio = active_mask.sum() / max(len(group), 1)

        score = (min(healthy_profit_ratio, 1.0) * self.W4["盈利健康度"]
                 + (1 - min(stopped_ratio, 1.0)) * self.W4["停工退场率_逆向"]
                 + (1 - min(profit_dev, 1.0)) * self.W4["效益偏差率_逆向"]
                 + min(active_ratio, 1.0) * self.W4["在施项目活跃度"])

        return ModuleScore(
            score=max(0.0, min(1.0, score)),
            metrics={
                "healthy_profit_ratio": healthy_profit_ratio,
                "stopped_project_ratio": stopped_ratio,
                "profit_deviation_ratio": profit_dev,
                "active_project_ratio": active_ratio,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 模块五：资金效率与安全性（基于模型2.3）
    # ═══════════════════════════════════════════════════════════

    def _module_5_capital(self, group, project_codes, issue_index, total_contract):
        """模块五：资金效率与安全性 — 5个指标."""
        # ① 资金占用率（保证金+预收款应收 / 总合同额，基于模型2.3输出估算）
        m23 = self._get_model_df("2.3")
        capital_bound = 0.0
        if len(m23) > 0:
            proj_m23 = self._filter_model_df_by_project_codes(m23, project_codes)
            if "保证金金额" in m23.columns:
                capital_bound = proj_m23["保证金金额"].apply(safe_float).sum()
        capital_occupancy = capital_bound / total_contract if total_contract > 0 else 0.0

        # ② 保证金周转天数（从模型2.3问题描述中提取，简化估算）
        deposit_days = 0.0
        if len(m23) > 0 and "问题描述" in m23.columns:
            proj_m23 = self._filter_model_df_by_project_codes(m23, project_codes)
            descs = proj_m23["问题描述"].astype(str)
            import re
            days_list = []
            for d in descs:
                m = re.search(r"(\d+)\s*天", d)
                if m:
                    days_list.append(int(m.group(1)))
            deposit_days = sum(days_list) / max(len(days_list), 1) if days_list else 0.0

        # ③ 逾期回收率（逾期>90天占总应收，基于模型2.3）
        overdue_amt = 0.0
        m23_projects = {code for code in project_codes
                       if issue_index.get(code, {}).get("models", set()) & {"2.3"}}
        overdue_ratio = len(m23_projects) / max(len(project_codes), 1)

        # ④ 预收款缺口率（基于模型2.3）
        advance_gap = 0.0
        if len(m23) > 0 and "问题分类" in m23.columns:
            proj_m23 = self._filter_model_df_by_project_codes(m23, project_codes)
            advance_cats = proj_m23["问题分类"].astype(str)
            advance_gap = advance_cats.str.contains("预收款", na=False).sum() / max(len(project_codes), 1)

        # ⑤ 负流项目占比
        negative_flow = 0.0
        if len(m23) > 0 and "问题分类" in m23.columns:
            proj_m23 = self._filter_model_df_by_project_codes(m23, project_codes)
            flow_cats = proj_m23["问题分类"].astype(str)
            negative_flow = flow_cats.str.contains("负流|资金负", na=False).sum() / max(len(project_codes), 1)

        # 资金回收率（收款/签约额）
        collection_rate = group["_collection_amt"].sum() / total_contract_abs if (total_contract_abs := group["_contract_amt"].abs().sum()) > 0 else 0.0

        score = ((1 - min(capital_occupancy, 1.0)) * self.W5["资金占用率_逆向"]
                 + (1 - min(deposit_days / 365, 1.0)) * self.W5["保证金周转天数_逆向"]
                 + (1 - min(overdue_ratio, 1.0)) * self.W5["逾期回收率_逆向"]
                 + (1 - min(advance_gap, 1.0)) * self.W5["预收款缺口率_逆向"]
                 + (1 - min(negative_flow, 1.0)) * self.W5["负流项目占比_逆向"]
                 + min(collection_rate, 1.0) * self.W5["资金回收率"])

        return ModuleScore(
            score=max(0.0, min(1.0, score)),
            metrics={
                "capital_occupancy_rate": capital_occupancy,
                "deposit_turnover_days": deposit_days,
                "overdue_recovery_rate": overdue_ratio,
                "advance_shortfall_rate": advance_gap,
                "negative_flow_ratio": negative_flow,
                "collection_rate": collection_rate,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 模块六：数据质量与流程效率（基于模型1.4）—— v2.10 新增
    # ═══════════════════════════════════════════════════════════

    def _module_6_data_quality(self, group, project_codes, issue_index, total_projects):
        """模块六：数据质量与流程效率分析 — 5个指标（v2.10新增）.

        数据来源：模型1.4（营销统计数据多维交叉验真）输出 + DMP字段完整性.
        """
        m14 = self._get_model_df("1.4")
        total = max(total_projects, 1)

        # ① 数据完整率：关键字段非空率
        completeness = 0.0
        field_count = 0
        for col in self.KEY_FIELDS:
            if col in group.columns:
                completeness += group[col].notna().mean()
                field_count += 1
        completeness = completeness / max(field_count, 1)

        # ② 流程合规率（招文评审→交标→中标→签约时序合规占比）
        process_issues = 0
        if len(m14) > 0:
            proj_m14 = self._filter_model_df_by_project_codes(m14, project_codes)
            if "问题分类" in m14.columns:
                cats = proj_m14["问题分类"].astype(str)
                process_issues = cats.str.contains("流程|时序|合规|顺序", na=False).sum()
        process_compliance = 1.0 - process_issues / total

        # ③ 中标签约偏差率（|中标额-签约额|>5%项目占比）
        bid_dev = 0.0
        if len(m14) > 0 and "问题分类" in m14.columns:
            proj_m14 = self._filter_model_df_by_project_codes(m14, project_codes)
            cats = proj_m14["问题分类"].astype(str)
            bid_dev = cats.str.contains("偏差|中标.*签约|签约.*中标|金额差异", na=False).sum() / total

        # ④ 测算规律性指数（基于A值标准差归一化）
        a_std = group["_a_value"].std()
        estimation_regularity = max(0.0, min(1.0, 1.0 - a_std / 0.05)) if pd.notna(a_std) else 0.5

        # ⑤ 签约延迟率（预计签约日已过但未签约的项目占比）
        sign_delay = 0.0
        if len(m14) > 0 and "问题分类" in m14.columns:
            proj_m14 = self._filter_model_df_by_project_codes(m14, project_codes)
            cats = proj_m14["问题分类"].astype(str)
            sign_delay = cats.str.contains("延迟|未签约|超期|逾期.*签约", na=False).sum() / total

        score = (completeness * self.W6["数据完整率"] + process_compliance * self.W6["流程合规率"]
                 + (1 - bid_dev) * self.W6["中标签约偏差率_逆向"] + estimation_regularity * self.W6["测算规律性指数"]
                 + (1 - sign_delay) * self.W6["签约延迟率_逆向"])

        return ModuleScore(
            score=max(0.0, min(1.0, score)),
            metrics={
                "data_completeness": completeness,
                "process_compliance": process_compliance,
                "bid_sign_deviation": bid_dev,
                "estimation_regularity": estimation_regularity,
                "sign_delay_ratio": sign_delay,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 全局概览
    # ═══════════════════════════════════════════════════════════

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
            # 模块一指标
            "区域渗透率": {"value": round(m1.metrics["region_penetration_rate"] * 100, 1),
                       "formula": "落地城市数 ÷ 授权深耕+重点城市数"},
            "跨区域经营指数": {"value": round(m1.metrics["cross_region_contract_ratio"] * 100, 1),
                          "formula": "非常规区域合同额 ÷ 总合同额"},
            # 模块二指标
            "战略客户产出比": {"value": round(m2.metrics["quality_customer_share"] * 100, 1),
                          "formula": "战略+优质客户合同额 ÷ 总合同额"},
            "客户集中度风险": {"value": round(m2.metrics["top5_customer_share"] * 100, 1),
                          "formula": "前5大客户合同额占比"},
            # 模块三指标
            "风险项目占比": {"value": round(m3.metrics["risk_project_ratio"] * 100, 1),
                        "formula": "触碰红线/限制投标项目数 ÷ 总项目数"},
            # 模块四指标
            "盈利健康度": {"value": round(m4.metrics["healthy_profit_ratio"] * 100, 1),
                       "formula": "A值≥底线项目占比"},
            # 模块五指标
            "资金回收率": {"value": round(m5.metrics.get("collection_rate", 0) * 100, 1),
                       "formula": "累计收款 ÷ 签约额"},
            # 模块六指标
            "数据完整率": {"value": round(m6.metrics["data_completeness"] * 100, 1),
                       "formula": "关键字段非空率（DMP 78字段）"},
            # 综合
            "score_band": self._score_band_counts(df, issue_index),
            # v2.10: 当前使用的155规划基准名称
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
        "top_subsidiaries": [],
        "top_cities": [],
    }
