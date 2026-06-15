"""
Data adapter for unified project analysis.

Design principles:
1. DMP is the primary source for signing, bidding, customer and contract fields.
2. Appendix 1 supplements post-signing facts and may already include former SAP fields.
3. Bid report supplements bid-phase dates and labels that DMP does not retain.
"""
from pathlib import Path
import re

import numpy as np
import pandas as pd

from utils.helpers import parse_date, safe_float


APP1_SUPPLEMENT_FIELDS = [
    "最近一期成本分析利润率",
    "预收款应收款",
    "预收款实收款",
    "预收款约定支付日期",
    "预收款实际收款日期",
    "资金结余",
    "负流原因分析",
    "保证金约定退还日期",
    "保证金实际回收日期",
    "项目状态",
    "实际完成产值",
    "工期延误天数",
    "累计收款",
    "应收未收款",
    "未开工或退场原因",
]

BID_SUPPLEMENT_FIELDS = [
    "中标额（元）",
    "中标报量时间",
    "预计签约时间",
    "是否高端客户",
    "是否高端市场",
    "是否高端项目",
    "项目分类",
    "项目模式类",
]

QCC_RISK_COLUMNS = [
    "qcc_is_shixin",
    "qcc_is_weiyue",
    "qcc_has_zhongjian_lawsuit",
    "qcc_zhongjian_status",
    "qcc_risk_details",
]

CUSTOMER_NAME_CANDIDATES = [
    "客户名称",
    "瀹㈡埛鍚嶇О",
    "客户",
    "瀹㈡埛",
]


def _normalize_company_name(value) -> str:
    text = str(value or "").strip()
    if text.lower() in {"nan", "none"}:
        return ""
    text = re.sub(r"\s+", "", text)
    for suffix in ("（集团）", "(集团)"):
        text = text.replace(suffix, "")
    return text


