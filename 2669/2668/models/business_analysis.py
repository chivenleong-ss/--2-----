"""
Business-health analysis layer.

This upgrades the audit output into an operating-posture view:
1. Quantified module scoring
2. Multi-dimensional cross analysis
3. Current-batch trend summaries
4. Dashboard-ready tables and recommendations
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from utils.helpers import safe_float


@dataclass
class ModuleScore:
    score: float
    metrics: dict


class BusinessHealthAnalyzer:
    """Operating-health analyzer using the current batch only."""

    def run(self, all_results: dict, dmp_df: pd.DataFrame) -> dict:
        if dmp_df is None or dmp_df.empty:
            return self._empty_result()

        df = self._prepare_dataframe(dmp_df)
        issue_index = self._build_issue_index(all_results)

        subsidiaries = self._build_scope_table(df, issue_index, "申报单位")
        cities = self._build_scope_table(df, issue_index, "_project_city_norm")
        overview = self._build_overview(df, issue_index)
        trends = self._build_trends(df)
        focus_projects = self._build_focus_projects(df, issue_index)
        recommendations = self._build_recommendations(overview, subsidiaries, cities, focus_projects)

        summary = {
            "total_projects": int(len(df)),
            "total_contract_yi": round(df["_contract_amt"].sum() / 1e8, 2),
            "covered_units": int(subsidiaries["名称"].nunique()) if not subsidiaries.empty else 0,
            "covered_cities": int(cities["名称"].nunique()) if not cities.empty else 0,
            "top_unit": subsidiaries.iloc[0]["名称"] if not subsidiaries.empty else "",
            "top_unit_score": round(float(subsidiaries.iloc[0]["综合得分"]), 1) if not subsidiaries.empty else 0.0,
            "high_risk_project_count": int(sum(1 for v in issue_index.values() if v["risk_score"] >= 6)),
            "strong_project_count": int((df["_actual_output"] > 0).sum()),
        }

        return {
            "summary": summary,
            "overview": overview,
            "subsidiaries": subsidiaries,
            "cities": cities,
            "trends": trends,
            "focus_projects": focus_projects,
            "recommendations": recommendations,
        }

    def _empty_result(self) -> dict:
        return {
            "summary": {"total_projects": 0, "total_contract_yi": 0.0},
            "overview": {},
            "subsidiaries": pd.DataFrame(),
            "cities": pd.DataFrame(),
            "trends": [],
            "focus_projects": [],
            "recommendations": [],
        }

    def _prepare_dataframe(self, dmp_df: pd.DataFrame) -> pd.DataFrame:
        df = dmp_df.copy()
        df["_project_code"] = df.get("项目编码", "").astype(str).str.strip()
        df["_contract_amt"] = df.get("签约额（元）", 0).apply(safe_float)
        df["_actual_output"] = df.get("实际完成产值", 0).apply(safe_float)
        df["_collection_amt"] = df.get("累计收款", 0).apply(safe_float)
        df["_customer_name"] = df.get("客户名称", "").astype(str).str.strip()
        df["_project_city_norm"] = df.get("项目城市", df.get("项目地址", "")).astype(str).str.strip()
        df["_sign_year"] = df.apply(self._extract_year, axis=1)
        df["_a_value"] = df.get("一次性经营效益率（%）（A值）", 0).apply(safe_float)
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

    def _build_issue_index(self, all_results: dict) -> dict:
        index = {}
        for model_id, payload in all_results.items():
            df = payload[0]
            if df is None or len(df) == 0 or "项目编码" not in df.columns:
                continue
            for _, row in df.iterrows():
                code = str(row.get("项目编码", "")).strip()
                if not code:
                    continue
                bucket = index.setdefault(
                    code,
                    {
                        "risk_score": 0,
                        "issue_count": 0,
                        "red_count": 0,
                        "yellow_count": 0,
                        "models": set(),
                        "categories": set(),
                    },
                )
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

    def _build_scope_table(self, df: pd.DataFrame, issue_index: dict, scope_col: str) -> pd.DataFrame:
        rows = []
        for scope_name, group in df.groupby(scope_col, dropna=False):
            scope_name = str(scope_name).strip()
            if not scope_name or scope_name == "nan":
                continue
            rows.append(self._analyze_scope(scope_name, group, issue_index))
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values(["综合得分", "签约额（亿元）"], ascending=[False, False]).reset_index(drop=True)

    def _analyze_scope(self, scope_name: str, group: pd.DataFrame, issue_index: dict) -> dict:
        project_codes = {code for code in group["_project_code"].tolist() if code}
        total_projects = len(group)
        total_contract = group["_contract_amt"].sum()
        customer_count = group["_customer_name"].replace("nan", "").loc[lambda s: s != ""].nunique()

        region = self._region_module(group, project_codes, issue_index, total_contract)
        customer = self._customer_module(group, project_codes, issue_index, total_contract, customer_count)
        contract = self._contract_module(group, project_codes, issue_index, total_contract)
        performance = self._performance_module(group, project_codes, issue_index, total_contract)
        capital = self._capital_module(group, project_codes, issue_index, total_contract)

        total_score = (
            region.score * 0.30
            + customer.score * 0.20
            + contract.score * 0.20
            + performance.score * 0.15
            + capital.score * 0.15
        )

        return {
            "名称": scope_name,
            "项目数": int(total_projects),
            "客户数": int(customer_count),
            "签约额（亿元）": round(total_contract / 1e8, 2),
            "区域得分": round(region.score, 1),
            "客户得分": round(customer.score, 1),
            "合同得分": round(contract.score, 1),
            "履约得分": round(performance.score, 1),
            "资金得分": round(capital.score, 1),
            "综合得分": round(total_score, 1),
            "区域渗透率": round(region.metrics["region_penetration_rate"] * 100, 1),
            "跨区域经营指数": round(region.metrics["cross_region_contract_ratio"] * 100, 1),
            "深耕区域集中度": round(region.metrics["deep_region_ratio"] * 100, 1),
            "客户集中度风险": round(customer.metrics["top5_customer_share"] * 100, 1),
            "战略客户产出比": round(customer.metrics["quality_customer_share"] * 100, 1),
            "客户风险占比": round(customer.metrics["customer_risk_ratio"] * 100, 1),
            "风险项目占比": round(contract.metrics["risk_project_ratio"] * 100, 1),
            "高风险合同额占比": round(contract.metrics["risk_contract_ratio"] * 100, 1),
            "产值转化率": round(performance.metrics["output_conversion_rate"] * 100, 1),
            "停工退场率": round(performance.metrics["stopped_project_ratio"] * 100, 1),
            "盈利健康度": round(performance.metrics["healthy_profit_ratio"] * 100, 1),
            "资金回收率": round(capital.metrics["collection_rate"] * 100, 1),
            "资金风险占比": round(capital.metrics["capital_risk_ratio"] * 100, 1),
            "经营诊断": self._diagnose(total_score, region, customer, contract, performance, capital),
        }

    def _region_module(self, group: pd.DataFrame, project_codes: set[str], issue_index: dict, total_contract: float) -> ModuleScore:
        actual_cities = {v for v in group["_project_city_norm"].tolist() if v and v != "nan"}
        authorized_cities = set()
        if "授权城市" in group.columns:
            for val in group["授权城市"].dropna():
                authorized_cities.update(city.strip() for city in str(val).split(",") if city.strip() and city.strip() != "nan")
        if "核心城市" in group.columns:
            deep_cities = {
                city.strip()
                for val in group["核心城市"].dropna()
                for city in str(val).split(",")
                if city.strip() and city.strip() != "nan"
            }
        else:
            deep_cities = set()

        penetration = min(len(actual_cities) / max(len(authorized_cities), 1), 1.0) if actual_cities else 0.0
        deep_ratio = len(actual_cities & deep_cities) / max(len(actual_cities), 1) if actual_cities else 0.0
        cross_region_amt = self._issue_amount_for_projects(group, project_codes, issue_index, {"1.1"})
        cross_region_ratio = cross_region_amt / total_contract if total_contract > 0 else 0.0

        score = (penetration * 0.40 + deep_ratio * 0.25 + (1 - min(cross_region_ratio, 1.0)) * 0.35) * 100
        return ModuleScore(
            score=max(0.0, min(100.0, score)),
            metrics={
                "region_penetration_rate": penetration,
                "deep_region_ratio": deep_ratio,
                "cross_region_contract_ratio": cross_region_ratio,
            },
        )

    def _customer_module(self, group: pd.DataFrame, project_codes: set[str], issue_index: dict, total_contract: float, customer_count: int) -> ModuleScore:
        cust_amt = group.groupby("_customer_name")["_contract_amt"].sum().sort_values(ascending=False) if "_customer_name" in group.columns else pd.Series(dtype=float)
        top5_share = cust_amt.head(5).sum() / total_contract if total_contract > 0 and not cust_amt.empty else 0.0
        quality_mask = group.get("是否优质客户", pd.Series(index=group.index, dtype=str)).astype(str).str.strip() == "是"
        quality_amt = group.loc[quality_mask, "_contract_amt"].sum() if len(group) else 0.0
        quality_share = quality_amt / total_contract if total_contract > 0 else 0.0

        customer_issue_projects = {code for code in project_codes if issue_index.get(code, {}).get("models", set()) & {"3.1", "3.2"}}
        customer_risk_ratio = len(customer_issue_projects) / max(len(project_codes), 1)

        score = ((1 - min(top5_share, 1.0)) * 0.40 + min(quality_share, 1.0) * 0.35 + (1 - min(customer_risk_ratio, 1.0)) * 0.25) * 100
        return ModuleScore(
            score=max(0.0, min(100.0, score)),
            metrics={
                "top5_customer_share": top5_share,
                "quality_customer_share": quality_share,
                "customer_risk_ratio": customer_risk_ratio,
                "customer_count": customer_count,
            },
        )

    def _contract_module(self, group: pd.DataFrame, project_codes: set[str], issue_index: dict, total_contract: float) -> ModuleScore:
        risk_projects = {code for code in project_codes if issue_index.get(code, {}).get("models", set()) & {"2.1", "2.4"}}
        risk_project_ratio = len(risk_projects) / max(len(project_codes), 1)
        risk_amt = group[group["_project_code"].isin(risk_projects)]["_contract_amt"].sum()
        risk_contract_ratio = risk_amt / total_contract if total_contract > 0 else 0.0

        score = ((1 - min(risk_project_ratio, 1.0)) * 0.55 + (1 - min(risk_contract_ratio, 1.0)) * 0.45) * 100
        return ModuleScore(
            score=max(0.0, min(100.0, score)),
            metrics={"risk_project_ratio": risk_project_ratio, "risk_contract_ratio": risk_contract_ratio},
        )

    def _performance_module(self, group: pd.DataFrame, project_codes: set[str], issue_index: dict, total_contract: float) -> ModuleScore:
        output_conversion = group["_actual_output"].sum() / total_contract if total_contract > 0 else 0.0
        stopped_projects = {code for code in project_codes if issue_index.get(code, {}).get("models", set()) & {"2.5"}}
        stopped_ratio = len(stopped_projects) / max(len(project_codes), 1)
        healthy_profit_ratio = (group["_a_value"] > 0).sum() / max(len(group), 1)

        score = (min(output_conversion, 1.0) * 0.50 + (1 - min(stopped_ratio, 1.0)) * 0.30 + min(healthy_profit_ratio, 1.0) * 0.20) * 100
        return ModuleScore(
            score=max(0.0, min(100.0, score)),
            metrics={
                "output_conversion_rate": output_conversion,
                "stopped_project_ratio": stopped_ratio,
                "healthy_profit_ratio": healthy_profit_ratio,
            },
        )

    def _capital_module(self, group: pd.DataFrame, project_codes: set[str], issue_index: dict, total_contract: float) -> ModuleScore:
        collection_rate = group["_collection_amt"].sum() / total_contract if total_contract > 0 else 0.0
        capital_projects = {code for code in project_codes if issue_index.get(code, {}).get("models", set()) & {"2.3"}}
        capital_risk_ratio = len(capital_projects) / max(len(project_codes), 1)
        score = (min(collection_rate, 1.0) * 0.60 + (1 - min(capital_risk_ratio, 1.0)) * 0.40) * 100
        return ModuleScore(
            score=max(0.0, min(100.0, score)),
            metrics={"collection_rate": collection_rate, "capital_risk_ratio": capital_risk_ratio},
        )

    def _build_overview(self, df: pd.DataFrame, issue_index: dict) -> dict:
        total_contract = df["_contract_amt"].sum()
        project_codes = set(df["_project_code"].tolist())
        region = self._region_module(df, project_codes, issue_index, total_contract)
        customer = self._customer_module(df, project_codes, issue_index, total_contract, df["_customer_name"].nunique())
        contract = self._contract_module(df, project_codes, issue_index, total_contract)
        performance = self._performance_module(df, project_codes, issue_index, total_contract)
        capital = self._capital_module(df, project_codes, issue_index, total_contract)

        return {
            "module_scores": {
                "区域布局": round(region.score, 1),
                "客户稳定": round(customer.score, 1),
                "合同质量": round(contract.score, 1),
                "履约盈利": round(performance.score, 1),
                "资金效率": round(capital.score, 1),
            },
            "kpis": {
                "区域渗透率": {"value": round(region.metrics["region_penetration_rate"] * 100, 1), "formula": "落地城市数 ÷ 授权深耕+重点城市数"},
                "跨区域经营指数": {"value": round(region.metrics["cross_region_contract_ratio"] * 100, 1), "formula": "非常规区域合同额 ÷ 总合同额"},
                "战略客户产出比": {"value": round(customer.metrics["quality_customer_share"] * 100, 1), "formula": "战略+优质客户合同额 ÷ 总合同额"},
                "风险项目占比": {"value": round(contract.metrics["risk_project_ratio"] * 100, 1), "formula": "触碰红线/限制投标项目数 ÷ 总项目数"},
                "产值转化率": {"value": round(performance.metrics["output_conversion_rate"] * 100, 1), "formula": "实际完成产值 ÷ 签约额"},
                "资金回收率": {"value": round(capital.metrics["collection_rate"] * 100, 1), "formula": "累计收款 ÷ 签约额"},
            },
            "score_band": self._score_band_counts(df, issue_index),
        }

    def _score_band_counts(self, df: pd.DataFrame, issue_index: dict) -> dict:
        buckets = {"强势区": 0, "稳健区": 0, "承压区": 0}
        scope = self._build_scope_table(df, issue_index, "_project_code")
        if scope.empty:
            return buckets
        for score in scope["综合得分"].tolist():
            if score >= 80:
                buckets["强势区"] += 1
            elif score >= 65:
                buckets["稳健区"] += 1
            else:
                buckets["承压区"] += 1
        return buckets

    def _build_trends(self, df: pd.DataFrame) -> list[dict]:
        if "_sign_year" not in df.columns:
            return []
        valid = df[df["_sign_year"] > 0].copy()
        if valid.empty:
            return []
        rows = []
        for year, group in valid.groupby("_sign_year"):
            contract = group["_contract_amt"].sum()
            output = group["_actual_output"].sum()
            collection = group["_collection_amt"].sum()
            rows.append(
                {
                    "year": int(year),
                    "project_count": int(len(group)),
                    "contract_yi": round(contract / 1e8, 2),
                    "output_conversion_rate": round((output / contract * 100) if contract > 0 else 0.0, 1),
                    "collection_rate": round((collection / contract * 100) if contract > 0 else 0.0, 1),
                }
            )
        return sorted(rows, key=lambda x: x["year"])

    def _build_focus_projects(self, df: pd.DataFrame, issue_index: dict) -> list[dict]:
        rows = []
        for _, row in df.iterrows():
            code = row["_project_code"]
            if not code:
                continue
            issue = issue_index.get(code)
            if not issue:
                continue
            rows.append(
                {
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
                }
            )
        rows.sort(key=lambda x: (-x["risk_score"], -x["contract_yi"], -x["red_count"]))
        return rows[:10]

    def _build_recommendations(self, overview: dict, subsidiaries: pd.DataFrame, cities: pd.DataFrame, focus_projects: list[dict]) -> list[dict]:
        module_scores = overview.get("module_scores", {})
        weak_module = min(module_scores, key=module_scores.get) if module_scores else ""
        recs = []
        if weak_module:
            recs.append(
                {
                    "title": f"{weak_module}模块优先修复",
                    "detail": f"{weak_module}当前为五大模块最低分，建议作为本轮经营提升的首要抓手。",
                    "level": "high",
                }
            )
        if not subsidiaries.empty:
            weakest_units = subsidiaries.sort_values("综合得分").head(3)["名称"].tolist()
            recs.append(
                {
                    "title": "二级单位分层治理",
                    "detail": f"建议优先跟踪低分单位：{'、'.join(weakest_units)}。",
                    "level": "medium",
                }
            )
        if not cities.empty:
            risky_cities = cities.sort_values("综合得分").head(3)["名称"].tolist()
            recs.append(
                {
                    "title": "城市布局优化",
                    "detail": f"建议对承压城市开展专项诊断：{'、'.join(risky_cities)}。",
                    "level": "medium",
                }
            )
        if focus_projects:
            names = [item["project_name"] for item in focus_projects[:3] if item["project_name"]]
            recs.append(
                {
                    "title": "重点项目盯控",
                    "detail": f"高风险高体量项目建议一案一策：{'、'.join(names)}。",
                    "level": "high",
                }
            )
        return recs

    def _diagnose(self, total_score: float, *modules: ModuleScore) -> str:
        score_map = {
            "区域": modules[0].score,
            "客户": modules[1].score,
            "合同": modules[2].score,
            "履约": modules[3].score,
            "资金": modules[4].score,
        }
        weakest = min(score_map, key=score_map.get)
        if total_score >= 80:
            return f"整体经营健康，继续巩固{weakest}短板即可。"
        if total_score >= 65:
            return f"整体可控，但{weakest}模块偏弱，建议专项提升。"
        return f"{weakest}模块拖累明显，建议列入重点经营诊断清单。"

    def _issue_amount_for_projects(self, group: pd.DataFrame, project_codes: set[str], issue_index: dict, model_ids: set[str]) -> float:
        selected = {code for code in project_codes if issue_index.get(code, {}).get("models", set()) & model_ids}
        return group[group["_project_code"].isin(selected)]["_contract_amt"].sum()
