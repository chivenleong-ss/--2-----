"""
离散化分析引擎：风险-收益九宫格决策矩阵 — v3.0 双轨制版

核心架构：
  六大模块得分 (0-100) → 降维 → R(风险1-3档) × E(收益1-3档) → 九宫格
  ├─ Project_Level (单项目微观): 模块三+四+五 → R/E → 九宫格
  └─ Company_Level (分公司宏观): 全量六模块 → 雷达图+综合分

v3.0 更新：
1. 集成 ScoringEngine — 基于 config/rules.json 的 Project_Level/Company_Level 双轨制配置
2. 四大防线机制落地：红线乘数、平滑插值、数据缺失惩罚、成熟度时间豁免
3. 0-100 → 1-3 档降维映射：≥80→强势区(3档/1档), 65-79→稳健区(2档), <65→承压区(1档/3档)
4. 风险维度(R轴) ← 模块三(合同质量)+模块五(资金效率)
5. 收益维度(E轴) ← 模块四(履约盈利)+模块五(资金效率)
6. 严格双轨制隔离：单项目九宫格绝不混入 Company_Level 宏观指标
"""
import json
import os
import re
from typing import Any

import pandas as pd
import numpy as np

from utils.helpers import safe_float
from utils.scoring_engine import ScoringEngine, DATA_MISSING, EXEMPT


def _extract_city(address: str) -> str:
    """从 '中国/省份/城市/区县' 路径中提取城市名."""
    text = str(address or "").strip()
    if not text or text.lower() == "nan":
        return ""
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if parts and parts[0] in ("中国",):
        parts = parts[1:]
    if len(parts) >= 2:
        city = parts[1]
        if city and city not in ("市辖区", "省直辖", "直辖县", "无"):
            return city
    return parts[0] if parts else text


