"""
Model 1.1: 区域布局动态偏差检测与窜区预警模型

制度依据：
- 《市场营销管理办法》及手册0520（2026年5月修订版）
- 手册0520 第二篇章 区域管理（附件2：经营区域及维护认定清单）
- 手册0520 附件4：经营区域分级认定清单（4级分类）
- 手册0520 附件5：分级结果运用（跨区域≥5亿）
- 《市场营销区域管理办法》3.2（区域布局与授权管理）
- 中建四市字〔2024〕143号（经营区域认定）

多年度区域认定（2023/2024/2025/2026）：
  项目签约年份不同，区域认定可能变化。本模型按项目签约年份匹配对应年度的
  区域认定表，确保审计判断的准确性与时效性。
  - 2023年及以前项目 → 使用2023年度区域认定
  - 2024年项目 → 使用2024年度区域认定（中建四市字〔2024〕143号）
  - 2025年项目 → 使用2025年度区域认定
  - 2026年及以后项目 → 使用2026年度区域认定（手册0520）

检测逻辑（完全依据上述制度文件，不依赖DMP导出字段）：
1. 窜区检测：项目所在城市不在三级单位的授权城市集合内，且项目省份不在授权省份内
2. 非常规区域门槛：跨区域承接项目合同额≥5亿（手册0520附件5，原10亿）
3. 特殊豁免：轨道交通/水利水电/能源类/专业工程/基础设施不受区域管理限制
4. 安装公司：承接安装/钢结构类专业承包项目不受区域限制（手册0520 p70）
5. 基础设施项目：不受区域限制（手册0520附件5）
6. 4级分类（2026起）：核心/重点/常规/普通区域
"""
import pandas as pd
from datetime import datetime
from models.base_model import BaseModel
from utils.helpers import safe_float


