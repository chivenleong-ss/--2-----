"""
关联链2: 客户→合同质量关联提示链（相关性提示，非因果推断）
模型1.3/3.1(客户) → 模型2.2(盈利) → 模型2.3(资金安全)
识别客户流失、战略客户波动是否同步伴随盈利和资金回收异常。

重要说明（评委意见）：
  关联链仅做"相关性提示"，不做"因果性判断"。
  资金回收困难可能因业主自身流动性问题、合同约定回款周期等，
  不一定因客户流失导致。本链仅提示多个异常同时出现，供审计进一步核查。
"""
import pandas as pd


def run_chain_2(model_outputs: dict, logger=None) -> dict:
    """
    Cross-reference customer issues with contract quality issues.
    """
    r13 = model_outputs.get("1.3", (pd.DataFrame(), {}))[0]
    r31 = model_outputs.get("3.1", (pd.DataFrame(), {}))[0]
    r22 = model_outputs.get("2.2", (pd.DataFrame(), {}))[0]
    r23 = model_outputs.get("2.3", (pd.DataFrame(), {}))[0]

    findings = []

    # Customer churn (3.1) linked to fund issues (2.3)
    if len(r31) > 0 and len(r23) > 0:
        churn_issues = r31[r31["问题分类"].astype(str).str.contains("流失|预警", na=False)]
        if len(churn_issues) > 0:
            # Model 3.1 output uses "申报单位" column, not "客户名称"
            affected_units = set(churn_issues["申报单位"].dropna().astype(str)) if "申报单位" in churn_issues.columns else set()
            findings.append({
                "关联类型": "客户流失→资金风险敞口",
                "流失预警单位数": len(affected_units),
                "资金安全问题项目数": len(r23),
                "分析结论": "客户流失预警与项目保证金/预收款回收困难同步出现（相关性提示，需审计核查确认因果方向）",
                "严重等级": "red" if len(churn_issues) >= 3 else "yellow",
            })

    # Customer concentration (1.3) linked to profit dependency
    if len(r13) > 0 and len(r22) > 0:
        concentration_issues = r13[r13["问题分类"].astype(str).str.contains("集中", na=False)]
        if len(concentration_issues) > 0:
            findings.append({
                "关联类型": "客户集中→盈利脆弱",
                "客户集中度预警": "是",
                "客户集中度问题数": len(concentration_issues),
                "盈利预警项目数": len(r22),
                "分析结论": "客户过度集中与盈利预警同步出现，单一客户风险可能影响整体盈利稳定性（相关性提示，需结合具体项目合同条款核查）",
                "严重等级": "yellow",
            })

    result = {
        "chain": "客户→合同质量关联提示链",
        "description": "识别客户流失、战略客户波动是否同步伴随盈利和资金回收异常（相关性提示，非因果推断）",
        "total_correlations": len(findings),
        "details": findings,
    }

    if logger:
        logger.log_check("客户→合同关联", True, {"correlations_found": len(findings)})

    return result