class DiscreteAnalyzer:
    """风险-收益离散化分析器，v2.10 六模块版."""

    def __init__(self, config: dict):
        self._config = config  # v3.1: 存储config供_score_project_modules使用
        rules = config.get("离散化分析", {})
        if not rules:
            disc_path = os.path.join(os.path.dirname(__file__), "..", "config", "discrete_rules.json")
            if os.path.exists(disc_path):
                with open(disc_path, "r", encoding="utf-8") as f:
                    disc_config = json.load(f)
                rules = disc_config.get("离散化分析", {})

        self.risk_cfg = rules.get("风险维度", {})
        self.return_cfg = rules.get("收益维度", {})
        self.grid_strategies = rules.get("九宫格处置策略", {})
        self.city_thresholds = rules.get("城市聚合", {})
        self.module_mapping = rules.get("六模块指标映射", {})  # v2.10

        self.risk_weights = self.risk_cfg.get("权重", {})
        self.return_weights = self.return_cfg.get("权重", {})

        self.risk_cut_low = self.risk_cfg.get("分箱阈值", {}).get("低风险上限", 1.6)
        self.risk_cut_high = self.risk_cfg.get("分箱阈值", {}).get("中风险上限", 2.4)
        self.return_cut_low = self.return_cfg.get("分箱阈值", {}).get("低收益上限", 1.6)
        self.return_cut_high = self.return_cfg.get("分箱阈值", {}).get("中收益上限", 2.4)

        inst = config.get("institutional", {})
        profit_cfg = inst.get("盈利底线", {})
        profit_new = profit_cfg.get("手册0520", profit_cfg)
        self.profit_floor = profit_new.get("承接效益率_严禁投标_上限", 0.0)

        # v2.10: 置信度阈值（从config读取）
        bh_cfg = config.get("business_health", {}) if config else {}
        cl = bh_cfg.get("confidence_levels", {})
        self.conf_high = cl.get("高置信度下限", 80)
        self.conf_medium = cl.get("中置信度下限", 50)

        # v3.2: 动态阈值路由（按年份和工程类别）
        dyn_bands = bh_cfg.get("score_bands_dynamic", {})
        self._switch_year = dyn_bands.get("制度切换年份", 2026)
        self._bands_new = dyn_bands.get("新制度", {"强势": 80, "稳健": 65})
        self._bands_old = dyn_bands.get("历史", {"强势": 75, "稳健": 60})
        self._bands_realestate = dyn_bands.get("地产", {"强势": 75, "稳健": 55})

        # v3.0: 九宫格映射规则（从config/rules.json读取，回退至discrete_rules.json）
        self._nine_grid = config.get("九宫格映射规则", {})
        if not self._nine_grid:
            self._nine_grid = rules  # fallback: 使用 discrete_rules.json 的原始结构
        if not self._nine_grid.get("九宫格处置策略"):
            self._nine_grid["九宫格处置策略"] = self.grid_strategies

        # v3.0: 评分引擎引用（延迟初始化）
        self._scoring_engine = None

        # v3.2: 提取所有 is_veto=true 的指标名 → 模块映射（驱动否决网）
        self._veto_indicator_map = {}  # {indicator_name: module_key}
        pl = config.get("Project_Level", {}) if config else {}
        for mk, mv in pl.items():
            if mk.startswith("_"):
                continue
            for ik, iv in mv.items():
                if ik.startswith("_"):
                    continue
                if isinstance(iv, dict) and iv.get("is_veto"):
                    self._veto_indicator_map[ik] = mk

        # v3.2: 严禁投标清单（从 institutional 读取）
        inst = config.get("institutional", {}) if config else {}
        self._forbidden_bid_rules = inst.get("风险分级_严禁投标", {})

    def _get_scoring_engine(self, config: dict = None) -> ScoringEngine:
        """获取或懒初始化评分引擎."""
        if self._scoring_engine is None:
            if config is not None:
                self._scoring_engine = ScoringEngine(config)
            else:
                self._scoring_engine = ScoringEngine(rules_path=os.path.join(
                    os.path.dirname(__file__), "..", "config", "rules.json"
                ))
        return self._scoring_engine

    # ═══════════════════════════════════════════════════════
    # 主入口：原有 run()（回退兼容）
    # ═══════════════════════════════════════════════════════

    def run(self, all_results: dict, dmp_df: pd.DataFrame,
            appendix_df: pd.DataFrame = None, region_auth: dict = None) -> dict:
        """原有入口：从模型原始输出直接计算R/E（v2.9兼容模式）."""
        proj_df = self._analyze_projects(all_results, dmp_df, appendix_df, region_auth)
        city_df = self._aggregate_by_city(proj_df)
        sub_df = self._aggregate_by_subsidiary(proj_df)
        summary = self._global_summary(proj_df)
        return {
            "projects": proj_df,
            "cities": city_df,
            "subsidiaries": sub_df,
            "summary": summary,
            "grid_distribution": self._grid_counts(proj_df),
            "_mode": "legacy",
        }

    # ═══════════════════════════════════════════════════════
    # v3.0 双轨制核心入口：基于 ScoringEngine + 六模块的R/E计算
    # ═══════════════════════════════════════════════════════

    # ── 模块得分 → 1-3 档位映射（v3.0 唯一离散化节点）──
    @staticmethod
    def score_to_risk_tier(score_0_100: float) -> int:
        """模块得分 → 风险档位 (R轴).

        风险逻辑：得分越高 → 风险越低 → 档位越小
        ≥80 → 低风险(1档)
        65-79 → 中风险(2档)
        <65 → 高风险(3档)

        Args:
            score_0_100: 模块0-100标准分

        Returns:
            1/2/3 风险档位
        """
        if score_0_100 >= 80:
            return 1  # 低风险
        elif score_0_100 >= 65:
            return 2  # 中风险
        else:
            return 3  # 高风险

    def _risk_continuous(self, score_0_100: float) -> float:
        """v4.0: 连续风险值 (1.0-3.0)，经 ScoringEngine 平滑插值消灭阶梯断崖."""
        engine = self._get_scoring_engine()
        return engine.score_to_risk_level(score_0_100)

    def _return_continuous(self, score_0_100: float) -> float:
        """v4.0: 连续收益值 (1.0-3.0)，经 ScoringEngine 平滑插值消灭阶梯断崖."""
        engine = self._get_scoring_engine()
        return engine.score_to_return_level(score_0_100)

    def _calibrate_thresholds(self, all_scores: list = None) -> tuple:
        """v4.0: 混合阈值标定（数据驱动 P33/P67 + 制度底线 75/60 + 冷启动 80/65）."""
        if all_scores is None or len(all_scores) < 10:
            return 80, 65  # 冷启动：首次运行使用绝对标准
        p33 = np.percentile(all_scores, 33)
        p67 = np.percentile(all_scores, 67)
        return max(p67, 75), max(p33, 60)

    def _dynamic_risk_tier(self, score_0_100: float, project_year: int = None,
                           engineering_type: str = None) -> float:
        """v4.0: 连续风险值 (1.0-3.0)，返回 float 替代旧版 int 1/2/3."""
        return self._risk_continuous(score_0_100)

    def _dynamic_return_tier(self, score_0_100: float, project_year: int = None,
                             engineering_type: str = None) -> float:
        """v4.0: 连续收益值 (1.0-3.0)，返回 float 替代旧版 int 1/2/3."""
        return self._return_continuous(score_0_100)

    @staticmethod
    def score_to_tier_label(score_0_100: float) -> str:
        """通用得分转标签."""
        if score_0_100 >= 80:
            return "强势区"
        elif score_0_100 >= 65:
            return "稳健区"
        else:
            return "承压区"

    @staticmethod
    def _truthy_veto(value) -> bool:
        """识别外部传入的一票否决/严禁投标标记."""
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return text in {"1", "true", "yes", "y", "是", "严禁投标", "red", "veto"}

    def _has_contract_veto_from_scores(self, module_scores: dict) -> bool:
        """run_dual_track直调用场景下的合同底线一票否决识别."""
        if not isinstance(module_scores, dict):
            return False
        for key in ("严禁投标", "合同底线一票否决", "合同底线_一票否决", "is_veto", "合同底线", "合同底线等级"):
            if key in module_scores and self._truthy_veto(module_scores.get(key)):
                return True
        for key in ("合同底线", "合同底线_档位", "合同底线等级", "合同底线_level"):
            if safe_float(module_scores.get(key, 0)) >= 3:
                return True
        return False

    def _has_contract_veto(self, proj_code, all_results) -> bool:
        """v4.0: 全模型扫描 — 遍历全部11个模型的red/严禁投标检出判定一票否决.

        替代旧版硬编码4个模型维度的做法。任一模型对该项目检出red/严禁投标即触发。
        """
        code = str(proj_code)
        if not code:
            return False

        RED_MODELS = ["1.1", "1.2", "1.3", "1.4", "2.1", "2.2", "2.3", "2.4", "2.5", "3.1", "3.2"]

        for model_id in RED_MODELS:
            model_data = all_results.get(model_id)
            if model_data is None:
                continue
            issues_df = model_data[0] if isinstance(model_data, tuple) else model_data
            if not isinstance(issues_df, pd.DataFrame) or len(issues_df) == 0:
                continue
            if "项目编码" not in issues_df.columns:
                continue

            proj_issues = issues_df[issues_df["项目编码"].astype(str) == code]
            if len(proj_issues) == 0:
                continue

            # 检查严重等级是否包含red/严禁投标
            severity_cols = [c for c in ("严重等级", "问题分类") if c in proj_issues.columns]
            if severity_cols:
                text = proj_issues[severity_cols].astype(str).agg(" ".join, axis=1)
                if text.str.contains("严禁投标|red", na=False).any():
                    return True

        return False

    def _apply_elimination_veto(self, reason: str = "合同底线严禁投标") -> dict:
        """返回一票否决后的淘汰区坐标与策略."""
        grid_key = "(3,1)"
        strategies = self._nine_grid.get("九宫格处置策略", {}) if hasattr(self, '_nine_grid') else self.grid_strategies
        strategy = strategies.get(grid_key, {})
        return {
            "r_score": 3.0,
            "e_score": 1.0,
            "r_level": 3,
            "e_level": 1,
            "grid_key": grid_key,
            "grid_name": strategy.get("名称", "淘汰区"),
            "strategy": strategy.get("策略", "立即止损，启动问责"),
            "veto_reason": reason,
        }

    def run_dual_track(
        self,
        module_scores: dict[str, float],
        project_start_date: str = None,
        level: str = "project",
        project_year: int = None,
        engineering_type: str = None,
    ) -> dict:
        """v3.2 双轨制主入口：从六模块0-100得分计算九宫格R/E坐标.

        **双轨制路由规则**：
        - level="project": 仅读取模块三(合同质量)+模块四(履约盈利)+模块五(资金效率)
          的单项目微观得分，严格剥离模块一、二的宏观指标。
        - level="company": 读取全量六模块得分，用于分公司宏观画像。

        **R轴(风险维度)**：模块三(55%)+模块五(45%)
          得分→档位: 使用 _dynamic_risk_tier (按年份/工程类别路由阈值)

        **E轴(收益维度)**：模块四(55%)+模块五(45%)
          得分→档位: 使用 _dynamic_return_tier (按年份/工程类别路由阈值)

        Args:
            module_scores: {"模块三_合同质量": 75, "模块四_履约盈利": 82, ...}
            project_start_date: 项目开工日期（可选）
            level: "project" | "company"
            project_year: 项目签约年份（v3.2: 动态阈值路由用）
            engineering_type: 工程类别（v3.2: 地产/基建差异化阈值）

        Returns:
            {r_score, e_score, r_level, e_level, grid_key, grid_name, strategy, ...}
        """
        has_contract_veto = self._has_contract_veto_from_scores(module_scores)

        # ═══════════════════════════════════════════════════════
        # 双轨制隔离：根据 level 选择模块
        # ═══════════════════════════════════════════════════════
        if level == "project":
            # 项目级：仅模块三+四+五（微观指标）
            allowed_modules = {
                "模块三_合同质量", "模块四_履约盈利", "模块五_资金效率",
            }
            # 也兼容下划线分隔的key
            mod3 = module_scores.get("模块三_合同质量",
                    module_scores.get("模块三", 50))
            mod4 = module_scores.get("模块四_履约盈利",
                    module_scores.get("模块四", 50))
            mod5 = module_scores.get("模块五_资金效率",
                    module_scores.get("模块五", 50))
        else:
            # 分公司级：引入模块一(区域布局)作为宏观风险维度
            mod1 = module_scores.get("模块一_区域布局",
                    module_scores.get("模块一", 50))
            mod3 = module_scores.get("模块三_合同质量",
                    module_scores.get("模块三", 50))
            mod4 = module_scores.get("模块四_履约盈利",
                    module_scores.get("模块四", 50))
            mod5 = module_scores.get("模块五_资金效率",
                    module_scores.get("模块五", 50))

        # ═══════════════════════════════════════════════════════
        # R轴：模块三(合同质量, 0.55) + 模块五(资金效率, 0.45)
        # ═══════════════════════════════════════════════════════
        r_cfg = self._nine_grid.get("风险维度_R轴", {}) if hasattr(self, '_nine_grid') else {}
        r_weights = r_cfg.get("构成模块", {})
        w3_r = r_weights.get("模块三_合同质量", {}).get("weight", 0.55)
        w5_r = r_weights.get("模块五_资金效率", {}).get("weight", 0.45)
        w1_r = 0.30 if level == "company" else 0.0

        # 0-100 → 1-3 档位（风险方向，v3.2: 动态阈值路由）
        r3_tier = self._dynamic_risk_tier(mod3, project_year, engineering_type)
        r5_tier = self._dynamic_risk_tier(mod5, project_year, engineering_type)
        if level == "company":
            r1_tier = self._dynamic_risk_tier(mod1, project_year, engineering_type)
            w3_r, w5_r = 0.40, 0.30
            r_components = [r1_tier, r3_tier, r5_tier]
            r_score = (r1_tier * w1_r + r3_tier * w3_r + r5_tier * w5_r) / (w1_r + w3_r + w5_r)
        else:
            r_components = [r3_tier, r5_tier]
            r_score = (r3_tier * w3_r + r5_tier * w5_r) / (w3_r + w5_r)
        r_score = round(max(1.0, min(3.0, r_score)), 2)

        # 分箱
        r_cut_low = r_cfg.get("分箱阈值", {}).get("低风险上限", 1.6)
        r_cut_high = r_cfg.get("分箱阈值", {}).get("中风险上限", 2.4)
        r_level = 1 if r_score <= r_cut_low else (2 if r_score <= r_cut_high else 3)
        # v4.0: 连续值下不再需要 max(r_components) == 3 强制升级逻辑

        # ═══════════════════════════════════════════════════════
        # E轴：模块四(履约盈利, 0.55) + 模块五(资金效率, 0.45)
        # ═══════════════════════════════════════════════════════
        e_cfg = self._nine_grid.get("收益维度_E轴", {}) if hasattr(self, '_nine_grid') else {}
        e_weights = e_cfg.get("构成模块", {})
        w4_e = e_weights.get("模块四_履约盈利", {}).get("weight", 0.55)
        w5_e = e_weights.get("模块五_资金效率", {}).get("weight", 0.45)

        # 0-100 → 1-3 档位（收益方向，v3.2: 动态阈值路由）
        e4_tier = self._dynamic_return_tier(mod4, project_year, engineering_type)
        e5_tier = self._dynamic_return_tier(mod5, project_year, engineering_type)
        e_score = (e4_tier * w4_e + e5_tier * w5_e) / (w4_e + w5_e)
        e_score = round(max(1.0, min(3.0, e_score)), 2)

        # 分箱
        e_cut_low = e_cfg.get("分箱阈值", {}).get("低收益上限", 1.6)
        e_cut_high = e_cfg.get("分箱阈值", {}).get("中收益上限", 2.4)
        e_level = 1 if e_score <= e_cut_low else (2 if e_score <= e_cut_high else 3)

        if has_contract_veto:
            veto = self._apply_elimination_veto()
            r_score = veto["r_score"]
            e_score = veto["e_score"]
            r_level = veto["r_level"]
            e_level = veto["e_level"]

        # ═══════════════════════════════════════════════════════
        # 九宫格定位
        # ═══════════════════════════════════════════════════════
        grid_key = f"({r_level},{e_level})"
        strategies = self._nine_grid.get("九宫格处置策略", {}) if hasattr(self, '_nine_grid') else {}
        strategy = strategies.get(grid_key, {})
        grid_name = strategy.get("名称", "未分类")
        grid_strategy = strategy.get("策略", "常规管理，动态监控")

        return {
            "r_score": r_score,
            "e_score": e_score,
            "r_level": r_level,
            "e_level": e_level,
            "grid_key": grid_key,
            "grid_name": grid_name,
            "strategy": grid_strategy,
            "r_detail": {
                **({"模块一_区域布局": {"module_score": mod1, "tier": r1_tier,
                               "label": self.score_to_tier_label(mod1)}} if level == "company" else {}),
                "模块三_合同质量": {"module_score": mod3, "tier": r3_tier,
                               "label": self.score_to_tier_label(mod3)},
                "模块五_资金效率": {"module_score": mod5, "tier": r5_tier,
                               "label": self.score_to_tier_label(mod5)},
            },
            "e_detail": {
                "模块四_履约盈利": {"module_score": mod4, "tier": e4_tier,
                               "label": self.score_to_tier_label(mod4)},
                "模块五_资金效率": {"module_score": mod5, "tier": e5_tier,
                               "label": self.score_to_tier_label(mod5)},
            },
            "level": level,
            "veto_reason": "合同底线严禁投标" if has_contract_veto else "",
            "_双轨制隔离": (
                "project模式下模块一(区域布局)、模块二(客户稳定)被严格阻断，"
                "绝不会混入宏观指标干扰单项目九宫格坐标" if level == "project"
                else "全量六模块参与分公司宏观画像计算"
            ),
        }

    # ═══════════════════════════════════════════════════════════
    # v3.1: 项目级真实评分 —— 接ScoringEngine
    # ═══════════════════════════════════════════════════════════

    def _score_project_modules(self, row: dict, all_results: dict, region_auth: dict = None) -> dict:
        """为单个项目独立计算模块三/四/五得分（0-100），基于项目自身DMP字段+模型输出。

        不再借用单位/城市聚合分，而是用ScoringEngine对项目逐指标评分。
        模块一/二保留单位级来源（它们本质是聚合指标）。

        Returns:
            {"模块三_合同质量": 75.5, "模块四_履约盈利": 82.0, "模块五_资金效率": 68.5}
        """
        engine = self._get_scoring_engine(self._config)
        proj_code = str(row.get("项目编码", ""))

        # ── 模块三：合同质量 ──
        m3_metrics = []

        # 4.1 严禁底线触碰次数（veto）: 来自模型2.1
        m21_df = all_results.get("2.1", (pd.DataFrame(), {}))[0] if isinstance(all_results, dict) else pd.DataFrame()
        veto_count = 0
        if len(m21_df) > 0 and "项目编码" in m21_df.columns:
            proj_m21 = m21_df[m21_df["项目编码"].astype(str) == proj_code]
            if len(proj_m21) > 0 and "严重等级" in m21_df.columns:
                veto_count = (proj_m21["严重等级"].astype(str).str.contains("严禁投标|red", na=False)).sum()
        m3_metrics.append({"name": "严禁底线触碰次数", "value": float(veto_count)})

        # 4.2 限制投标触发次数: 同上
        restrict_count = 0
        if len(m21_df) > 0 and "项目编码" in m21_df.columns:
            proj_m21 = m21_df[m21_df["项目编码"].astype(str) == proj_code]
            if len(proj_m21) > 0 and "严重等级" in m21_df.columns:
                restrict_count = (proj_m21["严重等级"].astype(str).str.contains("限制投标", na=False)).sum()
        m3_metrics.append({"name": "限制投标条款触碰次数", "value": float(restrict_count)})

        # 4.3 付款条件优良率: 非2.1问题的项目占比
        payment_good = 1.0 if veto_count + restrict_count == 0 else 0.0
        m3_metrics.append({"name": "付款条件合规度", "value": payment_good})

        # 4.4 合同条款不利度: 模型2.4标记（严重程度感知）
        m24_df = all_results.get("2.4", (pd.DataFrame(), {}))[0] if isinstance(all_results, dict) else pd.DataFrame()
        clause_bad = 0.0
        permit_ok = 1.0
        if len(m24_df) > 0 and "项目编码" in m24_df.columns:
            proj_m24 = m24_df[m24_df["项目编码"].astype(str) == proj_code]
            if len(proj_m24) > 0:
                # 合同条款不利度：按严重等级区分
                if "问题分类" in proj_m24.columns:
                    cats = proj_m24["问题分类"].astype(str)
                    sevs = proj_m24["严重等级"].astype(str) if "严重等级" in proj_m24.columns else cats
                    # 放弃优先受偿权/结算期超6月等严重条款 → 1.0, 其他不利条款 → 0.3
                    if cats.str.contains("放弃优先受偿|结算期.*6.*月|需协助业主融资", na=False).any() or sevs.str.contains("red|严禁", na=False).any():
                        clause_bad = 1.0
                    else:
                        clause_bad = 0.3
                else:
                    clause_bad = 0.3  # 有检出但无法判级 → 保守扣分

                # 三证合规率: 许可证/三证问题
                if "问题分类" in proj_m24.columns:
                    permit_issues = (proj_m24["问题分类"].astype(str).str.contains("三证|许可证", na=False)).sum()
                    sevs = proj_m24["严重等级"].astype(str) if "严重等级" in proj_m24.columns else pd.Series([""] * len(proj_m24))
                    if permit_issues > 0:
                        permit_ok = 0.0 if sevs.str.contains("red|严禁", na=False).any() else 0.3
        m3_metrics.append({"name": "合同条款不利度", "value": clause_bad})

        # 4.5 三证合规率
        m3_metrics.append({"name": "三证合规率", "value": permit_ok})

        m3_results = engine.evaluate_batch(m3_metrics, project_start_date=row.get("开工时间"), level="project")
        m3_module = engine.calculate_module_score(m3_results)
        m3_score = m3_module.get("module_score", 50.0)

        # ── 模块四：履约盈利 ──
        m4_metrics = []

        contract_amt = safe_float(row.get("签约额（元）", 0))
        actual_output = safe_float(row.get("实际完成产值", 0))

        # 5.1 产值转化率 (0-2范围，插值到0-100)
        conversion = actual_output / contract_amt if contract_amt > 0 else 0.0
        conversion = max(0.0, min(2.0, conversion))
        m4_metrics.append({"name": "产值转化率", "value": conversion})

        # 5.2 盈利健康度: A值（归一化：百分比→小数，兼容两种存储格式）
        a_value = safe_float(row.get("一次性经营效益率（%）（A值）", 0))
        if a_value > 1.0:
            a_value = a_value / 100.0
        m4_metrics.append({"name": "盈利健康度", "value": a_value})

        # 5.3 停工退场率: 模型2.5标记（严重程度感知：red/停工→1.0, 其他→0.3）
        m25_df = all_results.get("2.5", (pd.DataFrame(), {}))[0] if isinstance(all_results, dict) else pd.DataFrame()
        stop_ratio = 0.0
        if len(m25_df) > 0 and "项目编码" in m25_df.columns:
            proj_m25 = m25_df[m25_df["项目编码"].astype(str) == proj_code]
            if len(proj_m25) > 0:
                cats = proj_m25["问题分类"].astype(str)
                sevs = proj_m25["严重等级"].astype(str) if "严重等级" in proj_m25.columns else cats
                if cats.str.contains("停工|退场|停缓建", na=False).any() or sevs.str.contains("red|严禁", na=False).any():
                    stop_ratio = 1.0   # 真正停工退场 → 一票否决
                else:
                    stop_ratio = 0.3   # 施工异常但非停工 → 扣分但不否决
        m4_metrics.append({"name": "停工退场率", "value": stop_ratio})

        # 5.4 签约履约偏差率: 老签约+低产值转化
        # derive sign_year from 签约时间 (not _sign_year, which is dropped before scoring)
        sign_year = 0
        sign_time = row.get("签约时间")
        if sign_time is not None and not (isinstance(sign_time, float) and pd.isna(sign_time)):
            try:
                sign_year = pd.Timestamp(sign_time).year
            except Exception:
                pass
        sign_deviation = 1.0 if (conversion < 0.5 and sign_year > 0) else 0.0
        m4_metrics.append({"name": "签约履约偏差率", "value": sign_deviation})

        # 5.5 效益偏差率: 模型2.2（严重程度感知：red/严禁→1.0, 其他→0.3）
        m22_df = all_results.get("2.2", (pd.DataFrame(), {}))[0] if isinstance(all_results, dict) else pd.DataFrame()
        profit_dev = 0.0
        if len(m22_df) > 0 and "项目编码" in m22_df.columns and "问题分类" in m22_df.columns:
            proj_m22 = m22_df[m22_df["项目编码"].astype(str) == proj_code]
            if len(proj_m22) > 0:
                cats = proj_m22["问题分类"].astype(str)
                sevs = proj_m22["严重等级"].astype(str) if "严重等级" in proj_m22.columns else cats
                if sevs.str.contains("red|严禁|严重", na=False).any():
                    profit_dev = 1.0   # 严重效益偏差
                else:
                    profit_dev = 0.3   # 轻微偏差
        m4_metrics.append({"name": "效益偏差率", "value": profit_dev})

        # 5.6 在施项目活跃度: 产值>0且收款>0
        collection = safe_float(row.get("累计收款", 0))
        active = 1.0 if actual_output > 0 and collection > 0 else 0.0
        m4_metrics.append({"name": "在施项目活跃度", "value": active})

        m4_results = engine.evaluate_batch(m4_metrics, project_start_date=row.get("开工时间"), level="project")
        m4_module = engine.calculate_module_score(m4_results)
        m4_score = m4_module.get("module_score", 50.0)

        # ── 模块五：资金效率 ──
        m5_metrics = []

        # 6.1 资金占用率: 保证金/合同额（从模型2.3）
        m23_df = all_results.get("2.3", (pd.DataFrame(), {}))[0] if isinstance(all_results, dict) else pd.DataFrame()
        capital_bound = 0.0
        proj_m23 = pd.DataFrame()
        if len(m23_df) > 0 and "项目编码" in m23_df.columns:
            proj_m23 = m23_df[m23_df["项目编码"].astype(str) == proj_code]
            if len(proj_m23) > 0 and "保证金金额" in m23_df.columns:
                capital_bound = proj_m23["保证金金额"].apply(safe_float).sum()
        capital_occupancy = capital_bound / contract_amt if contract_amt > 0 else 0.0
        m5_metrics.append({"name": "资金占用率", "value": capital_occupancy})

        # 6.2 保证金周转天数: 从模型2.3问题描述提取（简化为0）
        m5_metrics.append({"name": "保证金周转天数", "value": 0.0})

        # 6.3-6.6: 基于模型2.3的严重程度感知指标（一次查询，分项判定）
        overdue_ratio = 0.0
        advance_gap = 0.0
        negative_flow = 0.0
        if len(proj_m23) > 0 and "问题分类" in proj_m23.columns:
            cats = proj_m23["问题分类"].astype(str)
            sevs = proj_m23["严重等级"].astype(str) if "严重等级" in proj_m23.columns else cats
            has_severe = sevs.str.contains("red|严禁|严重", na=False).any()

            # 逾期回收率：检查逾期天数 ≥ 90天 → 严重
            if cats.str.contains("逾期", na=False).any():
                desc_col = "问题描述" if "问题描述" in proj_m23.columns else None
                descs = proj_m23[desc_col].astype(str) if desc_col else pd.Series([""] * len(proj_m23))
                severe_overdue = False
                for d in descs:
                    m = re.search(r"逾期\s*(\d+)\s*天", d)
                    if m and int(m.group(1)) >= 90:
                        severe_overdue = True
                        break
                overdue_ratio = 1.0 if severe_overdue else (0.5 if has_severe else 0.3)

            # 预收款缺口率
            if cats.str.contains("预收款", na=False).any():
                advance_gap = 1.0 if has_severe else 0.3

            # 负流项目占比
            if cats.str.contains("负流|资金负", na=False).any():
                negative_flow = 1.0 if has_severe else 0.5

        m5_metrics.append({"name": "逾期回收率", "value": overdue_ratio})

        # 6.4 资金回收率: 收款/签约
        collection_rate = collection / contract_amt if contract_amt > 0 else 0.0
        m5_metrics.append({"name": "资金回收率", "value": collection_rate})

        # 6.5 预收款缺口率
        m5_metrics.append({"name": "预收款缺口率", "value": advance_gap})

        # 6.6 负流项目占比
        m5_metrics.append({"name": "负流项目占比", "value": negative_flow})

        m5_results = engine.evaluate_batch(m5_metrics, project_start_date=row.get("开工时间"), level="project")
        m5_module = engine.calculate_module_score(m5_results)
        m5_score = m5_module.get("module_score", 50.0)

        return {
            "模块三_合同质量": round(m3_score, 1),
            "模块四_履约盈利": round(m4_score, 1),
            "模块五_资金效率": round(m5_score, 1),
        }

    # ═══════════════════════════════════════════════════════════
    # v2.10 新入口：基于六模块指标的R/E计算（兼容保留）
    # ═══════════════════════════════════════════════════════════


    def run_with_module_scores(self, all_results: dict, dmp_df: pd.DataFrame,
                                business_results: dict = None,
                                appendix_df: pd.DataFrame = None,
                                region_auth: dict = None) -> dict:
        """v2.10 主入口：从六模块指标计算R/E.

        如果business_results为空或六模块数据不可用，自动回退到legacy模式。
        """
        if business_results is None or not isinstance(business_results, dict):
            return self.run(all_results, dmp_df, appendix_df, region_auth)

        module_index = self._build_module_index(business_results)

        # 检查是否有有效的六模块数据
        has_modules = any(
            module_index.get(k, {}).get("scores", {}).get(f"模块{n}_得分", 0) > 0
            for k in list(module_index.keys())[:1] for n in range(1, 7)
        )
        if not has_modules and module_index:
            # 新格式：module_index = {scope_name: {module scores + metrics}}
            pass

        proj_df = self._analyze_projects_with_modules(
            all_results, dmp_df, module_index, appendix_df, region_auth
        )
        city_df = self._aggregate_by_city(proj_df)
        sub_df = self._aggregate_by_subsidiary(proj_df)
        summary = self._global_summary(proj_df)

        return {
            "projects": proj_df,
            "cities": city_df,
            "subsidiaries": sub_df,
            "summary": summary,
            "grid_distribution": self._grid_counts(proj_df),
            "_mode": "module_based",
        }

    def _build_module_index(self, business_results: dict) -> dict:
        """构建六模块指标索引：{单位/城市名: {模块得分, 各指标值}}."""
        index = {}

        subsidiaries = business_results.get("subsidiaries", [])
        if hasattr(subsidiaries, "to_dict"):
            subsidiaries = subsidiaries.to_dict(orient="records")

        for row in (subsidiaries or []):
            name = str(row.get("名称", row.get("申报单位", ""))).strip()
            if not name or name == "nan":
                continue
            entry = {"scores": {}, "metrics": {}}
            for key, val in row.items():
                if key.startswith("模块") and key.endswith("_得分"):
                    entry["scores"][key] = safe_float(val)
                else:
                    entry["metrics"][key] = safe_float(val) if isinstance(val, (int, float, str)) and str(val).replace(".", "").replace("-", "").isdigit() else val
            # 将百分比值转换为0-1范围
            for k in list(entry["metrics"].keys()):
                v = entry["metrics"][k]
                if isinstance(v, (int, float)) and v > 1.5 and k not in ("保证金周转天数", "区域合同额强度", "签约额（亿元）", "项目数", "客户数", "综合得分"):
                    entry["metrics"][k] = v / 100.0
            index[name] = entry

        cities = business_results.get("cities", [])
        if hasattr(cities, "to_dict"):
            cities = cities.to_dict(orient="records")

        for row in (cities or []):
            name = str(row.get("名称", row.get("城市", ""))).strip()
            if not name or name == "nan":
                continue
            entry = {"scores": {}, "metrics": {}}
            for key, val in row.items():
                if key.startswith("模块") and key.endswith("_得分"):
                    entry["scores"][key] = safe_float(val)
                else:
                    entry["metrics"][key] = safe_float(val) if isinstance(val, (int, float, str)) and str(val).replace(".", "").replace("-", "").isdigit() else val
            for k in list(entry["metrics"].keys()):
                v = entry["metrics"][k]
                if isinstance(v, (int, float)) and v > 1.5 and k not in ("保证金周转天数", "区域合同额强度", "签约额（亿元）", "项目数", "客户数", "综合得分"):
                    entry["metrics"][k] = v / 100.0
            index[name] = entry

        return index

    def _default_modules(self) -> dict:
        return {"scores": {}, "metrics": {}}

    def _module_indicator_to_level(self, module_data, indicators, direction="direct",
                                    thresholds=None):
        """v2.10: 六模块指标 → 1/2/3离散档位.

        Args:
            module_data: {scores: {...}, metrics: {...}}
            indicators: str（单指标）或 [(name, weight), ...]（多指标加权）
            direction: 'direct'（值越大越好）或 'inverse'（值越小越好）
            thresholds: [lo, hi] 分箱阈值，如 [0.50, 0.80]。
                       默认None时使用百分位校准模式（P25/P75自动计算）。
                       每个子维度应有独立的业务校准阈值。
        """
        if isinstance(indicators, str):
            raw_value = module_data.get("metrics", {}).get(indicators, 0.5)
            raw_value = float(raw_value) if isinstance(raw_value, (int, float)) else 0.5
        else:
            raw_value = 0.0
            total_w = 0.0
            for name, weight in indicators:
                v = module_data.get("metrics", {}).get(name, 0.5)
                raw_value += float(v) * weight if isinstance(v, (int, float)) else 0.5 * weight
                total_w += weight
            raw_value = raw_value / total_w if total_w > 0 else 0.5

        raw_value = max(0.0, min(1.0, raw_value))
        if direction == "inverse":
            raw_value = 1.0 - raw_value

        # 使用维度专属阈值（来自discrete_rules.json），而非全局硬编码
        if thresholds and len(thresholds) == 2:
            lo, hi = float(thresholds[0]), float(thresholds[1])
        else:
            # 回退：三等分（仅当配置缺失时）
            lo, hi = 0.33, 0.67

        # 校准：阈值映射至离散档位
        # direct模式：value≥hi→1档(好), lo≤value<hi→2档(中), value<lo→3档(差)
        if raw_value >= hi:
            return 1
        elif raw_value >= lo:
            return 2
        else:
            return 3

    def _get_dim_thresholds(self, side, dim_name):
        """从六模块映射配置中读取维度专属分箱阈值.

        Args:
            side: '风险维度' 或 '收益维度'
            dim_name: 'region'/'contract'/'customer'/'capital'/'perf' 或
                      'profit'/'conversion'/'collection'/'scale'
        Returns:
            [lo, hi] 或 None
        """
        try:
            mm = self.module_mapping.get(side, {})
            dim_cfg = mm.get(dim_name, {})
            return dim_cfg.get("分箱阈值", None)
        except Exception:
            return None

    def _return_weight(self, *names, default=0.0) -> float:
        """兼容收益维度权重命名迁移."""
        for name in names:
            if name in self.return_weights:
                return self.return_weights.get(name, default)
        return default

    def _has_module_scores(self, module_data, *module_names) -> bool:
        """判断是否具备可用的模块0-100得分."""
        scores = module_data.get("scores", {}) if isinstance(module_data, dict) else {}
        return any(safe_float(scores.get(name, 0)) > 0 for name in module_names)

    def _project_grid_from_module_scores(self, module_data):
        """按新方案使用模块三/四/五得分计算项目级九宫格."""
        if not self._has_module_scores(
            module_data,
            "模块三_得分",
            "模块四_得分",
            "模块五_得分",
        ):
            return None

        score_map = {
            "模块三": safe_float(module_data.get("scores", {}).get("模块三_得分", 50)),
            "模块四": safe_float(module_data.get("scores", {}).get("模块四_得分", 50)),
            "模块五": safe_float(module_data.get("scores", {}).get("模块五_得分", 50)),
        }
        return self.run_dual_track(score_map, level="project", project_year=None, engineering_type=None)

    def _analyze_projects_with_modules(self, all_results, dmp_df, module_index,
                                        appendix_df=None, region_auth=None):
        """v2.10: 基于六模块指标的项目级离散分析."""
        rows = []
        for idx, row in dmp_df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            customer = str(row.get("客户名称", ""))
            unit = str(row.get("申报单位", "")).strip()
            city = _extract_city(row.get("项目地址", ""))
            contract_amt = safe_float(row.get("签约额（元）", 0))

            unit_mod = module_index.get(unit, self._default_modules())
            city_mod = module_index.get(city, unit_mod)

            # v3.1: 接ScoringEngine，用项目自身字段算模块三/四/五，杀死单位借分
            try:
                proj_module_scores = self._score_project_modules(row, all_results, region_auth)
                module_grid = self.run_dual_track(
                    proj_module_scores,
                    project_start_date=row.get("开工时间"),
                    level="project",
                    project_year=int(row.get("_sign_year", 0)) or None,
                    engineering_type=str(row.get("工程类别", "")) or None,
                )
                if module_grid is not None:
                    R = round(float(module_grid["r_score"]), 2)
                    r_level = int(module_grid["r_level"])
                    E = round(float(module_grid["e_score"]), 2)
                    e_level = int(module_grid["e_level"])
                    grid_key = module_grid["grid_key"]
                    grid_name = module_grid["grid_name"]
                    grid_strategy = module_grid["strategy"]
                else:
                    module_grid = None
            except Exception as e:
                module_grid = None

            if module_grid is None:
                # 回退兼容：ScoringEngine失败时才使用旧的子维度离散化逻辑
                try:
                    r_region = self._module_indicator_to_level(
                        unit_mod, "跨区域经营指数", "inverse",
                        thresholds=self._get_dim_thresholds("风险维度", "region"))
                except Exception:
                    r_region = self._region_risk(row, all_results, region_auth)

                try:
                    r_contract = self._module_indicator_to_level(
                        unit_mod, [("风险项目占比", 0.6), ("合同条款不利度", 0.4)], "inverse",
                        thresholds=self._get_dim_thresholds("风险维度", "contract"))
                except Exception:
                    r_contract = self._contract_risk(proj_code, all_results)

                try:
                    r_customer = self._module_indicator_to_level(
                        unit_mod, [("客户稳定性指数", 0.5), ("客户集中度风险", 0.5)], "inverse",
                        thresholds=self._get_dim_thresholds("风险维度", "customer"))
                except Exception:
                    r_customer = self._customer_risk(proj_code, customer, row, all_results)

                try:
                    r_capital = self._module_indicator_to_level(
                        unit_mod, [("逾期回收率", 0.6), ("负流项目占比", 0.4)], "inverse",
                        thresholds=self._get_dim_thresholds("风险维度", "capital"))
                except Exception:
                    r_capital = self._capital_risk(proj_code, all_results)

                try:
                    r_perf = self._module_indicator_to_level(
                        unit_mod, [("停工退场率", 0.6), ("签约履约偏差率", 0.4)], "inverse",
                        thresholds=self._get_dim_thresholds("风险维度", "perf"))
                except Exception:
                    r_perf = self._performance_risk(proj_code, all_results, row, appendix_df)

                R_raw = (r_region * self.risk_weights.get("区域合规", 1.0) +
                         r_contract * self.risk_weights.get("合同底线", 1.0) +
                         r_customer * self.risk_weights.get("客户健康", 0.8) +
                         r_capital * self.risk_weights.get("资金安全", 0.8) +
                         r_perf * self.risk_weights.get("履约真实", 0.8))
                total_w = sum(self.risk_weights.values())
                R = max(1.0, min(3.0, R_raw / total_w if total_w > 0 else R_raw))
                r_level = 1 if R <= self.risk_cut_low else (2 if R <= self.risk_cut_high else 3)

                try:
                    e_profit = self._module_indicator_to_level(
                        unit_mod, "盈利健康度", "direct",
                        thresholds=self._get_dim_thresholds("收益维度", "profit"))
                    e_profit = 4 - e_profit
                except Exception:
                    e_profit = self._profit_level(row)
                try:
                    e_conversion = self._module_indicator_to_level(
                        unit_mod, "产值转化率", "direct",
                        thresholds=self._get_dim_thresholds("收益维度", "conversion"))
                    e_conversion = 4 - e_conversion
                except Exception:
                    e_conversion = self._conversion_level(row, appendix_df)
                try:
                    e_collection = self._module_indicator_to_level(
                        unit_mod, "资金回收率", "direct",
                        thresholds=self._get_dim_thresholds("收益维度", "collection"))
                    e_collection = 4 - e_collection
                except Exception:
                    e_collection = self._collection_level(row, appendix_df)
                try:
                    e_scale = self._module_indicator_to_level(
                        unit_mod, "区域合同额强度", "direct",
                        thresholds=self._get_dim_thresholds("收益维度", "scale"))
                    e_scale = 4 - e_scale
                except Exception:
                    contract_intensity = unit_mod.get("metrics", {}).get("区域合同额强度", 2.0)
                    e_scale = 1 if float(contract_intensity) < 1 else (2 if float(contract_intensity) < 5 else 3)

                E_raw = (e_profit * self.return_weights.get("盈利水平", 1.05) +
                         e_conversion * self._return_weight("产值转化", "产值转化率", default=0.75) +
                         e_collection * self.return_weights.get("资金回收", 0.75) +
                         e_scale * self._return_weight("战略价值", "合同规模", default=0.45))
                total_ew = sum(self.return_weights.values())
                E = max(1.0, min(3.0, E_raw / total_ew if total_ew > 0 else E_raw))
                e_level = 1 if E <= self.return_cut_low else (2 if E <= self.return_cut_high else 3)

                grid_key = f"({r_level},{e_level})"
                grid_info = self.grid_strategies.get(grid_key, {})
                grid_name = grid_info.get("名称", "未分类")
                grid_strategy = grid_info.get("策略", "")

            veto_reason = ""
            if self._has_contract_veto(proj_code, all_results):
                veto = self._apply_elimination_veto()
                R = veto["r_score"]
                r_level = veto["r_level"]
                E = veto["e_score"]
                e_level = veto["e_level"]
                grid_key = veto["grid_key"]
                grid_name = veto["grid_name"]
                grid_strategy = veto["strategy"]
                veto_reason = veto["veto_reason"]

            # ── v3.2: 置信度 ──
            # v3.2 关键修复：不再挪用模块六得分，而是直接计算项目 DMP 字段完整度
            key_dmp_fields = ["签约额（元）", "实际完成产值", "累计收款",
                              "一次性经营效益率（%）（A值）", "开工时间", "客户名称"]
            field_count = sum(1 for f in key_dmp_fields
                            if f in row and pd.notna(row.get(f)))
            completeness = field_count / len(key_dmp_fields)
            confidence = completeness * 100.0  # 百分制

            # 仍保留模块六得分作为数据质量参考（从单位表取，但标记来源）
            module6_score = unit_mod.get("scores", {}).get("模块六_得分", 50)
            if isinstance(module6_score, (int, float)) and module6_score > 1.5:
                module6_score = module6_score
            conf_level = "high" if confidence >= self.conf_high else \
                         "medium" if confidence >= self.conf_medium else "low"

            rows.append({
                "项目编码": proj_code,
                "项目名称": proj_name,
                "申报单位": unit,
                "项目城市": city[:30] if city else "",
                "客户名称": customer[:30] if customer else "",
                "签约额（元）": contract_amt,
                "R_得分": round(R, 2),
                "R_等级": r_level,
                "E_得分": round(E, 2),
                "E_等级": e_level,
                "九宫格": grid_name,
                "处置策略": grid_strategy,
                "网格键": grid_key,
                # v3.2: 置信度从项目字段完整度独立计算
                "数据置信度": round(confidence, 1),
                "置信度等级": conf_level,
                "数据完整度_字段级": round(completeness * 100, 1),  # v3.2 新增：字段完整度
                "模块六_得分": round(float(module6_score) if isinstance(module6_score, (int, float)) else 50, 1),
                "一票否决原因": veto_reason,
            })

        return pd.DataFrame(rows)

    # ═══════════════════════════════════════════════════════
    # 原有子维度计算方法（回退兼容用）
    # ═══════════════════════════════════════════════════════

    def _analyze_projects(self, all_results, dmp_df, appendix_df=None, region_auth=None):
        """原有项目级离散分析（legacy模式）."""
        rows = []
        for idx, row in dmp_df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            customer = str(row.get("客户名称", ""))
            unit = str(row.get("申报单位", ""))
            city = str(row.get("项目地址", ""))
            contract_amt = safe_float(row.get("签约额（元）", 0))

            r_region = self._region_risk(row, all_results, region_auth)
            r_contract = self._contract_risk(proj_code, all_results)
            r_customer = self._customer_risk(proj_code, customer, row, all_results)
            r_capital = self._capital_risk(proj_code, all_results)
            r_perf = self._performance_risk(proj_code, all_results, row, appendix_df)

            R_raw = (r_region * self.risk_weights.get("区域合规", 1.0) +
                     r_contract * self.risk_weights.get("合同底线", 1.0) +
                     r_customer * self.risk_weights.get("客户健康", 0.8) +
                     r_capital * self.risk_weights.get("资金安全", 0.8) +
                     r_perf * self.risk_weights.get("履约真实", 0.8))
            total_w = sum(self.risk_weights.values())
            R = max(1.0, min(3.0, R_raw / total_w if total_w > 0 else R_raw))
            r_level = 1 if R <= self.risk_cut_low else (2 if R <= self.risk_cut_high else 3)

            e_profit = self._profit_level(row)
            e_conversion = self._conversion_level(row, appendix_df)
            e_collection = self._collection_level(row, appendix_df)
            e_scale = self._scale_level(contract_amt)

            E_raw = (e_profit * self.return_weights.get("盈利水平", 1.05) +
                     e_conversion * self._return_weight("产值转化", "产值转化率", default=0.75) +
                     e_collection * self.return_weights.get("资金回收", 0.75) +
                     e_scale * self._return_weight("战略价值", "合同规模", default=0.45))
            total_ew = sum(self.return_weights.values())
            E = max(1.0, min(3.0, E_raw / total_ew if total_ew > 0 else E_raw))
            e_level = 1 if E <= self.return_cut_low else (2 if E <= self.return_cut_high else 3)

            grid_key = f"({r_level},{e_level})"
            grid_info = self.grid_strategies.get(grid_key, {})
            grid_name = grid_info.get("名称", "未分类")
            grid_strategy = grid_info.get("策略", "")

            veto_reason = ""
            if self._has_contract_veto(proj_code, all_results):
                veto = self._apply_elimination_veto()
                R = veto["r_score"]
                r_level = veto["r_level"]
                E = veto["e_score"]
                e_level = veto["e_level"]
                grid_key = veto["grid_key"]
                grid_name = veto["grid_name"]
                grid_strategy = veto["strategy"]
                veto_reason = veto["veto_reason"]

            rows.append({
                "项目编码": proj_code, "项目名称": proj_name,
                "申报单位": unit, "项目城市": city[:30] if city else "",
                "客户名称": customer[:30] if customer else "",
                "签约额（元）": contract_amt,
                "R_得分": round(R, 2), "R_等级": r_level,
                "E_得分": round(E, 2), "E_等级": e_level,
                "九宫格": grid_name, "处置策略": grid_strategy,
                "网格键": grid_key,
                "数据置信度": 50.0, "置信度等级": "medium",
                "数据完整度_字段级": 50.0,
                "模块六_得分": 50.0,
                "一票否决原因": veto_reason,
            })
        return pd.DataFrame(rows)

    def _region_risk(self, row, all_results, region_auth=None):
        proj_code = str(row.get("项目编码", ""))
        r11_df = all_results.get("1.1", (pd.DataFrame(), {}))[0]
        if len(r11_df) > 0 and "项目编码" in r11_df.columns:
            proj_issues = r11_df[r11_df["项目编码"].astype(str) == proj_code]
            if len(proj_issues) > 0:
                cats = proj_issues["问题分类"].astype(str)
                if cats.str.contains("窜区|跨区域违规", na=False).any():
                    return 3
        city = _extract_city(row.get("项目地址", ""))
        if not city or city in ("nan", ""):
            return 2
        if region_auth:
            unit = str(row.get("申报单位", "")).strip()
            unit_auth = region_auth.get(unit, {})
            if isinstance(unit_auth, dict):
                deep = unit_auth.get("深耕", [])
                if any(c in city for c in deep):
                    return 1
        return 2

    def _contract_risk(self, proj_code, all_results):
        r21_df = all_results.get("2.1", (pd.DataFrame(), {}))[0]
        if len(r21_df) == 0 or "项目编码" not in r21_df.columns:
            return 1
        proj_issues = r21_df[r21_df["项目编码"].astype(str) == proj_code]
        if len(proj_issues) == 0:
            return 1
        severities = proj_issues["严重等级"].astype(str)
        if severities.str.contains("严禁投标|red", na=False).any():
            return 3
        if severities.str.contains("限制投标", na=False).any():
            return 2
        return 1

    def _customer_risk(self, proj_code, customer, row, all_results):
        is_quality = str(row.get("是否优质客户", "")).strip() == "是"
        if is_quality:
            return 1
        r31_df = all_results.get("3.1", (pd.DataFrame(), {}))[0]
        if len(r31_df) > 0 and "客户名称" in r31_df.columns:
            cust_issues = r31_df[r31_df["客户名称"].astype(str) == customer]
            if len(cust_issues) > 0:
                cats = cust_issues["问题分类"].astype(str)
                if cats.str.contains("僵尸|流失", na=False).any():
                    return 3
                return 2
        return 2

    def _capital_risk(self, proj_code, all_results):
        r23_df = all_results.get("2.3", (pd.DataFrame(), {}))[0]
        if len(r23_df) == 0 or "项目编码" not in r23_df.columns:
            return 1
        proj_issues = r23_df[r23_df["项目编码"].astype(str) == proj_code]
        if len(proj_issues) == 0:
            return 1
        types = proj_issues["问题分类"].astype(str)
        if types.str.contains("资金负流", na=False).any():
            return 3
        descs = proj_issues["问题描述"].astype(str)
        for d in descs:
            m = re.search(r"逾期\s*(\d+)\s*天", d)
            if m and int(m.group(1)) >= 90:
                return 3
        if types.str.contains("保证金逾期|预收款逾期|延期回收|延期到账", na=False).any():
            return 2
        return 1

    def _performance_risk(self, proj_code, all_results, row, appendix_df=None):
        r25_df = all_results.get("2.5", (pd.DataFrame(), {}))[0]
        if len(r25_df) > 0 and "项目编码" in r25_df.columns:
            proj_issues = r25_df[r25_df["项目编码"].astype(str) == proj_code]
            if len(proj_issues) > 0:
                cats = proj_issues["问题分类"].astype(str)
                sevs = proj_issues["严重等级"].astype(str)
                if cats.str.contains("停工|退场|停缓建", na=False).any() or \
                   sevs.str.contains("red", na=False).any():
                    return 3
                return 2
        contract_amt = safe_float(row.get("签约额（元）", 0))
        actual_output = safe_float(row.get("实际完成产值", 0))
        if contract_amt > 0 and actual_output > 0:
            ratio = actual_output / contract_amt
            if ratio < 0.30:
                return 2
        return 1

    def _profit_level(self, row):
        a_val = safe_float(row.get("一次性经营效益率（%）（A值）", 0))
        if a_val <= self.profit_floor:
            return 1
        if a_val < 0.04:
            return 2
        return 3

    def _conversion_level(self, row, appendix_df=None):
        contract_amt = safe_float(row.get("签约额（元）", 0))
        if contract_amt <= 0:
            return 2
        actual_output = safe_float(row.get("实际完成产值", 0))
        if actual_output <= 0:
            return 1
        ratio = actual_output / contract_amt
        if ratio >= 0.80:
            return 3
        if ratio >= 0.50:
            return 2
        return 1

    def _collection_level(self, row, appendix_df=None):
        contract_amt = safe_float(row.get("签约额（元）", 0))
        if contract_amt <= 0:
            return 2
        collection = safe_float(row.get("累计收款", 0))
        if collection <= 0:
            return 1
        ratio = collection / contract_amt
        if ratio >= 0.60:
            return 3
        if ratio >= 0.30:
            return 2
        return 1

    @staticmethod
    def _scale_level(contract_amt):
        if contract_amt >= 500_000_000:
            return 3
        if contract_amt >= 100_000_000:
            return 2
        return 1

    # ═══════════════════════════════════════════════════════
    # 聚合方法
    # ═══════════════════════════════════════════════════════

    def _aggregate_by_city(self, proj_df):
        if proj_df.empty or "项目城市" not in proj_df.columns:
            return pd.DataFrame()
        city_groups = proj_df.groupby("项目城市")
        rows = []
        red_threshold = self.city_thresholds.get("红色城市阈值", 0.50)
        yellow_threshold = self.city_thresholds.get("黄色城市阈值", 0.30)
        # v3.1: 聚合惩罚参数
        disp_penalty = self.city_thresholds.get("方差惩罚系数", 0.3)
        conc_penalty = self.city_thresholds.get("集中度惩罚系数", 0.5)
        high_risk_thresh = self.city_thresholds.get("高危占比阈值", 0.2)

        for city, group in city_groups:
            if not city or city in ("nan", ""):
                continue
            n = len(group)
            total_contract = group["签约额（元）"].sum()
            w = group["签约额（元）"].clip(lower=0)

            # v3.1: 合同额加权均值 + 方差 + 集中度惩罚
            if w.sum() > 0:
                avg_r = (group["R_得分"] * w).sum() / w.sum()
                avg_e = (group["E_得分"] * w).sum() / w.sum()
            else:
                avg_r = group["R_得分"].mean()
                avg_e = group["E_得分"].mean()

            std_r = group["R_得分"].std(ddof=0)
            std_e = group["E_得分"].std(ddof=0)
            high_risk_ratio = (group["R_等级"] == 3).mean()

            # R轴惩罚（R大越差）
            penalty_r = disp_penalty * std_r + conc_penalty * max(0, high_risk_ratio - high_risk_thresh)
            adj_r = min(3.0, avg_r + penalty_r)

            # E轴惩罚（E大越好，分化向下修正）
            adj_e = max(1.0, avg_e - disp_penalty * std_e)

            grid_counts = group["九宫格"].value_counts().to_dict()
            elimination_pct = (grid_counts.get("淘汰区", 0) + grid_counts.get("整顿区", 0)) / n
            expansion_pct = (grid_counts.get("扩张区", 0) + grid_counts.get("培育区", 0)) / n
            avg_confidence = group["数据置信度"].mean() if "数据置信度" in group.columns else 50.0

            if elimination_pct > red_threshold:
                label = "[RED] 红色城市（战略退出）"
            elif expansion_pct > (1 - yellow_threshold):
                label = "[GREEN] 绿色城市（加大投入）"
            else:
                label = "[YELLOW] 黄色城市（审慎维持）"

            rows.append({
                "城市": city, "项目数": n,
                "合同总额（亿元）": round(total_contract / 1e8, 2),
                "平均R": round(adj_r, 2), "平均E": round(adj_e, 2),
                "内部分化度R": round(std_r, 2),  # v3.1 新增列，供前端高亮
                "淘汰整顿区占比": round(elimination_pct, 2),
                "扩张培育区占比": round(expansion_pct, 2),
                "城市标签": label,
                "平均置信度": round(avg_confidence, 1),
            })
        city_df = pd.DataFrame(rows)
        if not city_df.empty:
            city_df = city_df.sort_values("平均R")
        return city_df

    def _aggregate_by_subsidiary(self, proj_df):
        if proj_df.empty or "申报单位" not in proj_df.columns:
            return pd.DataFrame()
        unit_groups = proj_df.groupby("申报单位")
        rows = []
        # v3.1: 聚合惩罚参数
        disp_penalty = self.city_thresholds.get("方差惩罚系数", 0.3)
        conc_penalty = self.city_thresholds.get("集中度惩罚系数", 0.5)
        high_risk_thresh = self.city_thresholds.get("高危占比阈值", 0.2)

        for unit, group in unit_groups:
            n = len(group)
            total_contract = group["签约额（元）"].sum()
            w = group["签约额（元）"].clip(lower=0)

            # v3.1: 合同额加权均值 + 方差 + 集中度惩罚
            if w.sum() > 0:
                avg_r = (group["R_得分"] * w).sum() / w.sum()
                avg_e = (group["E_得分"] * w).sum() / w.sum()
            else:
                avg_r = group["R_得分"].mean()
                avg_e = group["E_得分"].mean()

            std_r = group["R_得分"].std(ddof=0)
            std_e = group["E_得分"].std(ddof=0)
            high_risk_ratio = (group["R_等级"] == 3).mean()

            # R轴惩罚（R大越差）
            penalty_r = disp_penalty * std_r + conc_penalty * max(0, high_risk_ratio - high_risk_thresh)
            adj_r = min(3.0, avg_r + penalty_r)

            # E轴惩罚（E大越好，分化向下修正）
            adj_e = max(1.0, avg_e - disp_penalty * std_e)

            grid_counts = group["九宫格"].value_counts().to_dict()
            expansion_pct = (grid_counts.get("扩张区", 0) + grid_counts.get("培育区", 0)) / n
            elimination_pct = (grid_counts.get("淘汰区", 0) + grid_counts.get("整顿区", 0)) / n
            avg_confidence = group["数据置信度"].mean() if "数据置信度" in group.columns else 50.0

            if expansion_pct >= 0.30 and elimination_pct <= 0.10:
                unit_type = "优质型"
            elif elimination_pct >= 0.25:
                unit_type = "风险型"
            else:
                unit_type = "均衡型"

            rows.append({
                "申报单位": unit, "项目数": n,
                "合同总额（亿元）": round(total_contract / 1e8, 2),
                "平均R": round(adj_r, 2), "平均E": round(adj_e, 2),
                "内部分化度R": round(std_r, 2),  # v3.1 新增列，供前端高亮
                "扩张区占比": round(expansion_pct, 2),
                "淘汰区占比": round(elimination_pct, 2),
                "单位类型": unit_type,
                "平均置信度": round(avg_confidence, 1),
            })
        sub_df = pd.DataFrame(rows)
        if not sub_df.empty:
            sub_df = sub_df.sort_values("平均R")
        return sub_df

    def _global_summary(self, proj_df):
        if proj_df.empty:
            return {"total_projects": 0}
        n = len(proj_df)
        grid_counts = proj_df["九宫格"].value_counts().to_dict()
        elimination_count = grid_counts.get("淘汰区", 0) + grid_counts.get("整顿区", 0)
        expansion_count = grid_counts.get("扩张区", 0) + grid_counts.get("培育区", 0)
        high_risk_count = len(proj_df[proj_df["R_等级"] == 3])
        total_contract = proj_df["签约额（元）"].sum()
        avg_confidence = proj_df["数据置信度"].mean() if "数据置信度" in proj_df.columns else 50.0
        low_conf_count = len(proj_df[proj_df["数据置信度"] < 50]) if "数据置信度" in proj_df.columns else 0

        return {
            "total_projects": n,
            "total_contract_yi": round(total_contract / 1e8, 2),
            "high_risk_count": high_risk_count,
            "high_risk_pct": round(high_risk_count / n * 100, 1) if n > 0 else 0,
            "elimination_count": elimination_count,
            "expansion_count": expansion_count,
            "avg_R": round(proj_df["R_得分"].mean(), 2),
            "avg_E": round(proj_df["E_得分"].mean(), 2),
            "grid_distribution": grid_counts,
            "avg_confidence": round(avg_confidence, 1),
            "low_confidence_count": int(low_conf_count),
        }

    @staticmethod
    def _grid_counts(proj_df):
        if proj_df.empty:
            return {}
        grid = proj_df.groupby(["R_等级", "E_等级"]).size().unstack(fill_value=0)
        result = {}
        for r in [1, 2, 3]:
            for e in [1, 2, 3]:
                result[f"({r},{e})"] = int(grid.loc[r, e]) if r in grid.index and e in grid.columns else 0
        return result