class Model11Region(BaseModel):
    model_id = "1.1"
    model_name = "区域布局动态偏差检测与窜区预警"
    priority = "P1"
    dimension = "战略与布局"

    # 制度依据：手册0520 — 不受区域管理限制的项目类型
    # 轨道交通/水利水电/能源类/专业工程/基础设施 → 不受区域限制
    REGION_EXEMPT_TYPES = ["轨道交通", "水利水电", "能源类", "专业工程", "基础设施"]

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        rules = self.config.get("institutional", {}).get("区域管理", {})
        # 年份分层：2026年前适用旧制度(10亿)，2026年起适用手册0520新制度(5亿)
        regime_year = rules.get("制度切换年份_区域门槛", 2026)
        # 新制度阈值（2026+）
        min_amt_new = rules.get("非常规区域合同额下限_亿元", 5) * 100_000_000
        install_exempt_new = rules.get("安装公司_专业承包不受区域限制", True)
        infra_exempt_new = rules.get("基础设施项目不受区域限制", True)
        # 旧制度阈值（2026前）
        min_amt_old = rules.get("非常规区域合同额下限_历史_亿元", 10) * 100_000_000
        install_min_old = rules.get("安装公司非常规区域合同额下限_历史_亿元", 1.5) * 100_000_000
        core_ratio_min = rules.get("深耕区域合同额占比下限", 0.50)

        # Support multi-year region_auth: dict of DataFrames or single DataFrame
        if region_auth is None:
            logger.log_warning("无区域认定数据，跳过模型1.1")
            self._check_completed()
            return pd.DataFrame(), {"total_checked": 0, "窜区项目": 0, "非常规未达门槛": 0, "total_issues": 0}

        if isinstance(region_auth, dict):
            if all(v is None or (isinstance(v, pd.DataFrame) and v.empty) for v in region_auth.values()):
                logger.log_warning("所有年度区域认定数据均为空，跳过模型1.1")
                self._check_completed()
                return pd.DataFrame(), {"total_checked": 0, "窜区项目": 0, "非常规未达门槛": 0, "total_issues": 0}
        elif isinstance(region_auth, pd.DataFrame) and region_auth.empty:
            logger.log_warning("无区域认定数据，跳过模型1.1")
            self._check_completed()
            return pd.DataFrame(), {"total_checked": 0, "窜区项目": 0, "非常规未达门槛": 0, "total_issues": 0}

        df = dmp.copy()

        # === Step 1: Determine project signing year & build per-year auth lookups ===
        df["签约年份"] = df.apply(self._extract_signing_year, axis=1)

        # Build auth lookups per year
        auth_lookups = {}  # {year: lookup_dict}
        if isinstance(region_auth, dict):
            for year, auth_df in region_auth.items():
                if auth_df is not None and not auth_df.empty:
                    auth_lookups[int(year)] = self._build_auth_lookup(auth_df)
        else:
            # Single DataFrame — check for 年份 column
            if "年份" in region_auth.columns:
                for year, group in region_auth.groupby("年份"):
                    auth_lookups[int(year)] = self._build_auth_lookup(group)
            else:
                # Legacy: single year, assume 2025
                auth_lookups[2025] = self._build_auth_lookup(region_auth)

        if not auth_lookups:
            logger.log_warning("无法构建区域认定查找表，跳过模型1.1")
            self._check_completed()
            return pd.DataFrame(), {"total_checked": 0, "窜区项目": 0, "非常规未达门槛": 0, "total_issues": 0}

        # Default to latest available year for projects without a signing year
        default_year = max(auth_lookups.keys())

        # === Step 2: Audit each project against its year's designation ===
        findings = []
        unit_stats = {}  # per-unit: {core_amt, total_amt}

        # Track which designation year was used for each project (for logging)
        year_usage = {}

        for idx, row in df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            unit = str(row.get("申报单位", ""))
            address = str(row.get("项目地址", ""))
            # 使用合并合同额（含补充协议），避免补充协议单独行金额小漏判
            contract_amt = safe_float(row.get("_merged_contract_amt",
                                   row.get("签约额（元）", 0)))
            eng_type = str(row.get("工程类别", ""))
            signing_year = int(row.get("签约年份", 0)) if pd.notna(row.get("签约年份")) else 0

            # 手册0520: 区域管理适用范围不含投资类、运营类项目
            is_investment = str(row.get("是否投资项目", "")).strip() == "是"
            biz_type = str(row.get("业务类型", ""))
            if is_investment or "运营" in biz_type or "投资" in biz_type:
                continue

            city = self._extract_city(address)
            province = self._extract_province(address)

            # Select auth lookup for this project's signing year
            if signing_year and signing_year in auth_lookups:
                use_year = signing_year
            elif signing_year and signing_year < min(auth_lookups.keys()):
                use_year = min(auth_lookups.keys())  # use earliest available
            else:
                use_year = default_year

            auth_data = auth_lookups[use_year]
            year_usage[use_year] = year_usage.get(use_year, 0) + 1

            # Match unit to auth entry
            match_key, auth = self._match_unit(unit, auth_data)

            if match_key is None:
                # Unit not found in this year's lookup — try fallback to default year
                if use_year != default_year:
                    match_key, auth = self._match_unit(unit, auth_lookups[default_year])
                    if match_key:
                        use_year = default_year
                        year_usage[use_year] = year_usage.get(use_year, 0) + 1
                if match_key is None:
                    logger.log_warning(f"未在区域认定表中找到单位: {unit}")
                    continue

            issues = []

            # === 年份分层：旧项目用旧制度，新项目用新制度 ===
            is_new_regime = signing_year >= regime_year if signing_year else True

            # Select year-appropriate thresholds
            _min_amt = min_amt_new if is_new_regime else min_amt_old
            _install_exempt = install_exempt_new if is_new_regime else False
            _infra_exempt = infra_exempt_new if is_new_regime else False
            _install_min = install_min_old  # Only used in old regime

            # Determine if project type is exempt from region control
            # (制度依据：手册0520 — 轨道交通/水利水电/能源类/专业工程/基础设施不受区域管理限制)
            # Note: 基础设施 exemption only applies to 2026+ projects (is_new_regime)
            base_exempt_types = ["轨道交通", "水利水电", "能源类", "专业工程"]
            if _infra_exempt:
                base_exempt_types.append("基础设施")
            is_exempt = any(t in eng_type or t in proj_name for t in base_exempt_types)

            # 手册0520 p70: 安装公司承接安装/钢结构类专业承包项目不受区域限制（仅2026+）
            if _install_exempt:
                is_install_professional = "安装" in unit or "安装" in eng_type
                is_steel_structure = "钢结构" in eng_type or "钢结构" in proj_name
                if (is_install_professional or is_steel_structure) and ("专业承包" in eng_type or "专业" in eng_type):
                    is_exempt = True

            # Infrastructure units still use parent-level auth for窜区 detection
            is_infra_unit = "基础设施_二级区域" in auth.get("exemptions", [])
            use_parent_auth = is_infra_unit and auth.get("parent_auth")

            if use_parent_auth:
                effective_provinces = auth["parent_auth"]["provinces"]
                effective_cities = auth["parent_auth"]["all_cities"]
            else:
                effective_provinces = auth["provinces"]
                effective_cities = auth["all_cities"]

            # --- 窜区检测 ---
            if not is_exempt:
                in_city = self._city_in_set(city, effective_cities) if city else False
                in_province = self._province_in_set(province, effective_provinces) if province else False

                if not in_city and not in_province:
                    issues.append({
                        "type": "窜区",
                        "desc": (
                            f"项目位于{address}（城市:{city}，省:{province}），"
                            f"不在{unit}的授权范围。"
                            f"授权城市: {self._fmt_set(effective_cities)}；"
                            f"授权省份: {self._fmt_set(effective_provinces)}"
                        ),
                        "severity": "red"
                    })

            # --- 非常规区域门槛检测 ---
            # 年份分层: 2026前≥10亿，2026起≥5亿（手册0520附件5）
            in_conventional = self._province_in_set(province, effective_provinces) if province else False

            # 基础设施项目不受区域限制（仅2026+项目适用）
            is_infra_project = _infra_exempt and ("基础设施" in eng_type or "基础设施" in proj_name)

            if not in_conventional and not is_exempt and not is_infra_project:
                # Old regime: installation company has separate 1.5亿 threshold
                if not is_new_regime and ("安装" in eng_type or "安装" in unit):
                    threshold = _install_min
                    threshold_desc = f"门槛{threshold/1e8:.1f}亿（安装公司旧制度）"
                else:
                    threshold = _min_amt
                    regime_label = "手册0520附件5：跨区域≥5亿" if is_new_regime else "制度规定：跨区域≥10亿"
                    threshold_desc = f"门槛{threshold/1e8:.1f}亿（{regime_label}）"

                if contract_amt < threshold:
                    issues.append({
                        "type": "非常规区域未达门槛",
                        "desc": (
                            f"非常规区域项目（地址:{address}，省:{province}不在常规区域"
                            f"{self._fmt_set(auth['provinces'])}内），"
                            f"合同额{contract_amt/1e8:.1f}亿 < {threshold_desc}"
                        ),
                        "severity": "yellow"
                    })

            for issue in issues:
                findings.append({
                    "模型编号": "1.1",
                    "项目编码": proj_code,
                    "项目名称": proj_name,
                    "申报单位": unit,
                    "客户名称": str(row.get("客户名称", "")),
                    "项目城市": city,
                    "项目省份": province,
                    "问题分类": issue["type"],
                    "严重等级": issue["severity"],
                    "问题描述": issue["desc"],
                    "签约额（元）": contract_amt,
                    "签约年份": signing_year,
                    "认定年度": use_year,
                })

            # Track per-unit stats for core ratio check（按项目编码去重，避免补充协议行重复计）
            if unit not in unit_stats:
                unit_stats[unit] = {"core_amt": 0.0, "total_amt": 0.0, "seen_codes": set()}
            if proj_code not in unit_stats[unit]["seen_codes"]:
                unit_stats[unit]["seen_codes"].add(proj_code)
                unit_stats[unit]["total_amt"] += contract_amt
                in_core = self._city_in_set(city, auth["core_cities"]) if city else False
                if in_core:
                    unit_stats[unit]["core_amt"] += contract_amt

        issues_df = pd.DataFrame(findings)

        # === Step 3: Core region contract ratio (深耕区域合同额占比) ===
        # 制度依据：《市场营销区域管理办法》3.2
        for unit_name, stats in unit_stats.items():
            if stats["total_amt"] > 0:
                ratio = stats["core_amt"] / stats["total_amt"]
                if ratio < core_ratio_min:
                    findings.append({
                        "模型编号": "1.1",
                        "申报单位": unit_name,
                        "问题分类": "深耕区域占比不足",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"{unit_name}深耕区域合同额占比{ratio:.1%} < {core_ratio_min:.0%}，"
                            f"深耕{stats['core_amt']/1e8:.1f}亿 / 总{stats['total_amt']/1e8:.1f}亿"
                        ),
                        "签约额（元）": stats["total_amt"],
                    })

        # === Step 4: 授权城市未破零检测 ===
        # 检查每个单位的授权城市集合中是否有城市完全无项目落地
        actual_cities_by_unit = {}
        for _, row in df.iterrows():
            unit = str(row.get("申报单位", ""))
            address = str(row.get("项目地址", ""))
            city = self._extract_city(address)
            if unit and city:
                if unit not in actual_cities_by_unit:
                    actual_cities_by_unit[unit] = set()
                actual_cities_by_unit[unit].add(city)

        # Collect all authorized cities per unit across all years
        for unit_name in unit_stats:
            # Find auth entry for this unit from any year
            auth_cities_all = set()
            for year, lookup in auth_lookups.items():
                match_key, entry = self._match_unit(unit_name, lookup)
                if match_key and entry:
                    auth_cities_all |= entry.get("all_cities", set())

            if auth_cities_all:
                actual = actual_cities_by_unit.get(unit_name, set())
                uncovered = auth_cities_all - actual
                if len(uncovered) > 0:
                    # Only flag if at least 20% of authorized cities are uncovered
                    uncover_ratio = len(uncovered) / len(auth_cities_all)
                    if uncover_ratio >= 0.20:
                        findings.append({
                            "模型编号": "1.1",
                            "申报单位": unit_name,
                            "问题分类": "授权城市未破零",
                            "严重等级": "yellow",
                            "问题描述": (
                                f"{unit_name}授权{len(auth_cities_all)}个城市，"
                                f"{len(uncovered)}个无项目落地({uncover_ratio:.0%})，"
                                f"缺失: {', '.join(sorted(list(uncovered))[:5])}"
                            ),
                            "签约额（元）": 0,
                        })

        if len(issues_df) > 0:
            issues_df = issues_df.sort_values("严重等级")

        summary = {
            "total_checked": len(df),
            "窜区项目": len(issues_df[issues_df["问题分类"] == "窜区"]) if len(issues_df) > 0 else 0,
            "非常规未达门槛": len(issues_df[issues_df["问题分类"] == "非常规区域未达门槛"]) if len(issues_df) > 0 else 0,
            "深耕区域占比不足": len(issues_df[issues_df["问题分类"] == "深耕区域占比不足"]) if len(issues_df) > 0 else 0,
            "授权城市未破零": len(issues_df[issues_df["问题分类"] == "授权城市未破零"]) if len(issues_df) > 0 else 0,
            "total_issues": len(issues_df),
        }

        logger.set_summary(**summary)
        year_dist = ", ".join(f"{y}年认定:{n}个项目" for y, n in sorted(year_usage.items()))
        logger.log_check(f"窜区检测（多年度区域认定: {year_dist}）", True, {"窜区": summary["窜区项目"]})
        logger.log_check("非常规区域门槛检测", True, {"未达门槛": summary["非常规未达门槛"]})
        self._check_completed()

        return issues_df, summary

    # ===== Helper methods =====

    def _extract_signing_year(self, row) -> int:
        """
        Extract the year a project was signed from available date columns.
        Tries: 签约时间 → 中标时间 → 合同签订年度 → defaults to 0 (unknown)
        """
        for col in ["签约时间", "中标时间", "签约报量时间"]:
            val = row.get(col)
            if pd.notna(val) and val is not None:
                try:
                    if isinstance(val, datetime):
                        return val.year
                    if isinstance(val, str) and len(val) >= 4:
                        return int(val[:4])
                except (ValueError, TypeError):
                    continue

        # Try 合同签订年度 (may be just a year number)
        year_col = row.get("合同签订年度")
        if pd.notna(year_col) and year_col is not None:
            try:
                y = int(float(str(year_col).strip()))
                if 2000 <= y <= 2100:
                    return y
            except (ValueError, TypeError):
                pass

        return 0  # unknown

    def _build_auth_lookup(self, region_auth: pd.DataFrame) -> dict:
        """
        Parse the region authorization CSV into a structured lookup.

        CSV format varies by year:
          2023-2025 (6 columns):
            0: 局属二级单位 (parent company)
            1: 局属三级单位 (subsidiary)
            2: 核心城市(深耕区域)  (core cities)
            3: 重点城市(深耕区域)  (key/priority cities)
            4: 常规区域(省/市)     (conventional provinces)
            5: 备注              (notes — exemptions, special rules)

          2026 (7 columns, per handbook 0520 4-tier system):
            0: 局属二级单位
            1: 局属三级单位
            2: 核心城市(深耕区域)
            3: 重点城市(深耕区域)
            4: 常规区域(省/市)
            5: 普通区域          (general region — new 4th tier)
            6: 备注

        Returns dict keyed by 三级单位 name (exact as in CSV).
        Also builds a parent-level lookup for infrastructure projects.
        """
        ncols = len(region_auth.columns)
        lookup = {}
        parent_rows = {}  # 二级单位 -> row data for parent-level fallback

        for _, row in region_auth.iterrows():
            parent = str(row.iloc[0]).strip() if len(row) > 0 else ""
            unit_name = str(row.iloc[1]).strip() if len(row) > 1 else ""
            core_str = str(row.iloc[2]) if len(row) > 2 else ""
            key_str = str(row.iloc[3]) if len(row) > 3 else ""
            province_str = str(row.iloc[4]) if len(row) > 4 else ""

            # 2026 CSV has 普通区域 at column 5, 备注 at column 6
            if ncols >= 7:
                general_str = str(row.iloc[5]) if len(row) > 5 else ""
                notes = str(row.iloc[6]) if len(row) > 6 else ""
            else:
                general_str = ""
                notes = str(row.iloc[5]) if len(row) > 5 else ""

            # Skip rows with empty unit name or "小计"
            if not unit_name or unit_name == "nan":
                continue

            core_cities = self._parse_city_list(core_str)
            key_cities = self._parse_city_list(key_str)
            general_cities = self._parse_city_list(general_str)
            all_cities = core_cities | key_cities | general_cities
            provinces = self._parse_province_list(province_str)

            # Parse exemptions from 备注
            exemptions = []
            if "不受区域管理限制" in notes:
                exemptions.append("全豁免")
            if "不受区域限制" in notes:
                exemptions.append("全豁免")
            if "基础设施项目在" in notes:
                exemptions.append("基础设施_二级区域")

            entry = {
                "parent": parent,
                "unit_name": unit_name,
                "core_cities": core_cities,
                "key_cities": key_cities,
                "general_cities": general_cities,
                "all_cities": all_cities,
                "provinces": provinces,
                "exemptions": exemptions,
                "notes": notes,
            }

            if unit_name == "小计":
                # Store parent-level summary for infrastructure fallback
                parent_rows[parent] = entry
            else:
                lookup[unit_name] = entry

        # Attach parent-level auth to each entry for infrastructure fallback
        for unit_name, entry in lookup.items():
            parent = entry["parent"]
            if parent in parent_rows:
                entry["parent_auth"] = parent_rows[parent]

        return lookup

    def _match_unit(self, unit_name: str, lookup: dict) -> tuple:
        """
        Match an App1 unit name to a CSV entry.

        App1 uses short names (e.g. "广州分公司"), CSV uses full names
        (e.g. "一公司广州分公司"). Since this is a 一公司 internal audit,
        matching prefers 一公司 entries when multiple matches exist.

        Returns (match_key, entry) or (None, None).
        """
        unit = str(unit_name).strip()
        if not unit or unit == "nan":
            return None, None

        # 1. Exact match
        if unit in lookup:
            return unit, lookup[unit]

        # 2. Try prepending "一公司" (preferred since this is a 一公司 audit)
        candidate = "一公司" + unit
        if candidate in lookup:
            return candidate, lookup[candidate]

        # 3. Suffix match: CSV key ends with unit name, prefer 一公司
        suffix_matches = [k for k in lookup if k.endswith(unit)]
        if suffix_matches:
            yigongsi = [m for m in suffix_matches if m.startswith("一公司")]
            if yigongsi:
                return yigongsi[0], lookup[yigongsi[0]]
            return suffix_matches[0], lookup[suffix_matches[0]]

        # 4. Try prepending other parent names
        parents = sorted(set(e["parent"] for e in lookup.values() if e["parent"]))
        others = [p for p in parents if p != "一公司"]
        for parent in others:
            candidate = parent + unit
            if candidate in lookup:
                return candidate, lookup[candidate]

        # 5. Generic substring match as last resort
        for key, entry in lookup.items():
            if unit in key:
                return key, entry

        return None, None

    def _parse_city_list(self, text: str) -> set:
        """Parse city list from CSV text. Handles '（共N个）' suffix in 小计 rows."""
        import re
        cities = set()
        s = str(text).strip()
        if not s or s == "nan":
            return cities
        # Strip trailing （共N个） and parentheticals
        s = re.sub(r'[（(]共\d+个[)）]', '', s)
        for c in s.replace("、", ",").replace("，", ",").split(","):
            c = c.strip()
            # Also strip any trailing parenthetical per item
            c = re.sub(r'\s*[（(][^)）]*[)）]\s*', '', c).strip()
            if c and c not in ("全国", ""):
                cities.add(c)
        return cities

    def _parse_province_list(self, text: str) -> set:
        """Parse province list from CSV text. Handles '（共N个）' suffix in 小计 rows."""
        import re
        provinces = set()
        s = str(text).strip()
        if not s or s == "nan":
            return provinces
        s = re.sub(r'[（(]共\d+个[)）]', '', s)
        for p in s.replace("、", ",").replace("，", ",").split(","):
            p = p.strip()
            if p:
                provinces.add(p)
        return provinces

    def _extract_city(self, address: str) -> str:
        """Extract city name from project address (e.g. '广东省广州市天河区' -> '广州')."""
        s = str(address).strip()
        if not s:
            return ""

        # Handle autonomous regions: 广西壮族自治区南宁市 -> city=南宁
        for ar in ["壮族自治区", "回族自治区", "维吾尔自治区"]:
            if ar in s:
                after_ar = s.split(ar)[-1]
                if "市" in after_ar:
                    city = after_ar.split("市")[0].strip()
                    if city and len(city) <= 6:
                        return city
                # No市 suffix: take the first non-empty segment
                return after_ar.strip() if len(after_ar.strip()) <= 6 else ""

        # Pattern: XX省XX市
        if "省" in s:
            after_province = s.split("省")[-1]
            if "市" in after_province:
                city = after_province.split("市")[0].strip()
                if city and len(city) <= 6:
                    return city

        # Pattern: direct XX市 (for province-level cities like 重庆市, 上海市)
        if "市" in s:
            parts = s.split("市")
            for i, part in enumerate(parts[:-1]):
                words = part.replace("/", " ").replace("-", " ").split()
                if words:
                    city = words[-1].strip()
                    if city and len(city) <= 6:
                        return city

        return ""

    def _extract_province(self, address: str) -> str:
        """Extract province name from project address (e.g. '广东省广州市' -> '广东省')."""
        s = str(address).strip()
        if not s:
            return ""

        # Autonomous regions (must check before 省)
        for ar in ["广西壮族自治区", "内蒙古自治区", "西藏自治区",
                    "宁夏回族自治区", "新疆维吾尔自治区"]:
            if ar in s:
                return ar

        # Province
        if "省" in s:
            return s.split("省")[0].strip() + "省"

        # Province-level city (北京市, 上海市, 天津市, 重庆市)
        for city in ["北京市", "上海市", "天津市", "重庆市"]:
            if s.startswith(city) or city in s:
                return city

        return ""

    def _city_in_set(self, city: str, city_set: set) -> bool:
        """Check if city matches any entry in city_set, with or without 市 suffix."""
        if not city or not city_set:
            return False
        c = city.strip()
        c_no_suffix = c.rstrip("市")
        c_with_suffix = c if c.endswith("市") else c + "市"
        for auth_city in city_set:
            ac = auth_city.strip()
            if c == ac or c_no_suffix == ac or c_with_suffix == ac:
                return True
        return False

    def _province_in_set(self, province: str, province_set: set) -> bool:
        """Check if province matches any entry in province_set."""
        if not province or not province_set:
            return False
        p = province.strip()
        for ap in province_set:
            if p == ap.strip() or ap.strip().startswith(p.rstrip("省市自治区")) or p.startswith(ap.strip().rstrip("省市自治区")):
                return True
        return False

    def _fmt_set(self, s: set) -> str:
        """Format a set as a compact string for display."""
        if not s:
            return "（空）"
        items = sorted(s)
        if len(items) <= 5:
            return "、".join(items)
        return "、".join(items[:3]) + f"...共{len(items)}个"