def _find_first_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def load_qcc_risk_results(
    filepath: str | None = None,
    output_dir: str | None = None,
) -> pd.DataFrame:
    """Analyze optional QCC risk file and return customer-level flags."""
    base = Path(__file__).parent.parent
    if filepath is None:
        latest_outputs = sorted(
            (base / "output" / "qcc_screening").glob("风险排查_补充分析_*.xlsx"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        candidates = [
            base / "uploads" / "qcc_risk.xlsx",
            base / "uploads" / "qcc_risk.xls",
        ] + latest_outputs
        match = next((path for path in candidates if path.exists()), None)
        if match is None:
            return pd.DataFrame()
        filepath = str(match)

    path = Path(filepath)
    if not path.exists():
        return pd.DataFrame()

    if path.name.startswith("风险排查_补充分析_"):
        return _load_enriched_qcc_excel(path)

    if output_dir is None:
        output_dir = str(base / "output" / "qcc_screening")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    try:
        from utils.qcc_risk_screening import analyze_qcc_risk

        result = analyze_qcc_risk(str(path), output_dir=output_dir)
    except Exception as exc:
        print(f"[WARN] QCC risk analysis skipped: {exc}")
        return pd.DataFrame()

    records = []
    for company in result.get("companies", []):
        name = str(company.get("name", "")).strip()
        if not name:
            continue
        zhongjian_status = str(company.get("is_zhongjian", "")).strip()
        details = company.get("details", [])
        if isinstance(details, list):
            details_text = "\n".join(str(item) for item in details if str(item).strip())
        else:
            details_text = str(details or "")
        records.append(
            {
                "qcc_company_name": name,
                "_qcc_join_name": _normalize_company_name(name),
                "qcc_is_shixin": bool(company.get("is_shixin", False)),
                "qcc_is_weiyue": bool(company.get("is_weiyue", False)),
                "qcc_has_zhongjian_lawsuit": bool(zhongjian_status and "否" not in zhongjian_status and "鍚?" not in zhongjian_status),
                "qcc_zhongjian_status": zhongjian_status,
                "qcc_risk_details": details_text,
            }
        )

    if not records:
        return pd.DataFrame()

    qcc = pd.DataFrame(records)
    return qcc.drop_duplicates(subset="_qcc_join_name", keep="first")


def _qcc_yes(value) -> bool:
    text = str(value or "").strip().lower()
    return text in {"是", "y", "yes", "true", "1"} or text.startswith("是")


def _load_enriched_qcc_excel(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name="统计", header=2)
    except Exception as exc:
        print(f"[WARN] QCC enriched result skipped: {exc}")
        return pd.DataFrame()

    required = ["企业名称", "是否失信被执行人", "是否已违约或破产重整", "是否与中建存在重大诉讼"]
    if any(col not in df.columns for col in required):
        return pd.DataFrame()

    records = []
    for _, row in df.iterrows():
        name = str(row.get("企业名称", "") or "").strip()
        if not name or name.lower() == "nan":
            continue
        zhongjian_status = str(row.get("是否与中建存在重大诉讼", "") or "").strip()
        records.append(
            {
                "qcc_company_name": name,
                "_qcc_join_name": _normalize_company_name(name),
                "qcc_is_shixin": _qcc_yes(row.get("是否失信被执行人")),
                "qcc_is_weiyue": _qcc_yes(row.get("是否已违约或破产重整")),
                "qcc_has_zhongjian_lawsuit": _qcc_yes(zhongjian_status),
                "qcc_zhongjian_status": zhongjian_status,
                "qcc_risk_details": str(row.get("风险详情说明", "") or "").strip(),
            }
        )

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).drop_duplicates(subset="_qcc_join_name", keep="first")


def merge_qcc_risk(df: pd.DataFrame, qcc_risk: pd.DataFrame | None = None) -> pd.DataFrame:
    """Merge optional QCC customer risk flags into unified project data."""
    if df.empty:
        return df

    if qcc_risk is None:
        qcc_risk = load_qcc_risk_results()

    result = df.copy()
    for col in QCC_RISK_COLUMNS:
        if col not in result.columns:
            result[col] = False if col.startswith("qcc_is_") or col.startswith("qcc_has_") else ""

    if qcc_risk is None or qcc_risk.empty:
        return result

    customer_col = _find_first_column(result, CUSTOMER_NAME_CANDIDATES)
    if not customer_col:
        print("[WARN] QCC risk merge skipped: customer name column not found")
        return result

    result["_qcc_join_name"] = result[customer_col].apply(_normalize_company_name)
    qcc_cols = ["_qcc_join_name", "qcc_company_name"] + QCC_RISK_COLUMNS
    result = result.merge(
        qcc_risk[qcc_cols],
        on="_qcc_join_name",
        how="left",
        suffixes=("", "_qcc"),
    )

    for col in QCC_RISK_COLUMNS:
        merge_col = f"{col}_qcc"
        if merge_col in result.columns:
            if result[col].dtype == bool:
                result[col] = result[col] | result[merge_col].fillna(False).astype(bool)
            else:
                result[col] = result[col].where(result[col].astype(str).str.strip() != "", result[merge_col])
            result = result.drop(columns=[merge_col])

    for col in ["qcc_zhongjian_status", "qcc_risk_details", "qcc_company_name"]:
        if col in result.columns:
            result[col] = result[col].fillna("")
    for col in ["qcc_is_shixin", "qcc_is_weiyue", "qcc_has_zhongjian_lawsuit"]:
        result[col] = result[col].fillna(False).astype(bool)

    return result.drop(columns=["_qcc_join_name"], errors="ignore")


def _ensure_standard_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Backfill canonical columns expected by downstream models."""
    if "签约额（元）" not in df.columns:
        if "项目建安合同额（万元）" in df.columns:
            df["签约额（元）"] = df["项目建安合同额（万元）"].apply(safe_float) * 10000
        elif "安装工程合同额（万元）" in df.columns:
            df["签约额（元）"] = df["安装工程合同额（万元）"].apply(safe_float) * 10000
        elif "中标额（元）" in df.columns:
            df["签约额（元）"] = df["中标额（元）"].apply(safe_float)
        else:
            df["签约额（元）"] = 0.0
    else:
        df["签约额（元）"] = df["签约额（元）"].apply(safe_float)

    if "签约时间" not in df.columns and "预计签约时间" in df.columns:
        df["签约时间"] = pd.to_datetime(df["预计签约时间"], errors="coerce")

    return df


def load_bid_report(filepath: str = None) -> pd.DataFrame:
    """Load bid-winning report data."""
    if filepath is None:
        base = Path(__file__).parent.parent
        preferred = [
            base / "中标报量导出数据文件-1780290929280.xlsx",
            base / "中标报量导出数据文件.xlsx",
        ]
        candidates = [path for path in preferred if path.exists()]
        if not candidates:
            candidates = list(base.glob("中标报量导出数据文件-*.xlsx"))
        if candidates:
            filepath = str(candidates[0])
        else:
            return pd.DataFrame()

    path = Path(filepath)
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_excel(filepath)
    if df.empty or "项目编码" not in df.columns:
        return pd.DataFrame()

    keep_cols = ["项目编码"] + [col for col in BID_SUPPLEMENT_FIELDS if col in df.columns]
    bid = df[keep_cols].copy()
    bid["_join_code"] = bid["项目编码"].astype(str).str.strip()
    bid = bid.drop(columns=["项目编码"])

    for col in ["中标报量时间", "预计签约时间"]:
        if col in bid.columns:
            bid[col] = pd.to_datetime(bid[col], errors="coerce")
    if "中标额（元）" in bid.columns:
        bid["中标额（元）"] = bid["中标额（元）"].apply(safe_float)

    return bid.drop_duplicates(subset="_join_code", keep="first")


def build_unified_projects(dmp: pd.DataFrame, appendices: dict, bid_report: pd.DataFrame = None) -> pd.DataFrame:
    """Build unified project data using DMP + Appendix1 + bid report."""
    if dmp.empty:
        return pd.DataFrame()

    df = dmp.copy()
    app1 = appendices.get("appendix_1", pd.DataFrame())

    if "业务类型" not in df.columns:
        if "项目分类" in df.columns:
            df["业务类型"] = df["项目分类"].apply(_derive_biz_type)
        elif "工程类别" in df.columns:
            df["业务类型"] = df["工程类别"]
        elif "工程类别（原总公司市场口径）" in df.columns:
            df["业务类型"] = df["工程类别（原总公司市场口径）"]
        else:
            df["业务类型"] = "其他"

    for col in APP1_SUPPLEMENT_FIELDS:
        if col not in df.columns:
            df[col] = np.nan

    if not app1.empty and "项目编码（财务部）" in app1.columns:
        app1_supp = app1.copy()
        for col in APP1_SUPPLEMENT_FIELDS:
            if col not in app1_supp.columns:
                app1_supp[col] = np.nan

        app1_supp = app1_supp[["项目编码（财务部）"] + APP1_SUPPLEMENT_FIELDS].copy()
        app1_supp["_join_code"] = app1_supp["项目编码（财务部）"].astype(str).str.strip()
        app1_supp = app1_supp.dropna(subset=["_join_code"])
        app1_supp = app1_supp[app1_supp["_join_code"] != ""]
        app1_supp = app1_supp.drop(columns=["项目编码（财务部）"])
        app1_supp = app1_supp.drop_duplicates(subset="_join_code", keep="first")

        df["_join_code"] = df["项目编码"].astype(str).str.strip()
        df = df.merge(app1_supp, on="_join_code", how="left", suffixes=("", "_app1"), indicator="_appendix_join")
        df["附表已匹配"] = df["_appendix_join"].eq("both")
        df = df.drop(columns=["_appendix_join"], errors="ignore")
        for col in APP1_SUPPLEMENT_FIELDS:
            merge_col = f"{col}_app1"
            if merge_col in df.columns:
                df[col] = df[col].fillna(df[merge_col])
                df = df.drop(columns=[merge_col])

        if "最近一期成本分析利润率" in df.columns:
            unmatched_mask = ~df["附表已匹配"]
            if unmatched_mask.any():
                fuzzy_lookup = {}
                for _, row in app1_supp.iterrows():
                    code = str(row["_join_code"])
                    if len(code) >= 10:
                        fuzzy_lookup[code[-10:]] = row
                if fuzzy_lookup:
                    for idx in df[unmatched_mask].index:
                        code = str(df.at[idx, "_join_code"])
                        if len(code) >= 10 and code[-10:] in fuzzy_lookup:
                            source_row = fuzzy_lookup[code[-10:]]
                            df.at[idx, "附表已匹配"] = True
                            for col in APP1_SUPPLEMENT_FIELDS:
                                value = source_row.get(col)
                                if pd.notna(value):
                                    df.at[idx, col] = value
    else:
        df["_join_code"] = df["项目编码"].astype(str).str.strip()
        df["附表已匹配"] = False

    if bid_report is not None and not bid_report.empty:
        for col in BID_SUPPLEMENT_FIELDS:
            if col not in df.columns:
                df[col] = np.nan
        df = df.merge(bid_report, on="_join_code", how="left", suffixes=("", "_bid"))
        for col in BID_SUPPLEMENT_FIELDS:
            bid_col = f"{col}_bid"
            if bid_col in df.columns:
                df[col] = df[col].fillna(df[bid_col])
                df = df.drop(columns=[bid_col])
    else:
        for col in BID_SUPPLEMENT_FIELDS:
            if col not in df.columns:
                df[col] = np.nan

    df = _ensure_standard_columns(df)

    if "项目编码" in df.columns and "签约额（元）" in df.columns:
        code_amt = df.groupby("项目编码")["签约额（元）"].apply(lambda x: x.apply(safe_float).sum())
        df["_merged_contract_amt"] = df["项目编码"].map(code_amt)
    else:
        df["_merged_contract_amt"] = df.get("签约额（元）", pd.Series(index=df.index, dtype=float)).apply(safe_float)

    return df.drop(columns=["_join_code"], errors="ignore").reset_index(drop=True)


def _derive_biz_type(proj_class) -> str:
    s = str(proj_class)
    if "基础设施" in s:
        if "水利" in s:
            return "水利水电"
        if "能源" in s or "电力" in s:
            return "能源工程"
        if any(x in s for x in ["交通", "公路", "轨道", "市政"]):
            return "市政工程"
        if any(x in s for x in ["环保", "供水"]):
            return "环保工程"
        return "基础设施"
    if any(x in s for x in ["房屋建筑", "住宅", "商业", "办公", "公共", "工业厂房", "产业园区"]):
        return "房屋建筑"
    if "安装" in s or "机电" in s:
        return "安装工程"
    if "装饰" in s or "装修" in s:
        return "装饰装修"
    return "其他"


def _extract_percentage(text) -> float:
    s = str(text).strip()
    m = re.search(r"(\d+\.?\d*)\s*%", s)
    if m:
        return float(m.group(1))
    return 0.0


def _classify_non_cash(payment_form) -> float:
    s = str(payment_form).strip()
    if "承兑" in s or "汇票" in s or "商票" in s:
        pct = _extract_percentage(s)
        return pct if pct > 0 else 50.0
    if "抵房" in s or "资产抵" in s:
        pct = _extract_percentage(s)
        return pct if pct > 0 else 30.0
    if "现金" in s and "非现金" not in s:
        return 0.0
    return 0.0


def _map_project_type(proj_type) -> str:
    s = str(proj_type).strip()
    if s in ("商业住宅", "商业综合体"):
        return "地产"
    if any(x in s for x in ["基础设施", "公路", "市政", "轨道交通"]):
        return "基础设施"
    if "工业" in s or "厂房" in s:
        return "工业"
    if any(x in s for x in ["公共", "学校", "医院"]):
        return "公建"
    if "装修" in s or "装饰" in s:
        return "装饰"
    return "其他"


def _normalize_unit_name(name: str) -> str:
    s = str(name).strip()
    common_prefixes = [
        "一公司", "四川建设公司", "交通建设公司", "六公司", "土木公司",
        "建设发展公司", "贵州建设公司", "华南公司", "总承包公司",
        "西北公司", "北京公司", "珠江海外公司", "水利能源公司", "东方建投公司",
        "中建四局", "建设投资", "建设发展",
    ]
    for prefix in sorted(common_prefixes, key=len, reverse=True):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


def _parse_city_set(text: str) -> set:
    cities = set()
    s = str(text).strip()
    if not s or s == "nan":
        return cities
    s = re.sub(r"[（(]共\d+个[)）]", "", s)
    for c in s.replace("、", ",").replace("，", ",").split(","):
        c = re.sub(r"\s*[（(][^)）]*[)）]\s*", "", c).strip()
        if c and c not in ("全国", ""):
            cities.add(c)
    return cities


def _parse_province_set(text: str) -> set:
    provinces = set()
    s = str(text).strip()
    if not s or s == "nan":
        return provinces
    s = re.sub(r"[（(]共\d+个[)）]", "", s)
    for p in s.replace("、", ",").replace("，", ",").split(","):
        p = p.strip()
        if p:
            provinces.add(p)
    return provinces


def _match_unit_to_auth(unit: str, lookup: dict) -> str | None:
    unit = str(unit).strip()
    if not unit or unit == "nan":
        return None
    if unit in lookup:
        return unit

    candidate = "一公司" + unit
    if candidate in lookup:
        return candidate

    suffix_matches = [k for k in lookup if k.endswith(unit)]
    if suffix_matches:
        one_company = [m for m in suffix_matches if m.startswith("一公司")]
        return one_company[0] if one_company else suffix_matches[0]

    parents = set(entry["parent"] for entry in lookup.values() if entry.get("parent"))
    for parent in sorted(parents):
        if parent == "一公司":
            continue
        candidate = parent + unit
        if candidate in lookup:
            return candidate

    for key in lookup:
        if unit in key:
            return key
    return None


def enrich_region_data(df: pd.DataFrame, region_auth: pd.DataFrame) -> pd.DataFrame:
    """Add region authorization columns to unified project data."""
    if region_auth is None or region_auth.empty:
        return df

    ncols = len(region_auth.columns)
    df["授权城市"] = ""
    df["授权省份"] = ""
    df["普通区域"] = ""
    df["是否窜区"] = ""

    lookup = {}
    parent_rows = {}
    for _, row in region_auth.iterrows():
        parent = str(row.iloc[0]).strip() if len(row) > 0 else ""
        unit_name = str(row.iloc[1]).strip() if len(row) > 1 else ""
        if not unit_name or unit_name == "nan":
            continue

        core = _parse_city_set(str(row.iloc[2]) if len(row) > 2 else "")
        priority = _parse_city_set(str(row.iloc[3]) if len(row) > 3 else "")
        provinces = _parse_province_set(str(row.iloc[4]) if len(row) > 4 else "")
        if ncols >= 7:
            general = _parse_city_set(str(row.iloc[5]) if len(row) > 5 else "")
            notes = str(row.iloc[6]) if len(row) > 6 else ""
        else:
            general = set()
            notes = str(row.iloc[5]) if len(row) > 5 else ""

        entry = {
            "parent": parent,
            "cities": core | priority | general,
            "core_cities": core,
            "key_cities": priority,
            "general_cities": general,
            "provinces": provinces,
            "notes": notes,
        }
        if unit_name == "小计":
            parent_rows[parent] = entry
        else:
            lookup[unit_name] = entry

    for key, entry in lookup.items():
        parent = entry["parent"]
        if parent in parent_rows:
            entry["parent_provinces"] = parent_rows[parent]["provinces"]

    for idx, row in df.iterrows():
        unit = str(row.get("申报单位", ""))
        city = str(row.get("项目城市", ""))
        addr = str(row.get("项目地址", ""))
        match_key = _match_unit_to_auth(unit, lookup)
        if match_key is None:
            continue

        auth = lookup[match_key]
        is_infra = "基础设施" in match_key or "基础设施" in auth.get("notes", "")
        effective_provinces = auth.get("parent_provinces", auth["provinces"]) if is_infra else auth["provinces"]
        effective_cities = auth["cities"]

        df.at[idx, "授权城市"] = ", ".join(sorted(effective_cities)) if effective_cities else ""
        df.at[idx, "授权省份"] = ", ".join(sorted(effective_provinces)) if effective_provinces else ""
        df.at[idx, "普通区域"] = ", ".join(sorted(auth.get("general_cities", set()))) if auth.get("general_cities") else ""

        if effective_cities and city:
            city_no_suffix = city.rstrip("市")
            city_match = any(city == ac or city_no_suffix == ac or (city + "市") == ac for ac in effective_cities)
            if not city_match:
                prov_match = any(p.strip() in addr for p in effective_provinces) if effective_provinces else False
                if not prov_match:
                    df.at[idx, "是否窜区"] = "是"

    return df
