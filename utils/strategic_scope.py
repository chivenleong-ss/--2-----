"""Strategic planning scope detection shared by web views and models."""
from __future__ import annotations

from typing import Any

import pandas as pd

from data_loader.admin_structure_loader import resolve_unit_scope


def sector_targets_from(plan: dict[str, Any]) -> dict[str, dict]:
    return {
        str(key): value
        for key, value in (plan or {}).items()
        if not str(key).startswith("_") and isinstance(value, dict)
    }


def detect_strategic_scope(df: pd.DataFrame | None, config: dict | None) -> dict[str, Any]:
    """Detect whether current data should use bureau-level or company-level 155 targets.

    Administrative hierarchy is authoritative. Name aliases are only used as a fallback
    when the uploaded admin structure cannot resolve a unit.
    """
    strat_cfg = (config or {}).get("十五五战略规划", {})
    diff_bases = strat_cfg.get("二级单位差异化基准", {})
    global_base = strat_cfg.get("局级全局基准", strat_cfg.get("业务结构目标", {}))
    tolerance = strat_cfg.get("偏差容忍度", 0.05)

    global_targets = sector_targets_from(global_base)

    def fallback(reason: str, scope_name: str = "四局全局155基准") -> dict[str, Any]:
        return {
            "scope_type": "global",
            "scope_name": scope_name,
            "matched_unit": None,
            "target_dict": global_targets,
            "tolerance": tolerance,
            "not_applicable": [],
            "fallback_reason": reason,
            "match_source": "",
        }

    if df is None or df.empty:
        return fallback("暂无可识别项目数据", "四局全局155基准（暂无数据）")

    if "申报单位" not in df.columns:
        return fallback("缺少申报单位字段")

    units = [
        str(unit).strip()
        for unit in df["申报单位"].dropna().astype(str).unique()
        if str(unit).strip()
    ]
    if not units:
        return fallback("申报单位为空")

    company_lookup: dict[str, str] = {}
    for company_name, company_cfg in diff_bases.items():
        if str(company_name).startswith("_") or not isinstance(company_cfg, dict):
            continue
        company = str(company_name).strip()
        company_lookup[company] = company
        for alias in company_cfg.get("_alias", []):
            alias_name = str(alias).strip()
            if alias_name:
                company_lookup[alias_name] = company

    def match_config_company(name: str) -> str | None:
        name = str(name or "").strip()
        if not name:
            return None
        for key, company in company_lookup.items():
            if key and (key == name or key in name or name in key):
                return company
        return None

    unit_to_parent: dict[str, str | None] = {}
    unit_to_source: dict[str, str] = {}
    unit_to_secondary: dict[str, str] = {}
    for unit in units:
        resolved = resolve_unit_scope(unit)
        secondary = str(resolved.get("二级") or "").strip()
        unit_to_secondary[unit] = secondary

        matched = match_config_company(secondary)
        source = "行政架构"
        if not matched:
            # Fallback only when admin hierarchy cannot map to a configured company.
            # This keeps uploaded 行政架构 as the source of truth.
            matched = match_config_company(unit)
            source = "名称兜底" if matched else "未命中"
        unit_to_parent[unit] = matched
        unit_to_source[unit] = source

    parents = {parent for parent in unit_to_parent.values() if parent}
    if len(parents) == 1 and all(unit_to_parent.values()):
        company = next(iter(parents))
        plan = diff_bases.get(company, {})
        targets = sector_targets_from(plan)
        if targets:
            return {
                "scope_type": "single",
                "scope_name": f"{company} 专属155目标",
                "matched_unit": company,
                "target_dict": targets,
                "tolerance": tolerance,
                "not_applicable": list(plan.get("_not_applicable_sectors", [])),
                "fallback_reason": "",
                "source": plan.get("_source", ""),
                "note": plan.get("_note", ""),
                "match_source": "行政架构" if all(src == "行政架构" for src in unit_to_source.values()) else "行政架构+名称兜底",
                "resolved_secondaries": sorted(set(unit_to_secondary.values())),
            }

    if len(units) == 1:
        unit = units[0]
        secondary = unit_to_secondary.get(unit) or unit
        return fallback(f"{secondary} 未配置专属155基准（依据行政架构识别）")
    if parents:
        return fallback(f"当前数据包含多个单位或未全量命中专属基准：{len(units)}个申报单位")
    return fallback("未命中任何二级单位专属155基准")


def summarize_strategic_scope(scope: dict[str, Any]) -> dict[str, Any]:
    targets = scope.get("target_dict") or {}
    sectors = [key for key in targets.keys() if not str(key).startswith("_")]
    return {
        "scope_type": scope.get("scope_type", "global"),
        "scope_name": scope.get("scope_name", "四局全局155基准"),
        "matched_unit": scope.get("matched_unit"),
        "fallback_reason": scope.get("fallback_reason", ""),
        "source": scope.get("source", ""),
        "note": scope.get("note", ""),
        "sectors": sectors,
        "not_applicable": scope.get("not_applicable", []),
        "match_source": scope.get("match_source", ""),
        "resolved_secondaries": scope.get("resolved_secondaries", []),
    }
