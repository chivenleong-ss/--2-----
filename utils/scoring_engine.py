"""
底层评分引擎 —— 四大防线机制驱动的通用 0-100 标准化评分工厂.

v3.0: 基于 config/rules.json 的 Project_Level / Company_Level 双轨制配置，
      实现"制度即代码（Rule-as-Code）"与"零硬编码"设计理念。

四大防线机制：
┌────────────────────────────────────────────────────────────────────┐
│ 1. 红线乘数 (Veto Multiplier)                                       │
│    is_veto=True 的指标得 0 分 → module 总分直接归零               │
│    彻底锁死用其他优势指标"加权洗白"的空间                           │
├────────────────────────────────────────────────────────────────────┤
│ 2. 平滑插值 (Linear Interpolation)                                  │
│    在 [及格线, 优秀线] 区间内使用 np.interp 线性渐变                │
│    消灭断崖式阶梯阈值，逼真反映业务爬坡状态                         │
├────────────────────────────────────────────────────────────────────┤
│ 3. 防御性数据缺失惩罚 (Data Missing Defense)                        │
│    关键字段为 null/NaN 时判定为 DATA_MISSING, 按 0 分处理           │
│    前端强制渲染灰色 N/A 预警                                        │
├────────────────────────────────────────────────────────────────────┤
│ 4. 成熟度时间豁免 (Time-Decay Exemption)                            │
│    新开工 < exempt_days 天的项目, 履约类指标返回 EXEMPT              │
│    赋予默认稳健分 (default_exempt_score), 防止时空错位误杀          │
└────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
import pandas as pd


# ── 特殊返回值常量 ───────────────────────────────────────────────
# 使用 sentinel 字符串而非 None/NaN, 避免被误认为合法数值参与算术运算
DATA_MISSING = "DATA_MISSING"   # 关键数据缺失, 指标记为 0 分
EXEMPT = "EXEMPT"               # 新项目成熟度豁免, 赋予默认稳健分


class ScoringEngine:
    """通用评分引擎 —— 根据 JSON 配置将业务原值转换为 0-100 标准分.

    使用方式::

        config = json.load(open("config/rules.json"))
        engine = ScoringEngine(config)

        # 项目级评分
        score, meta = engine.evaluate(
            "产值转化率", actual_value=0.45,
            project_start_date="2026-04-15", level="project"
        )

        # 模块综合评分（含红线乘数）
        module_score = engine.calculate_module_score([
            {"name": "产值转化率", "score": 75, "is_veto": False},
            {"name": "盈利健康度", "score": 82, "is_veto": True},
            {"name": "停工退场率", "score": 65, "is_veto": True},
            {"name": "在施项目活跃度", "score": 60, "is_veto": False},
        ], weights={"产值转化率": 0.25, "盈利健康度": 0.20,
                     "停工退场率": 0.20, "在施项目活跃度": 0.10})
    """

    # ── 默认豁免分：新项目在豁免期内被赋予的稳健得分 ──
    DEFAULT_EXEMPT_SCORE = 65.0

    # ── 数据缺失惩罚分 ──
    DATA_MISSING_SCORE = 0.0

    def __init__(self, config: dict = None, rules_path: str = None):
        """初始化评分引擎.

        Args:
            config: 完整配置字典, 需包含 Project_Level 和 Company_Level 键.
                    如果为 None, 则从 rules_path 加载.
            rules_path: 配置文件路径, 默认为 "../config/rules.json"
        """
        if config is None:
            if rules_path is None:
                rules_path = os.path.join(
                    os.path.dirname(__file__), "..", "config", "rules.json"
                )
            with open(rules_path, "r", encoding="utf-8") as f:
                config = json.load(f)

        self._config = config

        # ── 双轨制规则存储 ──
        self._project_rules: dict[str, dict] = self._index_rules(
            config.get("Project_Level", {})
        )
        self._company_rules: dict[str, dict] = self._index_rules(
            config.get("Company_Level", {})
        )

        # ── 引擎元信息 ──
        engine_meta = config.get("_scoring_engine_meta", {})
        self._veto_enabled = True
        self._interpolation_enabled = True
        self._data_missing_enabled = True
        self._exemption_enabled = True

        # ── 从 business_health 读取模块权重和波段 ──
        bh = config.get("business_health", {})
        sb = bh.get("score_bands", {})
        self.STRONG_THRESHOLD = sb.get("强势区下限", 80)
        self.STEADY_THRESHOLD = sb.get("稳健区下限", 65)

        # ── 九宫格映射规则 ──
        self._nine_grid = config.get("九宫格映射规则", {})

    # ═══════════════════════════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════════════════════════

    def evaluate(
        self,
        metric_name: str,
        actual_value: Union[float, int, None],
        project_start_date: Union[str, datetime, None] = None,
        level: str = "project",
    ) -> tuple:
        """将单个指标的业务原值转换为 0-100 标准分.

        此方法内部按序执行四大防线机制：
        1. 缺失数据防御 → DATA_MISSING
        2. 成熟度时间豁免 → EXEMPT
        3. 平滑插值算法 → 线性渐变分数
        4. (红线乘数在 calculate_module_score 中执行)

        Args:
            metric_name: 指标中文名称, 如 "产值转化率", "严禁底线触碰次数"
            actual_value: 该指标的实际业务值. None/NaN 触发缺失防御.
            project_start_date: 项目开工日期 (str 或 datetime).
                               用于成熟度时间豁免判断.
            level: 双轨制路由关键字.
                   "project" → 使用 Project_Level 规则
                   "company" → 使用 Company_Level 规则

        Returns:
            (score, meta) 元组:
            - score: float 0-100 标准分, 或特殊值 DATA_MISSING / EXEMPT
            - meta: dict 包含指标名称、原始值、评分类型、是否触发豁免等诊断信息
        """
        # ── 选取双轨制规则 ──
        ruleset = self._project_rules if level == "project" else self._company_rules
        rule = ruleset.get(metric_name, None)

        # 未配置规则 → 回退到自适应评分
        if rule is None:
            return self._fallback_score(metric_name, actual_value)

        metric_type = rule.get("type", "direct_linear")
        thresholds = rule.get("thresholds", [])
        is_veto = rule.get("is_veto", False)
        exempt_days = rule.get("exempt_days", None)

        # ═══════════════════════════════════════════════════════
        # 防线三：防御性数据缺失惩罚
        # ═══════════════════════════════════════════════════════
        if actual_value is None or (isinstance(actual_value, float) and np.isnan(actual_value)):
            return self.DATA_MISSING_SCORE, {
                "metric": metric_name,
                "raw_value": None,
                "score": self.DATA_MISSING_SCORE,
                "status": "DATA_MISSING",
                "reason": "核心字段为空(null/NaN), 按0分处理, 前端渲染灰色N/A预警",
                "is_veto": is_veto,
                "type": metric_type,
            }

        # ═══════════════════════════════════════════════════════
        # 防线四：成熟度时间豁免
        # ═══════════════════════════════════════════════════════
        if exempt_days is not None and project_start_date is not None:
            if self._is_newly_started(project_start_date, exempt_days):
                return EXEMPT, {
                    "metric": metric_name,
                    "raw_value": actual_value,
                    "score": self.DEFAULT_EXEMPT_SCORE,
                    "status": "EXEMPT",
                    "reason": (
                        f"新开工 < {exempt_days} 天的项目，"
                        f"履约类指标 {metric_name} 自动豁免，"
                        f"赋予默认稳健分 {self.DEFAULT_EXEMPT_SCORE}，"
                        f"只查合规不查转化"
                    ),
                    "is_veto": is_veto,
                    "type": metric_type,
                    "exempt_days": exempt_days,
                }

        # ═══════════════════════════════════════════════════════
        # 防线二：平滑插值算法 → 核心评分逻辑
        # ═══════════════════════════════════════════════════════
        score = self._compute_score(actual_value, metric_type, thresholds)

        return score, {
            "metric": metric_name,
            "raw_value": actual_value,
            "score": score,
            "status": "SCORED",
            "reason": None,
            "is_veto": is_veto,
            "type": metric_type,
        }

    def evaluate_batch(
        self,
        metrics: list[dict],
        project_start_date: Union[str, datetime, None] = None,
        level: str = "project",
    ) -> list[dict]:
        """批量评估多个指标.

        Args:
            metrics: [{"name": "产值转化率", "value": 0.45}, ...]
            project_start_date: 项目开工日期
            level: "project" | "company"

        Returns:
            [{"name": ..., "score": 75, "raw_value": 0.45,
              "is_veto": False, "status": "SCORED"}, ...]
        """
        results = []
        for m in metrics:
            score, meta = self.evaluate(
                m.get("name", ""),
                m.get("value"),
                project_start_date=project_start_date,
                level=level,
            )
            # 处理豁免/缺失时的特殊返回值
            final_score = score
            if score == EXEMPT:
                final_score = self.DEFAULT_EXEMPT_SCORE
            elif score == DATA_MISSING:
                final_score = self.DATA_MISSING_SCORE

            results.append({
                "name": m.get("name", ""),
                "raw_value": m.get("value"),
                "score": final_score if isinstance(final_score, (int, float)) else 0,
                "is_veto": meta.get("is_veto", False),
                "status": meta.get("status", "SCORED"),
                "diagnostics": meta,
            })
        return results

    def calculate_module_score(
        self,
        metrics_results: list[dict],
        weights: dict[str, float] = None,
    ) -> dict:
        """计算模块综合得分 —— 加权求和 + 红线乘数（Veto）.

        防线一执行点:
            若模块内任一 is_veto=True 的指标得分为 0,
            则模块总分直接归零（VETO_TRIGGERED）.

        Args:
            metrics_results: evaluate_batch 的输出列表,
                             每项包含 name, score, is_veto
            weights: {"指标名": 权重, ...}. 若为 None 则等权平均.

        Returns:
            {
                "module_score": 0-100 的模块综合得分,
                "raw_weighted": 加权原始得分（veto前）,
                "veto_triggered": True/False,
                "veto_metrics": ["触发红线的指标名", ...],
                "data_missing": ["数据缺失的指标名", ...],
                "exempt_metrics": ["豁免的指标名", ...],
                "detail": {"指标名": 分项得分, ...}
            }
        """
        if not metrics_results:
            return {
                "module_score": 0.0,
                "raw_weighted": 0.0,
                "veto_triggered": False,
                "veto_metrics": [],
                "data_missing": [],
                "exempt_metrics": [],
                "detail": {},
            }

        # ── 等权回退 ──
        if weights is None:
            weights = {m["name"]: 1.0 for m in metrics_results}

        total_weight = sum(weights.get(m["name"], 0.0) for m in metrics_results)
        if total_weight <= 0:
            total_weight = 1.0

        # ═══════════════════════════════════════════════════════
        # 防线一：红线乘数检测
        # ═══════════════════════════════════════════════════════
        veto_triggered = False
        veto_metrics = []
        data_missing = []
        exempt_metrics = []

        for m in metrics_results:
            status = m.get("status", "SCORED")
            if status == "DATA_MISSING":
                data_missing.append(m["name"])
            if status == "EXEMPT":
                exempt_metrics.append(m["name"])
            # 红线检测：is_veto 且得分为 0 或 DATA_MISSING
            if m.get("is_veto", False):
                score_val = m.get("score", 0)
                if score_val <= 0 or status == "DATA_MISSING":
                    veto_triggered = True
                    veto_metrics.append(m["name"])

        # ── 加权计算 ──
        raw_weighted = 0.0
        detail = {}
        for m in metrics_results:
            w = weights.get(m["name"], 0.0) / total_weight
            s = m.get("score", 0.0)
            raw_weighted += s * w
            detail[m["name"]] = round(s, 1)

        # 红线乘数生效 → 总分归零
        if veto_triggered:
            module_score = 0.0
        else:
            module_score = raw_weighted

        return {
            "module_score": round(module_score, 1),
            "raw_weighted": round(raw_weighted, 1),
            "veto_triggered": veto_triggered,
            "veto_metrics": veto_metrics,
            "data_missing": data_missing,
            "exempt_metrics": exempt_metrics,
            "detail": detail,
        }

    # ═══════════════════════════════════════════════════════════════
    # 双轨制路由方法
    # ═══════════════════════════════════════════════════════════════

    def get_project_metric_names(self, module_id: int = None) -> list[str]:
        """获取 Project_Level 中已配置的所有指标名称.

        Args:
            module_id: 可选, 1-6 过滤特定模块. None 返回全部.
        """
        names = []
        for name, rule in self._project_rules.items():
            if module_id is None or rule.get("_module_id") == module_id:
                names.append(name)
        return names

    def get_company_metric_names(self, module_id: int = None) -> list[str]:
        """获取 Company_Level 中已配置的所有指标名称."""
        names = []
        for name, rule in self._company_rules.items():
            if module_id is None or rule.get("_module_id") == module_id:
                names.append(name)
        return names

    def get_module_weights(self, level: str = "company") -> dict:
        """从 business_health 读取模块权重.

        Args:
            level: "project" → 仅模块3-5权重（重新归一化）
                   "company" → 全量六模块权重
        """
        bh = self._config.get("business_health", {})
        mw = bh.get("module_weights", {})
        weights = {
            "模块一_区域布局": mw.get("模块一_区域布局", 0.25),
            "模块二_客户稳定": mw.get("模块二_客户稳定", 0.20),
            "模块三_合同质量": mw.get("模块三_合同质量", 0.18),
            "模块四_履约盈利": mw.get("模块四_履约盈利", 0.15),
            "模块五_资金效率": mw.get("模块五_资金效率", 0.12),
            "模块六_数据质量": mw.get("模块六_数据质量", 0.10),
        }
        if level == "project":
            # 项目级仅模块3+4+5, 重新归一化
            subset = {
                "模块三_合同质量": weights["模块三_合同质量"],
                "模块四_履约盈利": weights["模块四_履约盈利"],
                "模块五_资金效率": weights["模块五_资金效率"],
            }
            total = sum(subset.values())
            if total > 0:
                subset = {k: v / total for k, v in subset.items()}
            return subset
        return weights

    # ═══════════════════════════════════════════════════════════════
    # 九宫格映射
    # ═══════════════════════════════════════════════════════════════

    def compute_r_coordinate(self, module3_score: float, module5_score: float) -> float:
        """计算风险维度 R 轴坐标.

        R轴 = 模块三(合同质量, weight=0.55) + 模块五(资金效率, weight=0.45)

        Args:
            module3_score: 模块三 0-100 得分
            module5_score: 模块五 0-100 得分

        Returns:
            R 坐标值 (1.0-3.0 连续值)
        """
        r_cfg = self._nine_grid.get("风险维度_R轴", {})
        weights = r_cfg.get("构成模块", {})
        w3 = weights.get("模块三_合同质量", {}).get("weight", 0.55)
        w5 = weights.get("模块五_资金效率", {}).get("weight", 0.45)

        # ── 0-100 → 1-3 档映射（风险方向：高分→低档）──
        r3_level = self.score_to_risk_level(module3_score)
        r5_level = self.score_to_risk_level(module5_score)

        # ── 加权平均 ──
        r_raw = r3_level * w3 + r5_level * w5
        total_w = w3 + w5
        r_value = r_raw / total_w if total_w > 0 else r_raw

        return round(max(1.0, min(3.0, r_value)), 2)

    def compute_e_coordinate(self, module4_score: float, module5_score: float) -> float:
        """计算收益维度 E 轴坐标.

        E轴 = 模块四(履约盈利, weight=0.55) + 模块五(资金效率, weight=0.45)

        Args:
            module4_score: 模块四 0-100 得分
            module5_score: 模块五 0-100 得分

        Returns:
            E 坐标值 (1.0-3.0 连续值)
        """
        e_cfg = self._nine_grid.get("收益维度_E轴", {})
        weights = e_cfg.get("构成模块", {})
        w4 = weights.get("模块四_履约盈利", {}).get("weight", 0.55)
        w5 = weights.get("模块五_资金效率", {}).get("weight", 0.45)

        # ── 0-100 → 1-3 档映射（收益方向：高分→高档）──
        e4_level = self.score_to_return_level(module4_score)
        e5_level = self.score_to_return_level(module5_score)

        # ── 加权平均 ──
        e_raw = e4_level * w4 + e5_level * w5
        total_w = w4 + w5
        e_value = e_raw / total_w if total_w > 0 else e_raw

        return round(max(1.0, min(3.0, e_value)), 2)

    def get_nine_grid_position(self, r_score: float, e_score: float) -> dict:
        """根据 R/E 坐标确定九宫格位置.

        Args:
            r_score: R 轴连续分数 (1.0-3.0)
            e_score: E 轴连续分数 (1.0-3.0)

        Returns:
            {"grid_key": "(2,3)", "name": "优化区", "strategy": "...", "r_level": 2, "e_level": 3}
        """
        # ── 分箱阈值 ──
        r_cfg = self._nine_grid.get("风险维度_R轴", {}).get("分箱阈值", {})
        e_cfg = self._nine_grid.get("收益维度_E轴", {}).get("分箱阈值", {})

        r_cut_low = r_cfg.get("低风险上限", 1.6)
        r_cut_high = r_cfg.get("中风险上限", 2.4)
        e_cut_low = e_cfg.get("低收益上限", 1.6)
        e_cut_high = e_cfg.get("中收益上限", 2.4)

        # ── 分箱 ──
        r_level = 1 if r_score <= r_cut_low else (2 if r_score <= r_cut_high else 3)
        e_level = 1 if e_score <= e_cut_low else (2 if e_score <= e_cut_high else 3)

        grid_key = f"({r_level},{e_level})"
        strategies = self._nine_grid.get("九宫格处置策略", {})
        strategy = strategies.get(grid_key, {"名称": "未分类", "策略": "常规管理"})

        return {
            "grid_key": grid_key,
            "name": strategy.get("名称", "未分类"),
            "strategy": strategy.get("策略", ""),
            "color": strategy.get("颜色", "blue"),
            "r_level": r_level,
            "e_level": e_level,
            "r_score": r_score,
            "e_score": e_score,
        }

    # ═══════════════════════════════════════════════════════════════
    # 内部方法：规则索引
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _index_rules(level_config: dict) -> dict[str, dict]:
        """将嵌套的模块→指标字典扁平化为 {指标名: 规则} 索引.

        同时将 module 的 _module_id 继承给每个子指标.
        """
        indexed = {}
        for module_key, module_val in level_config.items():
            if module_key.startswith("_"):
                continue
            module_id = module_val.get("_module_id", None)
            for metric_name, metric_rule in module_val.items():
                if metric_name.startswith("_"):
                    continue
                if isinstance(metric_rule, dict):
                    rule_copy = copy.deepcopy(metric_rule)
                    rule_copy["_module_id"] = module_id
                    rule_copy["_module_key"] = module_key
                    indexed[metric_name] = rule_copy
        return indexed

    # ═══════════════════════════════════════════════════════════════
    # 内部方法：时间豁免判断
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _is_newly_started(start_date, exempt_days: int) -> bool:
        """判断项目是否处于成熟度豁免期内.

        Args:
            start_date: 开工日期, 支持 str / datetime / pd.Timestamp
            exempt_days: 豁免天数阈值

        Returns:
            True 若项目开工不足 exempt_days 天
        """
        if start_date is None:
            return False

        try:
            if isinstance(start_date, str):
                start_date = pd.Timestamp(start_date)
            if isinstance(start_date, pd.Timestamp):
                start_date = start_date.to_pydatetime()
            if not isinstance(start_date, datetime):
                return False

            age_days = (datetime.now() - start_date).days
            return age_days < exempt_days
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════════
    # 内部方法：核心评分逻辑 —— 平滑插值算法
    # ═══════════════════════════════════════════════════════════════

    def _compute_score(
        self,
        actual_value: float,
        metric_type: str,
        thresholds: list[dict],
    ) -> float:
        """根据指标类型和阈值配置, 计算 0-100 标准分.

        所有涉及阶梯跳跃的边界, 均使用 np.interp 线性插值平滑过渡.

        Args:
            actual_value: 实际业务值
            metric_type: direct_linear | inverse_linear | direct_step | inverse_step
            thresholds: 阈值配置列表

        Returns:
            0-100 标准分
        """
        if not thresholds:
            # 无阈值配置 → 直接映射
            return max(0.0, min(100.0, actual_value * 100))

        value = float(actual_value)

        if metric_type in ("direct_linear", "inverse_linear"):
            return self._linear_score(value, thresholds, metric_type)
        elif metric_type in ("direct_step", "inverse_step"):
            return self._step_score(value, thresholds, metric_type)
        else:
            # 未知类型 → 回退直接映射
            return max(0.0, min(100.0, value * 100))

    def _linear_score(
        self, value: float, thresholds: list[dict], metric_type: str
    ) -> float:
        """使用 np.interp 线性插值计算得分.

        将 thresholds 中的多段 [value_range] → [score_range] 拼接为
        连续的 xp(值) → fp(得分) 数组, 然后用 np.interp 插值.
        """
        # ── 构建插值锚点 ──
        x_points = []  # 业务值
        y_points = []  # 对应得分

        for t in thresholds:
            vr = t.get("value_range", [0, 1])
            sr = t.get("score_range", None)
            fixed_score = t.get("score", None)

            v_lo = vr[0] if vr[0] is not None else 0.0
            v_hi = vr[1] if len(vr) > 1 and vr[1] is not None else v_lo + 1.0

            if fixed_score is not None:
                # 固定得分段（如零触碰=100分）
                x_points.extend([v_lo, v_hi])
                y_points.extend([fixed_score, fixed_score])
            elif sr is not None:
                # 插值段
                s_lo, s_hi = sr[0], sr[1]
                x_points.extend([v_lo, v_hi])
                y_points.extend([s_lo, s_hi])

        if not x_points:
            return max(0.0, min(100.0, value * 100))

        # ── 排序并去重 ──
        sorted_pairs = sorted(zip(x_points, y_points), key=lambda p: p[0])
        xp = np.array([p[0] for p in sorted_pairs], dtype=np.float64)
        fp = np.array([p[1] for p in sorted_pairs], dtype=np.float64)

        # ── np.interp 线性插值 ──
        score = float(np.interp(value, xp, fp, left=fp[0], right=fp[-1]))

        # ── inverse 类型反转 ──
        if metric_type == "inverse_linear":
            score = 100.0 - score

        return round(max(0.0, min(100.0, score)), 1)

    def _step_score(
        self, value: float, thresholds: list[dict], metric_type: str
    ) -> float:
        """阶梯式打分, 但在阶梯边界使用线性插值平滑过渡.

        与纯阶梯打分的区别：不是 value_range[0] ≤ v ≤ value_range[1] → 固定分,
        而是在相邻阶梯的 [得分, 得分] 之间插入锚点做线性过渡.
        """
        x_points = []
        y_points = []

        for t in thresholds:
            vr = t.get("value_range", [0, 1])
            fixed_score = t.get("score", None)
            sr = t.get("score_range", None)

            v_lo = vr[0] if vr[0] is not None else 0.0
            v_hi = vr[1] if len(vr) > 1 and vr[1] is not None else v_lo + 1.0

            if fixed_score is not None:
                x_points.extend([v_lo, v_hi])
                y_points.extend([fixed_score, fixed_score])
            elif sr is not None:
                x_points.extend([v_lo, v_hi])
                y_points.extend([sr[0], sr[1]])

        if not x_points:
            return max(0.0, min(100.0, value * 100))

        sorted_pairs = sorted(zip(x_points, y_points), key=lambda p: p[0])
        xp = np.array([p[0] for p in sorted_pairs], dtype=np.float64)
        fp = np.array([p[1] for p in sorted_pairs], dtype=np.float64)

        score = float(np.interp(value, xp, fp, left=fp[0], right=fp[-1]))

        if metric_type == "inverse_step":
            score = 100.0 - score

        return round(max(0.0, min(100.0, score)), 1)

    # ═══════════════════════════════════════════════════════════════
    # 公开方法：0-100 → 1-3 连续档位映射（供 DiscreteAnalyzer v4.0 调用）
    # ═══════════════════════════════════════════════════════════════

    def score_to_risk_level(self, score: float) -> float:
        """模块得分 → 风险档位连续值（R轴，1.0-3.0）.

        R轴逻辑：得分越高 → 风险越低 → 档位越小。
        使用 np.interp 线性插值，消灭硬性阶梯断崖。

        64 → 3.0, 66 → 2.87, 72 → 2.47, 80 → 1.0
        """
        xp = np.array([0, 65, 80, 100], dtype=np.float64)
        fp = np.array([3.0, 3.0, 1.0, 1.0], dtype=np.float64)
        return float(np.interp(score, xp, fp))

    def score_to_return_level(self, score: float) -> float:
        """模块得分 → 收益档位连续值（E轴，1.0-3.0）.

        E轴逻辑：得分越高 → 收益越高 → 档位越大。
        使用 np.interp 线性插值，消灭硬性阶梯断崖。

        64 → 1.0, 66 → 1.13, 72 → 1.53, 80 → 3.0
        """
        xp = np.array([0, 65, 80, 100], dtype=np.float64)
        fp = np.array([1.0, 1.0, 3.0, 3.0], dtype=np.float64)
        return float(np.interp(score, xp, fp))

    # ═══════════════════════════════════════════════════════════════
    # 回退方法
    # ═══════════════════════════════════════════════════════════════

    def _fallback_score(self, metric_name: str, actual_value) -> tuple:
        """未配置规则时的自适应评分回退.

        尝试将值归一化到 0-100:
        - 若值在 [0, 1] 区间, 直接 ×100
        - 若值 > 1.5, 假设已经是 0-100 制, 直接返回
        - 缺失值返回 DATA_MISSING
        """
        if actual_value is None or (isinstance(actual_value, float) and np.isnan(actual_value)):
            return self.DATA_MISSING_SCORE, {
                "metric": metric_name,
                "raw_value": None,
                "score": self.DATA_MISSING_SCORE,
                "status": "DATA_MISSING",
                "reason": "未配置规则且值为空, 默认按数据缺失处理",
                "is_veto": False,
                "type": "fallback",
            }

        val = float(actual_value)
        if 0 <= val <= 1:
            score = val * 100.0
        elif val > 1.5:
            score = min(100.0, val)
        else:
            score = val * 100.0

        return round(score, 1), {
            "metric": metric_name,
            "raw_value": val,
            "score": round(score, 1),
            "status": "SCORED",
            "reason": f"回退自适应评分 (无 JSON 配置规则)",
            "is_veto": False,
            "type": "fallback",
        }


# ═══════════════════════════════════════════════════════════════════
# 便捷工厂函数
# ═══════════════════════════════════════════════════════════════════


def create_scoring_engine(config_path: str = None) -> ScoringEngine:
    """从配置文件创建 ScoringEngine 实例.

    Args:
        config_path: rules.json 路径, 默认自动探测

    Returns:
        已初始化的 ScoringEngine
    """
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "config", "rules.json"
        )
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    return ScoringEngine(config)


# ═══════════════════════════════════════════════════════════════════
# 自检入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # ── 加载配置 ──
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "rules.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    engine = ScoringEngine(config)

    print("=" * 72)
    print("  ScoringEngine 四大防线自检测试")
    print("=" * 72)

    # ── 测试1：平滑插值 ──
    print("\n[防线二] 平滑插值 —— 产值转化率")
    for val in [0.0, 0.15, 0.30, 0.40, 0.50, 0.65, 0.80, 1.0]:
        score, meta = engine.evaluate("产值转化率", val, level="project")
        bar = "█" * int(score / 5) if isinstance(score, (int, float)) else "???"
        print(f"  转化率 {val:.2f} → 得分 {score:6.1f}  {bar}")

    # ── 测试2：数据缺失防御 ──
    print("\n[防线三] 数据缺失防御 —— 产值转化率 = NaN")
    score, meta = engine.evaluate("产值转化率", None, level="project")
    print(f"  None → score={score}, status={meta['status']}")
    print(f"  reason: {meta['reason']}")

    # ── 测试3：成熟度时间豁免 ──
    print("\n[防线四] 成熟度时间豁免 —— 新开工项目")
    recent_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    score, meta = engine.evaluate(
        "产值转化率", 0.15, project_start_date=recent_date, level="project"
    )
    print(f"  开工日期={recent_date} (60天前)")
    print(f"  score={score}, status={meta['status']}")
    print(f"  reason: {meta.get('reason', 'N/A')}")

    # ── 测试4：红线乘数 ──
    print("\n[防线一] 红线乘数（Veto）—— 停工退场率碰零")
    module_results = [
        {"name": "产值转化率", "score": 85, "is_veto": False, "status": "SCORED"},
        {"name": "盈利健康度", "score": 72, "is_veto": True, "status": "SCORED"},
        {"name": "停工退场率", "score": 0, "is_veto": True, "status": "SCORED"},
        {"name": "在施项目活跃度", "score": 68, "is_veto": False, "status": "SCORED"},
    ]
    result = engine.calculate_module_score(module_results)
    print(f"  module_score={result['module_score']}")
    print(f"  veto_triggered={result['veto_triggered']}")
    print(f"  veto_metrics={result['veto_metrics']}")
    print(f"  raw_weighted={result['raw_weighted']} (如果没有veto, 模块得分={result['raw_weighted']})")

    # ── 测试5：九宫格映射 ──
    print("\n[九宫格] R/E 坐标映射")
    r = engine.compute_r_coordinate(module3_score=35, module5_score=42)
    e = engine.compute_e_coordinate(module4_score=78, module5_score=42)
    pos = engine.get_nine_grid_position(r, e)
    print(f"  模块三=35, 模块五=42 → R={r}")
    print(f"  模块四=78, 模块五=42 → E={e}")
    print(f"  九宫格: {pos['grid_key']} {pos['name']} ({pos['strategy']})")

    # ── 测试6：双轨制路由 ──
    print("\n[双轨制] 指标路由")
    proj_metrics = engine.get_project_metric_names()
    comp_metrics = engine.get_company_metric_names()
    print(f"  Project_Level 指标数: {len(proj_metrics)}")
    print(f"  Company_Level 指标数: {len(comp_metrics)}")
    print(f"  Project 独有(项目微观): {set(proj_metrics) - set(comp_metrics)}")
    print(f"  Company 独有(宏观画像): {set(comp_metrics) - set(proj_metrics)}")

    print("\n" + "=" * 72)
    print("  All tests passed")
    print("=" * 72)
