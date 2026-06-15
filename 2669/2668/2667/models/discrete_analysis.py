"""
离散化分析引擎：风险-收益九宫格决策矩阵
将11个模型的多维输出压缩为 R(风险) × E(收益) 两个离散维度，
支持项目级/城市级/分公司级三层聚合。

Usage:
  from models.discrete_analysis import DiscreteAnalyzer
  analyzer = DiscreteAnalyzer(config)
  grid_df, summary = analyzer.run(all_results, dmp_df, appendix_df)
"""
import json
import os
import re
from typing import Any

import pandas as pd

from utils.helpers import safe_float


class DiscreteAnalyzer:
    """风险-收益离散化分析器，零新增数据依赖，全部复用现有模型输出."""

    def __init__(self, config: dict):
        # Support both: full rules.json (auto-load discrete section) or discrete_rules.json directly
        rules = config.get("离散化分析", {})
        if not rules:
            # Try loading discrete_rules.json separately
            disc_path = os.path.join(os.path.dirname(__file__), "..", "config", "discrete_rules.json")
            if os.path.exists(disc_path):
                with open(disc_path, "r", encoding="utf-8") as f:
                    disc_config = json.load(f)
                rules = disc_config.get("离散化分析", {})
        self.risk_cfg = rules.get("风险维度", {})
        self.return_cfg = rules.get("收益维度", {})
        self.grid_strategies = rules.get("九宫格处置策略", {})
        self.city_thresholds = rules.get("城市聚合", {})

        self.risk_weights = self.risk_cfg.get("权重", {})
        self.return_weights = self.return_cfg.get("权重", {})

        self.risk_cut_low = self.risk_cfg.get("分箱阈值", {}).get("低风险上限", 1.6)
        self.risk_cut_high = self.risk_cfg.get("分箱阈值", {}).get("中风险上限", 2.4)
        self.return_cut_low = self.return_cfg.get("分箱阈值", {}).get("低收益上限", 1.6)
        self.return_cut_high = self.return_cfg.get("分箱阈值", {}).get("中收益上限", 2.4)

        # 制度底线（从盈利底线配置取手册0520值）
        inst = config.get("institutional", {})
        profit_cfg = inst.get("盈利底线", {})
        profit_new = profit_cfg.get("手册0520", profit_cfg)
        self.profit_floor = profit_new.get("承接效益率_严禁投标_上限", 0.0)

    # ── public API ────────────────────────────────────────────

    def run(self, all_results: dict, dmp_df: pd.DataFrame,
            appendix_df: pd.DataFrame = None, region_auth: dict = None) -> dict:
        """
        主入口：对全量项目执行离散化分析。

        Returns:
          {
            "projects": DataFrame,       # 项目级结果
            "cities": DataFrame,          # 城市级聚合
            "subsidiaries": DataFrame,    # 分公司级聚合
            "summary": dict,              # 全局摘要
            "grid_distribution": dict,    # 九宫格分布计数
          }
        """
        # 1. 项目级离散
        proj_df = self._analyze_projects(all_results, dmp_df, appendix_df, region_auth)

        # 2. 城市级聚合
        city_df = self._aggregate_by_city(proj_df)

        # 3. 分公司级聚合
        sub_df = self._aggregate_by_subsidiary(proj_df)

        # 4. 全局摘要
        summary = self._global_summary(proj_df)

        return {
            "projects": proj_df,
            "cities": city_df,
            "subsidiaries": sub_df,
            "summary": summary,
            "grid_distribution": self._grid_counts(proj_df),
        }

    # ── project-level ─────────────────────────────────────────

    def _analyze_projects(self, all_results: dict, dmp_df: pd.DataFrame,
                          appendix_df: pd.DataFrame = None,
                          region_auth: dict = None) -> pd.DataFrame:
        """为每个项目计算 R, E, 九宫格定位."""
        rows = []
        for idx, row in dmp_df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            customer = str(row.get("客户名称", ""))
            unit = str(row.get("申报单位", ""))
            city = str(row.get("项目地址", ""))
            contract_amt = safe_float(row.get("签约额（元）", 0))

            # ── 风险维度离散化 ──
            r_region = self._region_risk(row, all_results, region_auth)
            r_contract = self._contract_risk(proj_code, all_results)
            r_customer = self._customer_risk(proj_code, customer, row, all_results)
            r_capital = self._capital_risk(proj_code, all_results)
            r_perf = self._performance_risk(proj_code, all_results, row, appendix_df)

            R_raw = (r_region * self.risk_weights.get("区域合规", 1.0) +
                     r_contract * self.risk_weights.get("合同底线", 1.0) +
                     r_customer * self.risk_weights.get("客户健康", 0.8) +
                     r_capital * self.risk_weights.get("资金安全", 0.8) +
                     r_perf * self.risk_weights.get("履约真实", 0.4))
            # 归一化到 [1.0, 3.0]：除以权重总和（min=1*4.0=4.0, max=3*4.0=12.0 → /4.0 → [1,3]）
            total_weight = sum(self.risk_weights.values())
            R = R_raw / total_weight if total_weight > 0 else R_raw
            R = max(1.0, min(3.0, R))
            r_level = 1 if R <= self.risk_cut_low else (2 if R <= self.risk_cut_high else 3)

            # ── 收益维度离散化 ──
            e_profit = self._profit_level(row)
            e_conversion = self._conversion_level(row, appendix_df)
            e_collection = self._collection_level(row, appendix_df)
            e_scale = self._scale_level(contract_amt)

            E_raw = (e_profit * self.return_weights.get("盈利水平", 1.05) +
                     e_conversion * self.return_weights.get("产值转化", 0.75) +
                     e_collection * self.return_weights.get("资金回收", 0.75) +
                     e_scale * self.return_weights.get("合同规模", 0.45))
            total_return_weight = sum(self.return_weights.values())
            E = E_raw / total_return_weight if total_return_weight > 0 else E_raw
            E = max(1.0, min(3.0, E))
            e_level = 1 if E <= self.return_cut_low else (2 if E <= self.return_cut_high else 3)

            # ── 九宫格 ──
            grid_key = f"({r_level},{e_level})"
            grid_info = self.grid_strategies.get(grid_key, {})
            grid_name = grid_info.get("名称", "未分类")
            grid_strategy = grid_info.get("策略", "")
            grid_phrase = grid_info.get("领导用语", "")

            rows.append({
                "项目编码": proj_code,
                "项目名称": proj_name,
                "申报单位": unit,
                "项目城市": city[:30] if city else "",
                "客户名称": customer[:30] if customer else "",
                "签约额（元）": contract_amt,
                "R_得分": round(R, 2),
                "R_等级": r_level,
                "R_区域合规": r_region,
                "R_合同底线": r_contract,
                "R_客户健康": r_customer,
                "R_资金安全": r_capital,
                "R_履约真实": r_perf,
                "E_得分": round(E, 2),
                "E_等级": e_level,
                "E_盈利水平": e_profit,
                "E_产值转化": e_conversion,
                "E_资金回收": e_collection,
                "E_合同规模": e_scale,
                "九宫格": grid_name,
                "处置策略": grid_strategy,
                "领导用语": grid_phrase,
                "网格键": grid_key,
            })

        return pd.DataFrame(rows)

    # ── risk sub-dimensions ───────────────────────────────────

    def _region_risk(self, row, all_results: dict, region_auth: dict = None) -> int:
        """区域合规风险：1=深耕 2=重点/常规 3=非常规/窜区"""
        proj_code = str(row.get("项目编码", ""))
        # 检查模型1.1是否有窜区标记
        r11_df = all_results.get("1.1", (pd.DataFrame(), {}))[0]
        if len(r11_df) > 0 and "项目编码" in r11_df.columns:
            proj_issues = r11_df[r11_df["项目编码"].astype(str) == proj_code]
            if len(proj_issues) > 0:
                cats = proj_issues["问题分类"].astype(str)
                if cats.str.contains("窜区|跨区域违规", na=False).any():
                    return 3
        # 从区域认定判断
        city = str(row.get("项目地址", "")).strip()
        if not city or city in ("nan", ""):
            return 2
        if region_auth:
            unit = str(row.get("申报单位", "")).strip()
            unit_auth = region_auth.get(unit, {})
            if isinstance(unit_auth, dict):
                deep = unit_auth.get("深耕", [])
                key_reg = unit_auth.get("重点", [])
                if any(c in city for c in deep):
                    return 1
                if any(c in city for c in key_reg):
                    return 2
        return 2  # default: 重点/常规

    def _contract_risk(self, proj_code: str, all_results: dict) -> int:
        """合同底线风险：1=无触碰 2=限制投标 3=严禁投标"""
        r21_df = all_results.get("2.1", (pd.DataFrame(), {}))[0]
        if len(r21_df) == 0:
            return 1
        if "项目编码" not in r21_df.columns:
            return 1
        proj_issues = r21_df[r21_df["项目编码"].astype(str) == proj_code]
        if len(proj_issues) == 0:
            return 1
        severities = proj_issues["严重等级"].astype(str)
        if severities.str.contains("严禁投标|red", na=False).any():
            return 3
        if severities.str.contains("限制投标", na=False).any():
            return 2
        # yellow issues → still low risk (e.g. 付款条件标记校验不一致轻微)
        return 1

    def _customer_risk(self, proj_code: str, customer: str,
                       row, all_results: dict) -> int:
        """客户健康风险：1=战略/优质 2=普通 3=僵尸/流失"""
        # 先检查是否优质客户
        is_quality = str(row.get("是否优质客户", "")).strip() == "是"
        if is_quality:
            return 1
        # 检查模型3.1
        r31_df = all_results.get("3.1", (pd.DataFrame(), {}))[0]
        if len(r31_df) > 0 and "客户名称" in r31_df.columns:
            cust_issues = r31_df[r31_df["客户名称"].astype(str) == customer]
            if len(cust_issues) > 0:
                cats = cust_issues["问题分类"].astype(str)
                if cats.str.contains("僵尸|流失", na=False).any():
                    return 3
                return 2
        # 检查模型3.2
        r32_df = all_results.get("3.2", (pd.DataFrame(), {}))[0]
        if len(r32_df) > 0 and "客户名称" in r32_df.columns:
            cust_issues = r32_df[r32_df["客户名称"].astype(str) == customer]
            if len(cust_issues) > 0:
                return 2
        return 2  # default: 普通客户

    def _capital_risk(self, proj_code: str, all_results: dict) -> int:
        """资金安全风险：1=无逾期 2=逾期<90天 3=逾期≥90天或负流"""
        r23_df = all_results.get("2.3", (pd.DataFrame(), {}))[0]
        if len(r23_df) == 0:
            return 1
        if "项目编码" not in r23_df.columns:
            return 1
        proj_issues = r23_df[r23_df["项目编码"].astype(str) == proj_code]
        if len(proj_issues) == 0:
            return 1
        types = proj_issues["问题分类"].astype(str)
        if types.str.contains("资金负流", na=False).any():
            return 3
        # Check descriptions for overdue days >= 90
        descs = proj_issues["问题描述"].astype(str)
        for d in descs:
            m = re.search(r"逾期\s*(\d+)\s*天", d)
            if m and int(m.group(1)) >= 90:
                return 3
        if types.str.contains("保证金逾期|预收款逾期|延期回收|延期到账", na=False).any():
            return 2
        return 1

    def _performance_risk(self, proj_code: str, all_results: dict,
                          row, appendix_df=None) -> int:
        """履约真实风险：1=正常 2=转化率<50% 3=停工退场或<30%"""
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
        # Check from merged DMP row (appendix fields already merged by build_unified_projects)
        contract_amt = safe_float(row.get("签约额（元）", 0))
        actual_output = safe_float(row.get("实际完成产值", 0))
        if contract_amt > 0 and actual_output > 0:
            ratio = actual_output / contract_amt
            if ratio < 0.30:
                return 2
        return 1

    # ── return sub-dimensions ─────────────────────────────────

    def _profit_level(self, row) -> int:
        """盈利水平：1=A值<底线 2=达标但<均值 3=达标且≥均值"""
        a_val = safe_float(row.get("一次性经营效益率（%）（A值）", 0))
        if a_val <= self.profit_floor:
            return 1
        if a_val < 0.04:  # rough mean threshold
            return 2
        return 3

    def _conversion_level(self, row, appendix_df=None) -> int:
        """产值转化：1=<50% 2=50-80% 3=≥80%（字段已由build_unified_projects合并至DMP行）"""
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

    def _collection_level(self, row, appendix_df=None) -> int:
        """资金回收：1=<30% 2=30-60% 3=≥60%（字段已由build_unified_projects合并至DMP行）"""
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
    def _scale_level(contract_amt: float) -> int:
        """合同规模：1=<1亿 2=1-5亿 3=≥5亿"""
        if contract_amt >= 500_000_000:
            return 3
        if contract_amt >= 100_000_000:
            return 2
        return 1

    # ── aggregation ───────────────────────────────────────────

    def _aggregate_by_city(self, proj_df: pd.DataFrame) -> pd.DataFrame:
        """城市级聚合：风险-收益中心点 + 红黄绿标签."""
        if proj_df.empty or "项目城市" not in proj_df.columns:
            return pd.DataFrame()

        city_groups = proj_df.groupby("项目城市")
        rows = []
        red_threshold = self.city_thresholds.get("红色城市阈值", 0.50)
        yellow_threshold = self.city_thresholds.get("黄色城市阈值", 0.30)

        for city, group in city_groups:
            if not city or city in ("nan", ""):
                continue
            n = len(group)
            avg_r = group["R_得分"].mean()
            avg_e = group["E_得分"].mean()
            # Grid distribution
            grid_counts = group["九宫格"].value_counts().to_dict()
            elimination_pct = (grid_counts.get("淘汰区", 0) + grid_counts.get("整顿区", 0)) / n
            expansion_pct = (grid_counts.get("扩张区", 0) + grid_counts.get("培育区", 0)) / n
            total_contract = group["签约额（元）"].sum()

            if elimination_pct > red_threshold:
                label = "[RED] 红色城市（战略退出）"
            elif expansion_pct > (1 - yellow_threshold):
                label = "[GREEN] 绿色城市（加大投入）"
            else:
                label = "[YELLOW] 黄色城市（审慎维持）"

            rows.append({
                "城市": city,
                "项目数": n,
                "合同总额（亿元）": round(total_contract / 1e8, 2),
                "平均R": round(avg_r, 2),
                "平均E": round(avg_e, 2),
                "淘汰整顿区占比": round(elimination_pct, 2),
                "扩张培育区占比": round(expansion_pct, 2),
                "城市标签": label,
                "九宫格分布": str(grid_counts),
            })

        city_df = pd.DataFrame(rows)
        if not city_df.empty:
            city_df = city_df.sort_values("平均R")
        return city_df

    def _aggregate_by_subsidiary(self, proj_df: pd.DataFrame) -> pd.DataFrame:
        """分公司级聚合：对标排名 + 类型标签."""
        if proj_df.empty or "申报单位" not in proj_df.columns:
            return pd.DataFrame()

        unit_groups = proj_df.groupby("申报单位")
        rows = []
        for unit, group in unit_groups:
            n = len(group)
            avg_r = group["R_得分"].mean()
            avg_e = group["E_得分"].mean()
            grid_counts = group["九宫格"].value_counts().to_dict()
            total_contract = group["签约额（元）"].sum()
            expansion_pct = (grid_counts.get("扩张区", 0) + grid_counts.get("培育区", 0)) / n
            elimination_pct = (grid_counts.get("淘汰区", 0) + grid_counts.get("整顿区", 0)) / n

            if expansion_pct >= 0.30 and elimination_pct <= 0.10:
                unit_type = "优质型"
            elif elimination_pct >= 0.25:
                unit_type = "风险型"
            else:
                unit_type = "均衡型"

            rows.append({
                "申报单位": unit,
                "项目数": n,
                "合同总额（亿元）": round(total_contract / 1e8, 2),
                "平均R": round(avg_r, 2),
                "平均E": round(avg_e, 2),
                "扩张区占比": round(expansion_pct, 2),
                "淘汰区占比": round(elimination_pct, 2),
                "单位类型": unit_type,
            })

        sub_df = pd.DataFrame(rows)
        if not sub_df.empty:
            sub_df = sub_df.sort_values("平均R")
        return sub_df

    # ── summary ────────────────────────────────────────────────

    def _global_summary(self, proj_df: pd.DataFrame) -> dict:
        """全局摘要统计."""
        if proj_df.empty:
            return {"total_projects": 0}

        n = len(proj_df)
        grid_counts = proj_df["九宫格"].value_counts().to_dict()
        elimination_count = grid_counts.get("淘汰区", 0) + grid_counts.get("整顿区", 0)
        expansion_count = grid_counts.get("扩张区", 0) + grid_counts.get("培育区", 0)
        high_risk_count = len(proj_df[proj_df["R_等级"] == 3])
        total_contract = proj_df["签约额（元）"].sum()

        return {
            "total_projects": n,
            "total_contract_yi": round(total_contract / 1e8, 2),
            "high_risk_count": high_risk_count,
            "high_risk_pct": round(high_risk_count / n * 100, 1),
            "elimination_count": elimination_count,
            "expansion_count": expansion_count,
            "avg_R": round(proj_df["R_得分"].mean(), 2),
            "avg_E": round(proj_df["E_得分"].mean(), 2),
            "grid_distribution": grid_counts,
        }

    @staticmethod
    def _grid_counts(proj_df: pd.DataFrame) -> dict:
        """九宫格分布计数."""
        if proj_df.empty:
            return {}
        grid = proj_df.groupby(["R_等级", "E_等级"]).size().unstack(fill_value=0)
        result = {}
        for r in [1, 2, 3]:
            for e in [1, 2, 3]:
                result[f"({r},{e})"] = int(grid.loc[r, e]) if r in grid.index and e in grid.columns else 0
        return result


# ── convenience function for web / CLI ────────────────────────

def run_discrete_analysis(all_results: dict, dmp_df: pd.DataFrame,
                          appendix_df: pd.DataFrame = None,
                          region_auth: dict = None,
                          config_path: str = "config/rules.json") -> dict:
    """便捷入口：加载配置并执行离散化分析."""
    import json
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    analyzer = DiscreteAnalyzer(config)
    return analyzer.run(all_results, dmp_df, appendix_df, region_auth)
