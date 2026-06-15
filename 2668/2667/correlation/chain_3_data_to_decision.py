"""
关联链3: 数据质量→决策可靠性提示链（相关性提示）
模型1.4(数据验真) → 影响全部模型可信度

重要说明（评委意见）：
  关联链仅做"相关性提示"，不做"因果性判断"。
  数据异常标记提示相关模型结果需审慎解读，
  不代表数据异常一定导致分析结论错误。
"""
import pandas as pd


def run_chain_3(model_outputs: dict, logger=None) -> dict:
    """
    Assess how data quality issues undermine model reliability.
    """
    r14 = model_outputs.get("1.4", (pd.DataFrame(), {}))[0]

    findings = []
    flagged_projects = set()

    if len(r14) > 0:
        flagged_projects = set(r14["项目编码"].dropna().astype(str)) if "项目编码" in r14.columns else set()

        # Count data quality issues by type
        issue_types = r14["问题分类"].value_counts().to_dict() if "问题分类" in r14.columns else {}

        findings.append({
            "关联类型": "数据验真过滤器",
            "异常项目数": len(flagged_projects),
            "异常类型分布": str(issue_types),
            "影响模型": "全部11个模型",
            "严重等级": "red",
            "建议": f"{len(flagged_projects)}个项目存在数据异常标记，建议在所有模型报告中优先展示数据可信度标记（关联提示，非因果结论）",
        })

    # Calculate data confidence score per subsidiary
    confidence_by_unit = {}
    affected_models = 0
    for model_id, (issues_df, _) in model_outputs.items():
        if model_id == "1.4" or len(issues_df) == 0:
            continue
        if "项目编码" in issues_df.columns:
            affected = set(issues_df["项目编码"].dropna().astype(str)) & flagged_projects
            if len(affected) > 0:
                affected_models += 1

    result = {
        "chain": "数据质量→决策可靠性提示链",
        "description": "识别数据验真异常是否影响其他模型结果可信度（相关性提示：数据异常标记提示相关模型结论需审慎解读）",
        "flagged_projects": len(flagged_projects),
        "affected_models": affected_models,
        "confidence_impact": "high" if len(flagged_projects) > 5 else "medium",
        "details": findings,
    }

    if logger:
        logger.log_check("数据质量→决策关联", True, {"flagged": len(flagged_projects)})

    return result
