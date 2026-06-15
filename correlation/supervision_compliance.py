"""
Business supervision compliance mapping — 手册0520 附件6/7.

Maps audit findings from all 11 models to the 26 official violation categories
defined in handbook 0520 附件6, then assigns disposal measures per 附件7.

This is a cross-cutting post-processing module, not a detection model.
It translates technical audit findings into the official compliance framework.
"""
import pandas as pd
import json
from pathlib import Path


# Mapping from model findings to 附件6 violation categories
# Key: (model_id, issue_type) → Value: violation_category_id
FINDING_TO_VIOLATION = {
    # 模型1.1 区域
    ("1.1", "窜区"): "3_区域管理",
    ("1.1", "非常规区域未达门槛"): "3_区域管理",
    # 模型2.1 风险分级
    ("2.1", "严禁投标红线"): "1_投标与立项管理",
    ("2.1", "付款条件不达标"): "1_投标与立项管理",
    ("2.1", "预期净利润率不达标"): "1_投标与立项管理",
    ("2.1", "付款条件标记校验不一致"): "6_营销统计管理",  # v2.9: DMP标记与制度校验不符
    # 模型2.3 资金安全（保证金逾期涉及资信管理）
    ("2.3", "保证金逾期"): "2_客户与信用维护",
    # 模型3.2 客户质量
    ("3.2", "客户评级不合格"): "2_客户与信用维护",
    ("3.2", "战略客户占比不达标"): "2_客户与信用维护",
    # 模型1.4 数据质量
    ("1.4", "招文评审与交标时间倒置"): "6_营销统计管理",
    ("1.4", "招文领取与交标时间倒置"): "6_营销统计管理",      # v2.9新增
    ("1.4", "交标与中标时间倒置"): "6_营销统计管理",          # v2.9新增
    ("1.4", "中标与签约报量时间倒置"): "6_营销统计管理",      # v2.9新增
    ("1.4", "签约报量与签约时间倒置"): "6_营销统计管理",      # v2.9新增
    ("1.4", "投标利润率规律性异常"): "6_营销统计管理",
    ("1.4", "疑似拆包"): "6_营销统计管理",
}

# Default violation category per model when no specific issue type match
MODEL_DEFAULT_CATEGORY = {
    "1.1": "3_区域管理",
    "1.2": "3_区域管理",
    "1.3": "2_客户与信用维护",
    "1.4": "6_营销统计管理",
    "2.1": "1_投标与立项管理",
    "2.2": "1_投标与立项管理",
    "2.3": "2_客户与信用维护",
    "2.4": "1_投标与立项管理",
    "3.1": "5_营销绩效管理",
    "3.2": "2_客户与信用维护",
    "3.3": "1_投标与立项管理",
}


def map_findings_to_violations(all_results: dict, config: dict) -> pd.DataFrame:
    """
    Map all model findings to 附件6 violation categories.

    Returns DataFrame with columns:
      模型编号, 项目编码, 项目名称, 申报单位, 问题分类, 严重等级,
      违规类别, 违规编号, 违规描述, 违规等级, 处置等级, 处置措施
    """
    categories = config.get("supervision_compliance", {}).get("categories", {})
    disposal = config.get("supervision_compliance", {}).get("disposal_levels", {})

    rows = []
    for model_id, (issues_df, _) in all_results.items():
        if issues_df.empty:
            continue

        for _, row in issues_df.iterrows():
            issue_type = str(row.get("问题分类", ""))
            severity = str(row.get("严重等级", "yellow"))

            # Find matching violation category
            violation_cat_key = FINDING_TO_VIOLATION.get((model_id, issue_type))
            if violation_cat_key is None:
                # Try partial match on issue type
                for (mid, itype), cat in FINDING_TO_VIOLATION.items():
                    if mid == model_id and itype in issue_type:
                        violation_cat_key = cat
                        break

            if violation_cat_key is None:
                violation_cat_key = MODEL_DEFAULT_CATEGORY.get(model_id, "1_投标与立项管理")

            cat_data = categories.get(violation_cat_key, {})

            # Determine violation sub-category based on severity
            # red/critical findings → higher severity sub-category
            violation_id = _pick_violation_id(cat_data, severity)
            violation_info = cat_data.get(violation_id, {})

            if isinstance(violation_info, dict):
                violation_desc = violation_info.get("desc", "")
                violation_level = violation_info.get("level", "一般")
            else:
                violation_desc = ""
                violation_level = "一般"

            disp = disposal.get(violation_level, {})
            disposal_level = disp.get("level", "")
            disposal_measures = "；".join(disp.get("measures", []))

            rows.append({
                "模型编号": model_id,
                "项目编码": row.get("项目编码", ""),
                "项目名称": row.get("项目名称", ""),
                "申报单位": row.get("申报单位", ""),
                "问题分类": issue_type,
                "审计严重等级": severity,
                "违规类别": violation_cat_key.replace("_", " "),
                "违规编号": violation_id,
                "违规描述": violation_desc,
                "违规等级": violation_level,
                "处置等级": disposal_level,
                "处置措施": disposal_measures,
            })

    return pd.DataFrame(rows)


def _pick_violation_id(cat_data: dict, audit_severity: str) -> str:
    """Pick the most appropriate violation sub-category given audit severity."""
    if not cat_data:
        return ""

    # Separate sub-categories by their official level
    levels = {"一般": [], "较大": [], "重大": []}
    for key, info in cat_data.items():
        if isinstance(info, dict):
            lvl = info.get("level", "一般")
            levels[lvl].append(key)

    # Map audit severity to violation level
    if "red" in audit_severity.lower() or "严禁" in audit_severity:
        # Red findings → 较大 or 重大
        if levels["重大"]:
            return levels["重大"][0]
        if levels["较大"]:
            return levels["较大"][0]
    elif "yellow" in audit_severity.lower():
        if levels["较大"]:
            return levels["较大"][0]
        if levels["一般"]:
            return levels["一般"][0]

    # Default: first available sub-category
    for lvl in ["一般", "较大", "重大"]:
        if levels[lvl]:
            return levels[lvl][0]
    return list(cat_data.keys())[0]


def generate_compliance_summary(compliance_df: pd.DataFrame) -> dict:
    """Generate summary statistics for the compliance report."""
    if compliance_df.empty:
        return {"total_violations": 0, "by_category": {}, "by_level": {}, "by_disposal": {}}

    summary = {
        "total_violations": len(compliance_df),
        "by_category": compliance_df["违规类别"].value_counts().to_dict(),
        "by_level": compliance_df["违规等级"].value_counts().to_dict(),
        "by_disposal": compliance_df["处置等级"].value_counts().to_dict(),
        "重大_violations": len(compliance_df[compliance_df["违规等级"] == "重大"]),
        "较大_violations": len(compliance_df[compliance_df["违规等级"] == "较大"]),
        "一般_violations": len(compliance_df[compliance_df["违规等级"] == "一般"]),
    }
    return summary
