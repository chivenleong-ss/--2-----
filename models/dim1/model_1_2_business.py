"""
Model 1.2: Business Structure Strategic Deviation (业务结构战略性偏差检测).
Checks actual business mix against 十五五 strategic targets across three dimensions:
  1. 业务结构目标 — revenue share per segment vs annual targets (Table 3)
  2. 1236框架 — EPC转型 / 城市更新 / 新兴板块 growth path alignment
  3. 区域发展目标 — regional revenue distribution vs targets (Table 4)

Also retains original checks: infra subsidiary <70%, real estate >40%.
"""
import pandas as pd
from datetime import datetime
from models.base_model import BaseModel
from utils.helpers import safe_float
from utils.strategic_scope import detect_strategic_scope


class Model12Business(BaseModel):
    model_id = "1.2"
    model_name = "业务结构战略性偏差检测（含十五五战略对齐）"
    priority = "P2"
    dimension = "战略与布局"

    # Province → 大区 mapping per 十五五 Table 4
    PROVINCE_TO_REGION = {
        "广东": "华南大区", "广西": "华南大区", "海南": "华南大区",
        "上海": "华东大区", "江苏": "华东大区", "浙江": "华东大区",
        "福建": "华东大区", "安徽": "华东大区", "山东": "华东大区", "江西": "华东大区",
        "贵州": "西部大区", "四川": "西部大区", "重庆": "西部大区",
        "云南": "西部大区", "西藏": "西部大区", "陕西": "西部大区",
        "甘肃": "西部大区", "新疆": "西部大区",
        "北京": "多点区域", "山西": "多点区域", "河北": "多点区域",
        "湖北": "多点区域", "河南": "多点区域",
    }

    @staticmethod
    def _extract_year(row) -> int:
        for col in ["签约时间", "中标时间", "签约报量时间"]:
            val = row.get(col)
            if pd.notna(val):
                try:
                    if hasattr(val, "year"):
                        return val.year
                    return pd.Timestamp(val).year
                except Exception:
                    pass
        year_str = str(row.get("合同签订年度", ""))
        import re
        m = re.search(r"(\d{4})", year_str)
        if m:
            return int(m.group(1))
        return datetime.now().year

    @staticmethod
    def _extract_province(address: str) -> str:
        s = str(address).strip()
        if not s:
            return ""
        for ar in ["广西壮族自治区", "内蒙古自治区", "西藏自治区",
                    "宁夏回族自治区", "新疆维吾尔自治区"]:
            if ar in s:
                return ar
        if "省" in s:
            return s.split("省")[0].strip() + "省"
        for city in ["北京市", "上海市", "天津市", "重庆市"]:
            if s.startswith(city) or city in s:
                return city
        return ""

    def _classify_segment(self, row) -> str:
        """Classify a project into 十五五 business segment."""
        biz_type = str(row.get("业务类型", ""))
        eng_cat = str(row.get("工程类别", ""))
        eng_cat_old = str(row.get("工程类别（原总公司市场口径）", ""))
        is_re = str(row.get("是否地产类项目", "")).strip() == "是"
        is_urban = str(row.get("是否城市更新", "")).strip() == "是"

        combined = f"{biz_type} {eng_cat} {eng_cat_old}"

        # 城市更新与运营 (key growth point 1)
        urban_kw = ["城市更新", "老旧改造", "城中村", "老旧小区",
                     "既有建筑", "片区运营", "城市功能", "完整社区"]
        if is_urban or any(kw in combined for kw in urban_kw):
            return "城市更新与运营"

        # 新兴业务 (key growth point 2)
        emerging_kw = ["建筑工业化", "装配式", "模块化", "MiC", "CF-MiC",
                       "PC构件", "智能装备", "绿色建材", "低碳"]
        if any(kw in combined for kw in emerging_kw):
            return "新兴业务"

        # 建造板块 sub-classification
        infra_kw = ["基础设施", "市政", "公路", "轨道交通", "水利",
                     "水务", "能源", "机场", "港口"]
        if any(kw in combined for kw in infra_kw):
            return "基础设施"

        prof_kw = ["安装", "装饰", "机电", "钢结构", "幕墙", "园林",
                    "专业工程", "消防", "智能化", "弱电"]
        if any(kw in combined for kw in prof_kw):
            return "专业工程"

        # Default: 房屋建筑 (core of 建造板块)
        return "房屋建筑"

    def _classify_region(self, row) -> str:
        """Classify a project into 十五五 region."""
        province = self._extract_province(str(row.get("项目地址", "")))
        # Try short name match
        for full_prov, region in self.PROVINCE_TO_REGION.items():
            if full_prov in province or province in full_prov:
                return region
        return "多点区域"

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        df = dmp.copy()
        config_15 = self.config.get("十五五战略规划", {})
        strategic_scope = detect_strategic_scope(df, self.config)
        struct_targets = strategic_scope.get("target_dict") or config_15.get("局级全局基准", config_15.get("业务结构目标", {}))
        framework_1236 = config_15.get("1236框架", {})
        region_targets = config_15.get("区域发展目标", {})
        tolerance = strategic_scope.get("tolerance", config_15.get("偏差容忍度", 0.05))
        not_applicable = set(strategic_scope.get("not_applicable") or [])
        region_tolerance = region_targets.get("偏差容忍度", 0.05)

        findings = []
        logger.log_check(
            "战略规划口径自动探测",
            True,
            {
                "scope_name": strategic_scope.get("scope_name"),
                "matched_unit": strategic_scope.get("matched_unit"),
                "fallback_reason": strategic_scope.get("fallback_reason", ""),
            },
        )

        # Derive signing year for each project
        df["_sign_year"] = df.apply(self._extract_year, axis=1)
        # Clamp to config range
        df["_sign_year"] = df["_sign_year"].clip(2025, 2030)

        # ==================================================================
        # 一、业务结构目标偏离检测（十五五 Table 3）
        # ==================================================================
        if "签约额（元）" in df.columns:
            df["_segment"] = df.apply(self._classify_segment, axis=1)
            total_amt = df["签约额（元）"].apply(safe_float).sum()
            if total_amt > 0:
                # Per-segment actual vs target comparison
                for seg in ["房屋建筑", "基础设施", "专业工程",
                            "海外业务", "城市更新与运营", "新兴业务"]:
                    if seg in not_applicable:
                        continue
                    seg_amt = df[df["_segment"] == seg]["签约额（元）"].apply(safe_float).sum()
                    actual_pct = seg_amt / total_amt

                    # Get target for most recent year in data
                    years_in_data = sorted(df["_sign_year"].unique())
                    if not years_in_data:
                        continue
                    ref_year = max(years_in_data)
                    ref_year_str = str(int(ref_year))
                    target_dict = struct_targets.get(seg, {})
                    target_pct = target_dict.get(ref_year_str, None)

                    if isinstance(target_pct, (int, float)) and target_pct > 0:
                        deviation = abs(actual_pct - target_pct)
                        if deviation > tolerance:
                            direction = "偏高" if actual_pct > target_pct else "偏低"
                            findings.append({
                                "模型编号": "1.2",
                                "战略规划基准": strategic_scope.get("scope_name"),
                                "申报单位": strategic_scope.get("matched_unit") or "全局",
                                "问题分类": "业务结构目标偏离",
                                "严重等级": "red" if deviation > 0.10 else "yellow",
                                "问题描述": (
                                    f"【{strategic_scope.get('scope_name')}】{seg}实际占比{actual_pct:.1%}，"
                                    f"{ref_year}年目标{target_pct:.0%}，{direction}{deviation:.1%}"
                                ),
                                "当前占比": actual_pct,
                                "目标占比": target_pct,
                                "目标年度": int(ref_year),
                            })

        # ==================================================================
        # 二、1236框架 — EPC转型检测
        # ==================================================================
        epc_target = framework_1236.get("一个基本盘_建造板块", {}).get("EPC项目模式占比目标", 0.50)
        mode_col = "项目模式类"
        if mode_col in df.columns and "签约额（元）" in df.columns:
            epc_kw = ["EPC", "工程总承包", "设计施工总承包", "设计采购施工"]
            epc_df = df[df[mode_col].astype(str).str.contains("|".join(epc_kw), na=False)]
            epc_amt = epc_df["签约额（元）"].apply(safe_float).sum()
            total_amt = df["签约额（元）"].apply(safe_float).sum()
            if total_amt > 0:
                epc_ratio = epc_amt / total_amt
                if epc_ratio < epc_target:
                    findings.append({
                        "模型编号": "1.2",
                        "问题分类": "EPC转型滞后",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"【1236框架·基本盘】EPC/工程总承包项目签约额占比{epc_ratio:.0%}，"
                            f"未达目标{epc_target:.0%}，EPC转型推进需加速"
                        ),
                        "当前占比": epc_ratio,
                        "EPC目标": epc_target,
                    })

        # ==================================================================
        # 三、1236框架 — 城市更新与运营增长点检测
        # ==================================================================
        urban_cfg = framework_1236.get("两个增长点", {}).get("城市更新与运营", {})
        urban_kw = urban_cfg.get("项目识别关键字", ["城市更新"])
        if "签约额（元）" in df.columns:
            # Check if any project matches 城市更新 keywords
            urban_mask = pd.Series(False, index=df.index)
            for col in ["业务类型", "工程类别", "项目分类", "是否城市更新"]:
                if col in df.columns:
                    urban_mask |= df[col].astype(str).apply(
                        lambda x: any(kw in x for kw in urban_kw) if pd.notna(x) else False
                    )
            urban_count = urban_mask.sum()
            total_count = len(df)
            if total_count > 0 and urban_count == 0:
                findings.append({
                    "模型编号": "1.2",
                    "问题分类": "城市更新业务空白",
                    "严重等级": "yellow",
                    "问题描述": (
                        f"【1236框架·增长点】全局{total_count}个项目中未识别到城市更新与运营类项目，"
                        "十五五目标占比20%，需加速布局"
                    ),
                })

        # ==================================================================
        # 四、1236框架 — 新兴业务增长点检测
        # ==================================================================
        emerging_cfg = framework_1236.get("两个增长点", {}).get("新兴板块", {})
        emerging_kw = emerging_cfg.get("项目识别关键字", ["建筑工业化"])
        if "签约额（元）" in df.columns:
            emerging_mask = pd.Series(False, index=df.index)
            for col in ["业务类型", "工程类别", "项目分类"]:
                if col in df.columns:
                    emerging_mask |= df[col].astype(str).apply(
                        lambda x: any(kw in x for kw in emerging_kw) if pd.notna(x) else False
                    )
            emerging_count = emerging_mask.sum()
            emerging_mask = pd.Series(False, index=df.index)
            for col in ["业务类型", "工程类别", "项目分类"]:
                if col in df.columns:
                    emerging_mask |= df[col].astype(str).apply(
                        lambda x: any(kw in x for kw in emerging_kw) if pd.notna(x) else False
                    )
            emerging_amt = df.loc[emerging_mask, "签约额（元）"].apply(safe_float).sum()
            total_amt = df["签约额（元）"].apply(safe_float).sum()
            if total_amt > 0:
                emerging_pct = emerging_amt / total_amt
                target_2030 = struct_targets.get("新兴业务", {}).get("2030", 0.10)
                if emerging_pct < 0.01:
                    findings.append({
                        "模型编号": "1.2",
                        "问题分类": "新兴业务拓展不足",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"【1236框架·增长点】新兴业务（建筑工业化等）签约额占比仅{emerging_pct:.1%}，"
                            f"十五五目标2030年达{target_2030:.0%}，新质生产力培育需加速"
                        ),
                    })

        # ==================================================================
        # 五、区域发展目标偏离检测（十五五 Table 4）
        # ==================================================================
        if "项目地址" in df.columns and "签约额（元）" in df.columns:
            df["_region"] = df.apply(self._classify_region, axis=1)
            region_amt = df.groupby("_region")["签约额（元）"].apply(lambda x: x.apply(safe_float).sum())
            total = region_amt.sum()
            if total > 0:
                years_in_data = sorted(df["_sign_year"].unique())
                ref_year = str(int(max(years_in_data))) if years_in_data else "2026"

                for reg in ["华南大区", "华东大区", "西部大区", "多点区域"]:
                    actual = region_amt.get(reg, 0) / total
                    target_dict = region_targets.get(reg, {})
                    target = target_dict.get(ref_year, None)
                    if target is not None and target > 0:
                        dev = abs(actual - target)
                        if dev > region_tolerance:
                            direction = "偏高" if actual > target else "偏低"
                            findings.append({
                                "模型编号": "1.2",
                                "问题分类": "区域发展目标偏离",
                                "严重等级": "yellow",
                                "问题描述": (
                                    f"【区域发展目标】{reg}实际营收占比{actual:.1%}，"
                                    f"{ref_year}年目标{target:.0%}，{direction}{dev:.1%}"
                                ),
                                "当前占比": actual,
                                "目标占比": target,
                            })

        # ==================================================================
        # 六、原有校验：基础设施分公司专业定位 + 地产依赖
        # ==================================================================
        if "申报单位" in df.columns and "签约额（元）" in df.columns:
            for unit, group in df.groupby("申报单位"):
                total = group["签约额（元）"].apply(safe_float).sum()
                if total == 0:
                    continue

                if "基础设施" in str(unit):
                    if "_segment" not in group.columns:
                        group = group.copy()
                        group["_segment"] = group.apply(self._classify_segment, axis=1)
                    infra_amt = group[group["_segment"] == "基础设施"]["签约额（元）"].apply(safe_float).sum()
                    infra_pct = infra_amt / total if total > 0 else 0
                    if infra_pct < 0.70:
                        findings.append({
                            "模型编号": "1.2",
                            "申报单位": unit,
                            "问题分类": "专业定位偏离",
                            "严重等级": "yellow",
                            "问题描述": f"{unit}基础设施合同额占比{infra_pct:.1%} < 70%",
                            "当前占比": infra_pct,
                        })

                re_col = "是否地产类项目"
                if re_col in group.columns:
                    re_amt = group[group[re_col].astype(str).str.strip() == "是"]["签约额（元）"].apply(safe_float).sum()
                    re_pct = re_amt / total if total > 0 else 0
                    if re_pct > 0.40:
                        findings.append({
                            "模型编号": "1.2",
                            "申报单位": unit,
                            "问题分类": "地产依赖度过高",
                            "严重等级": "yellow",
                            "问题描述": f"{unit}地产类合同额占比{re_pct:.1%} > 40%",
                            "当前占比": re_pct,
                        })

        # Cleanup temp columns
        df = df.drop(columns=["_sign_year", "_segment", "_region"], errors="ignore")

        # ==================================================================
        # 七、各单位业务结构统计（仅展示，不做预警）
        # ==================================================================
        unit_breakdown = {}
        if "申报单位" in df.columns and "签约额（元）" in df.columns:
            df["_segment"] = df.apply(self._classify_segment, axis=1)
            for unit, group in df.groupby("申报单位"):
                unit_total = group["签约额（元）"].apply(safe_float).sum()
                if unit_total == 0:
                    continue
                seg_pcts = {}
                for seg in ["房屋建筑", "基础设施", "专业工程", "城市更新与运营", "新兴业务"]:
                    seg_amt = group[group["_segment"] == seg]["签约额（元）"].apply(safe_float).sum()
                    seg_pcts[seg] = round(seg_amt / unit_total, 4)
                unit_breakdown[unit] = {
                    "total_amt": unit_total,
                    "segments": seg_pcts,
                    "project_count": len(group),
                }
            df = df.drop(columns=["_segment"], errors="ignore")

        issues_df = pd.DataFrame(findings)
        if len(issues_df) > 0:
            issues_df = issues_df.sort_values("严重等级")

        def _count(cat):
            return len(issues_df[issues_df["问题分类"].str.contains(cat, na=False)]) if len(issues_df) > 0 else 0

        summary = {
            "total_units": df["申报单位"].nunique() if "申报单位" in df.columns else 0,
            "total_projects": len(df),
            "业务结构目标偏离": _count("业务结构目标偏离"),
            "EPC转型": _count("EPC转型"),
                "城市更新": _count("城市更新"),
                "新兴业务": _count("新兴业务"),
                "区域目标偏离": _count("区域发展目标偏离"),
                "专业定位偏离": _count("专业定位偏离"),
                "地产依赖": _count("地产依赖"),
                "total_issues": len(issues_df),
                "unit_breakdown": unit_breakdown,
                "strategic_scope": {
                    "scope_type": strategic_scope.get("scope_type"),
                    "scope_name": strategic_scope.get("scope_name"),
                    "matched_unit": strategic_scope.get("matched_unit"),
                    "fallback_reason": strategic_scope.get("fallback_reason", ""),
                },
            }

        logger.set_summary(**summary)
        self._check_completed()

        return issues_df, summary