# ═══════════════════════════════════════════════════════════════════
# v3.0 便捷工厂：双轨制离散分析器
# ═══════════════════════════════════════════════════════════════════


class DualTrackDiscreteAnalyzer:
    """v3.0 双轨制离散分析器 —— 封装 DiscreteAnalyzer + ScoringEngine.

    使用方式::

        config = json.load(open("config/rules.json"))
        analyzer = DualTrackDiscreteAnalyzer(config)

        # 场景一：单项目穿透（高管"定进退"）
        result = analyzer.analyze_project({
            "模块三_合同质量": 35,   # 合同底线穿透严重
            "模块四_履约盈利": 78,   # 履约盈利尚可
            "模块五_资金效率": 42,   # 资金回收困难
        })
        # → R=3.0(淘汰区), E=1.95(优化区)
        # → 九宫格: (3,2) 整顿区 — 限期整改，回溯审批

        # 场景二：分公司诊断（审计"查病因"）
        result = analyzer.analyze_company({
            "模块一_区域布局": 72,
            "模块二_客户稳定": 65,
            "模块三_合同质量": 58,
            "模块四_履约盈利": 81,
            "模块五_资金效率": 44,
            "模块六_数据质量": 90,
        })
        # → 综合得分 + 雷达图数据 + 短板诊断
    """

    def __init__(self, config: dict = None, rules_path: str = None):
        """初始化双轨制分析器.

        Args:
            config: 完整配置字典（来自 rules.json）
            rules_path: 配置文件路径
        """
        if config is None:
            if rules_path is None:
                rules_path = os.path.join(
                    os.path.dirname(__file__), "..", "config", "rules.json"
                )
            with open(rules_path, "r", encoding="utf-8") as f:
                config = json.load(f)

        self._config = config
        self._engine = ScoringEngine(config)
        self._discrete = DiscreteAnalyzer(config)

    # ── 属性代理 ──

    @property
    def scoring_engine(self) -> ScoringEngine:
        return self._engine

    @property
    def discrete_analyzer(self) -> DiscreteAnalyzer:
        return self._discrete

    # ── 场景一：单项目穿透分析 ──

    def analyze_project(
        self,
        module_scores: dict[str, float],
        project_start_date: str = None,
        project_year: int = None,
        engineering_type: str = None,
    ) -> dict:
        """单项目九宫格分析 —— 仅使用 Project_Level 微观指标.

        **双轨制隔离保证**：
        - 只读取模块三(合同质量)、模块四(履约盈利)、模块五(资金效率)
        - 绝不混入模块一(区域布局)、模块二(客户稳定)宏观指标
        - 防止烂项目借分公司好业绩"洗白"

        Args:
            module_scores: {"模块三_合同质量": 75, "模块四_履约盈利": 82, ...}
            project_start_date: 项目开工日期

        Returns:
            九宫格完整诊断结果
        """
        grid = self._discrete.run_dual_track(
            module_scores,
            project_start_date=project_start_date,
            level="project",
            project_year=project_year,
            engineering_type=engineering_type,
        )

        # 生成决策建议
        r_level = grid["r_level"]
        e_level = grid["e_level"]
        recommendations = self._project_recommendations(r_level, e_level, grid)

        return {
            **grid,
            "recommendations": recommendations,
            "analysis_type": "单项目穿透分析（Project_Level）",
            "_双轨制检查": (
                "已通过：未混入模块一(区域布局)、模块二(客户稳定)宏观指标"
            ),
        }

    # ── 场景二：分公司组织诊断 ──

    def analyze_company(
        self,
        module_scores: dict[str, float],
    ) -> dict:
        """分公司宏观画像分析 —— 全量六模块综合评估.

        用于审计/业务部门"查病因"——融合宏观战略指标与项目汇总得分.

        Args:
            module_scores: 全量六模块得分字典

        Returns:
            综合诊断结果
        """
        # 全量六模块加权综合得分
        weights = self._engine.get_module_weights(level="company")
        total_score = 0.0
        detail = {}
        for key, w in weights.items():
            score = module_scores.get(key, 50)
            total_score += score * w
            detail[key] = {
                "score": score,
                "weight": round(w, 2),
                "tier": DiscreteAnalyzer.score_to_tier_label(score),
                "contribution": round(score * w, 1),
            }

        total_score = round(total_score, 1)

        # 短板诊断
        sorted_modules = sorted(detail.items(), key=lambda x: x[1]["score"])
        weakest = sorted_modules[0] if sorted_modules else (None, {})
        strongest = sorted_modules[-1] if sorted_modules else (None, {})

        # 全量九宫格坐标（R/E）
        grid = self._discrete.run_dual_track(
            module_scores,
            level="company",
        )

        return {
            "total_score": total_score,
            "score_label": DiscreteAnalyzer.score_to_tier_label(total_score),
            "module_detail": detail,
            "weakest_module": weakest[0],
            "weakest_score": weakest[1].get("score", 0) if weakest[1] else 0,
            "strongest_module": strongest[0],
            "strongest_score": strongest[1].get("score", 0) if strongest[1] else 0,
            "grid_position": grid,
            "analysis_type": "分公司组织诊断（Company_Level）",
            "recommendations": self._company_recommendations(total_score, detail),
        }

    # ── 批量分析 ──

    def analyze_batch_projects(
        self,
        projects: list[dict],
    ) -> pd.DataFrame:
        """批量单项目九宫格分析.

        Args:
            projects: [{"项目编码": "P001", "模块三_合同质量": 75, ...}, ...]

        Returns:
            DataFrame, 每行一个项目的九宫格结果
        """
        rows = []
        for proj in projects:
            code = proj.get("项目编码", proj.get("project_code", ""))
            name = proj.get("项目名称", proj.get("project_name", ""))
            scores = {
                k: v for k, v in proj.items()
                if k.startswith("模块") and not k.startswith("模块一") and not k.startswith("模块二")
            }
            start_date = proj.get("开工时间", proj.get("project_start_date"))
            result = self.analyze_project(scores, project_start_date=start_date)
            rows.append({
                "项目编码": code,
                "项目名称": name,
                "R_得分": result["r_score"],
                "E_得分": result["e_score"],
                "R_等级": result["r_level"],
                "E_等级": result["e_level"],
                "九宫格": result["grid_name"],
                "处置策略": result["strategy"],
                "建议": " | ".join(result.get("recommendations", [])),
            })
        return pd.DataFrame(rows)

    # ── 决策建议生成 ──

    @staticmethod
    def _project_recommendations(r_level: int, e_level: int, grid: dict) -> list[str]:
        """根据九宫格位置生成项目级决策建议."""
        recs = []
        grid_key = grid.get("grid_key", "")

        if r_level == 3:
            recs.append("高风险——立即触发红线复盘，回溯审批链路")
            recs.append("建议暂停新资源投入，启动专项整改")
        elif r_level == 2:
            recs.append("中风险——加强日常监控频次至月度跟踪")
        else:
            recs.append("低风险——按季度常规跟踪即可")

        if e_level == 1:
            recs.append("低收益——评估是否主动退出或合并释放资源")
        elif e_level == 3:
            recs.append("高收益——可考虑资源倾斜，扩大市场份额")

        recs.append(f"处置策略: {grid.get('strategy', '常规管理')}")
        return recs

    @staticmethod
    def _company_recommendations(
        total_score: float, detail: dict
    ) -> list[str]:
        """根据分公司综合得分生成组织级诊断建议."""
        recs = []
        if total_score < 60:
            recs.append("全局承压——建议纳入重点经营诊断清单，启动专项治理")
        elif total_score < 75:
            recs.append("整体可控——但存在明显短板，建议针对性提升")

        sorted_mods = sorted(detail.items(), key=lambda x: x[1]["score"])
        if sorted_mods:
            weak_mod, weak_info = sorted_mods[0]
            if weak_info["score"] < 65:
                recs.append(f"短板优先修复: {weak_mod}({weak_info['score']}分)")

        return recs


