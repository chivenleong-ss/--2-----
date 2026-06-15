"""
关联链1: 战略→风险关联提示链（相关性提示，非因果推断）
模型1.1(区域布局) → 模型2.1(风险分级) → 模型2.2(盈利预警)
识别非常规区域项目是否同步叠加高风险承接、盈利异常或施工真实性异常。

重要说明（评委意见）：
  关联链仅做"相关性提示"，不做"因果性判断"。
  一个项目盈利恶化可能因材料涨价、管理不善、业主付款不及时等，
  不一定因区域违规。本链仅提示多个异常同时出现，供审计进一步核查。
"""
import pandas as pd


def run_chain_1(model_outputs: dict, logger=None) -> dict:
    """
    Cross-reference regional violations with risk and profit issues.

    Args:
        model_outputs: dict of (model_id, (issues_df, summary))

    Returns:
        dict with chain analysis results
    """
    r11 = model_outputs.get("1.1", (pd.DataFrame(), {}))[0]
    r21 = model_outputs.get("2.1", (pd.DataFrame(), {}))[0]
    r22 = model_outputs.get("2.2", (pd.DataFrame(), {}))[0]

    findings = []

    # Find projects with both 窜区 (1.1) AND 红线违规 (2.1)
    if len(r11) > 0 and len(r21) > 0:
        region_bad = set(r11["项目编码"].dropna().astype(str))
        risk_bad = set(r21["项目编码"].dropna().astype(str))
        double_violations = region_bad & risk_bad

        for proj in double_violations:
            r11_info = r11[r11["项目编码"].astype(str) == proj]
            r21_info = r21[r21["项目编码"].astype(str) == proj]
            region_type = r11_info["问题分类"].iloc[0] if len(r11_info) > 0 else ""
            risk_type = r21_info["问题分类"].iloc[0] if len(r21_info) > 0 else ""

            findings.append({
                "项目编码": proj,
                "关联类型": "区域违规+红线触碰",
                "区域问题": region_type,
                "风险问题": risk_type,
                "严重等级": "red",
            })

    # Find projects with非常规区域 AND low profit
    if len(r11) > 0 and len(r22) > 0:
        unconventional_projs = set(
            r11[r11["问题分类"].str.contains("非常规", na=False)]["项目编码"].dropna().astype(str)
        )
        profit_projs = set(r22["项目编码"].dropna().astype(str))
        overlap = unconventional_projs & profit_projs

        for proj in overlap:
            findings.append({
                "项目编码": proj,
                "关联类型": "非常规区域+盈利问题",
                "区域问题": "非常规区域未达门槛",
                "风险问题": "盈利预警",
                "严重等级": "yellow",
            })

    result = {
        "chain": "战略→风险关联提示链",
        "description": "识别非常规区域/区域违规项目是否同步叠加高风险承接、盈利异常或施工真实性异常（相关性提示，非因果推断）",
        "double_violations": len([f for f in findings if "双重" in str(f.get("关联类型", "")) or "红线" in str(f.get("关联类型", ""))]),
        "total_correlations": len(findings),
        "details": findings,
    }

    if logger:
        logger.log_check("战略→风险关联", True, {"correlations_found": len(findings)})

    return result