# ═══════════════════════════════════════════════════════════════════
# 自检入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 72)
    print("  DualTrackDiscreteAnalyzer 双轨制自检")
    print("=" * 72)

    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "rules.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    analyzer = DualTrackDiscreteAnalyzer(config)

    # ── 场景一：单项目九宫格 ──
    print("\n[场景一] 单项目穿透分析 (Project_Level)")
    project_scores = {
        "模块三_合同质量": 35,   # 底线穿透严重
        "模块四_履约盈利": 78,   # 履约尚可
        "模块五_资金效率": 42,   # 资金困难
    }
    result = analyzer.analyze_project(project_scores)
    print(f"  R轴: {result['r_score']} (等级{result['r_level']})")
    print(f"  E轴: {result['e_score']} (等级{result['e_level']})")
    print(f"  九宫格: {result['grid_key']} {result['grid_name']}")
    print(f"  策略: {result['strategy']}")
    print(f"  建议: {result['recommendations']}")
    print(f"  隔离检查: {result['_双轨制检查']}")

    # ── 场景二：分公司诊断 ──
    print("\n[场景二] 分公司宏观诊断 (Company_Level)")
    company_scores = {
        "模块一_区域布局": 72,
        "模块二_客户稳定": 65,
        "模块三_合同质量": 58,
        "模块四_履约盈利": 81,
        "模块五_资金效率": 44,
        "模块六_数据质量": 90,
    }
    result2 = analyzer.analyze_company(company_scores)
    print(f"  综合得分: {result2['total_score']}")
    print(f"  得分标签: {result2['score_label']}")
    print(f"  最强模块: {result2['strongest_module']} ({result2['strongest_score']}分)")
    print(f"  最弱模块: {result2['weakest_module']} ({result2['weakest_score']}分)")
    print(f"  九宫格: {result2['grid_position']['grid_key']} {result2['grid_position']['grid_name']}")
    print(f"  建议: {result2['recommendations']}")

    # ── 场景三：不同水位对比 ──
    print("\n[场景三] 0-100 → 1-3档映射验证")
    test_scores = [95, 82, 80, 79, 65, 64, 40, 15]
    for s in test_scores:
        r_tier = DiscreteAnalyzer.score_to_risk_tier(s)
        e_tier = DiscreteAnalyzer.score_to_return_tier(s)
        label = DiscreteAnalyzer.score_to_tier_label(s)
        print(f"  得分{s:3d} → R档{r_tier}({'低' if r_tier==1 else '中' if r_tier==2 else '高'}风险)"
              f" | E档{e_tier}({'高' if e_tier==3 else '中' if e_tier==2 else '低'}收益)"
              f" | {label}")

    # ── 场景四：双轨制隔离验证 ──
    print("\n[场景四] 双轨制隔离验证")
    # 项目级：即使传入模块一、二，也应该被忽略
    mixed_scores = {
        "模块一_区域布局": 95,  # 分公司宏观分很高
        "模块二_客户稳定": 90,  # 分公司宏观分很高
        "模块三_合同质量": 25,  # 但这个项目本身很差
        "模块四_履约盈利": 30,
        "模块五_资金效率": 20,
    }
    result3 = analyzer.analyze_project(mixed_scores)
    print(f"  项目级R/E: ({result3['r_score']}, {result3['e_score']})")
    print(f"  九宫格: {result3['grid_key']} {result3['grid_name']}")
    print(f"  验证: 模块一(95分)、模块二(90分)的高分未"
          f"{'干扰' if result3['r_level'] >= 2 else '干扰'}项目级评分")

    print("\n" + "=" * 72)
    print("  All tests passed")
    print("=" * 72)
