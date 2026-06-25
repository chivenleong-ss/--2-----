"""
Diagnostic insight translator — maps machine scores to plain-language
conclusions for leadership and business departments.

Import MODEL_ADVICE from report_generator to avoid duplication.
"""
from __future__ import annotations


# ── Per-model actionable advice (shared with report_generator.py) ──
MODEL_ADVICE = {
    "1.1": "复核区域授权边界，对非授权城市、跨区域项目补齐审批依据。",
    "1.2": "将业务结构偏离纳入季度经营分析，校准新兴业务、EPC、城市更新等资源投放。",
    "1.3": "对战略客户中标未签约、签约波动项目逐项复盘，形成客户维护清单。",
    "1.4": "先补齐关键字段，再复核模型结论，避免因源数据缺口造成误判或漏判。",
    "2.1": "对触碰严禁/限制投标底线项目立即暂停增量动作，开展承接审批追溯。",
    "2.2": "对承接即亏损、效益偏差项目复核报价测算、成本边界和合同口径。",
    "2.3": "建立保证金、预收款、回款节点的双日期预警和催收台账。",
    "2.4": "将高风险合同条款纳入负面清单，推动标准合同文本和例外审批闭环。",
    "2.5": "对停工退场、在施存疑项目开展现场或台账复核，确认履约真实性。",
    "3.1": "对客户流失、中标未签约和评级下调客户建立专项跟踪机制。",
    "3.2": "完善新客户准入评估，避免低质量新客户推高后续合同和资金风险。",
    "3.3": "对长期无交易、评级不符的僵尸客户启动清理程序，同步核查客户评级内控流程。",
}

# ── Per-module diagnosis templates (module_id → human-readable insight) ──
MODULE_DIAGNOSIS = {
    1: {
        "name": "区域布局",
        "strong": "区域布局健康，跨区合规与业务结构合理。",
        "steady": "区域布局基本稳健，可关注非授权区域项目占比。",
        "pressure": "区域布局承压——可能存在跨区合规风险或业务结构偏离目标。",
    },
    2: {
        "name": "客户稳定",
        "strong": "客户结构稳定，转化与活跃度良好。",
        "steady": "客户稳定度尚可，建议关注集中度风险和流失预警。",
        "pressure": "客户稳定性不足——客户流失、集中度过高或转化质量下滑。",
    },
    3: {
        "name": "合同质量",
        "strong": "合同质量良好，未触碰底线条款。",
        "steady": "合同质量基本合规，个别条款需关注。",
        "pressure": "合同质量承压——存在严禁投标底线触碰或付款条件不达标。",
    },
    4: {
        "name": "履约盈利",
        "strong": "履约盈利健康，项目效益符合预期。",
        "steady": "履约盈利尚可，个别项目效益偏差需关注。",
        "pressure": "履约盈利承压——存在停工退场、亏损项目或效益严重偏差。",
    },
    5: {
        "name": "资金效率",
        "strong": "资金周转效率良好，保证金与回款管控到位。",
        "steady": "资金效率基本正常，建议关注逾期回款和保证金占用。",
        "pressure": "资金效率承压——保证金逾期、回款周期过长或预收款占比异常。",
    },
    6: {
        "name": "数据质量",
        "strong": "数据质量可靠，流程合规与时间逻辑完整。",
        "steady": "数据质量基本可信，个别字段需补齐。",
        "pressure": "数据质量不足——关键字段缺失或时间链条不完整，可能影响其他模块判断。",
    },
}

# ── Overall conclusion templates ──
OVERALL_LEVELS = {
    "critical": {"label": "高风险，需立即组织专项整改", "color": "#dc2626"},
    "high": {"label": "中高风险，优先处理红线事项", "color": "#cb5b4b"},
    "medium": {"label": "中风险，预警事项偏多，建议专项跟踪", "color": "#c99518"},
    "low": {"label": "低至中风险，整体可控", "color": "#2f8f61"},
    "clean": {"label": "低风险，当前未见明显异常", "color": "#2f8f61"},
}


def module_diagnosis(module_id: int, score: float) -> str:
    """Return a one-sentence plain-language diagnosis for a module score."""
    diag = MODULE_DIAGNOSIS.get(module_id, {})
    if score >= 80:
        return diag.get("strong", "该模块表现优秀。")
    if score >= 65:
        return diag.get("steady", "该模块处于中等水平，有提升空间。")
    return diag.get("pressure", "该模块需要关注和改善。")


def overall_assessment(total_score: float, red_count: int = 0) -> dict:
    """Return overall risk assessment label, color, and summary."""
    if red_count >= 10:
        level = "critical"
    elif red_count > 0:
        level = "high"
    elif total_score < 60:
        level = "medium"
    elif total_score < 75:
        level = "low"
    else:
        level = "clean"
    info = OVERALL_LEVELS[level]
    return {"level": level, "label": info["label"], "color": info["color"]}


def module_advice(module_index: int) -> str:
    """Get suggested action for a module (1-indexed).

    Maps module index to the most relevant model advice.
    """
    mapping = {
        1: "1.2",  # 业务结构
        2: "3.1",  # 客户监控
        3: "2.1",  # 合同底线
        4: "2.2",  # 盈利底线
        5: "2.3",  # 资金安全
        6: "1.4",  # 数据质量
    }
    model_id = mapping.get(module_index, "")
    return MODEL_ADVICE.get(model_id, "建议纳入专项整改台账并持续跟踪。")


def get_module_insight(module_id: int, score: float) -> dict:
    """Get full insight for a module: diagnosis + suggested action."""
    return {
        "diagnosis": module_diagnosis(module_id, score),
        "advice": module_advice(module_id),
        "tone": "strong" if score >= 80 else ("steady" if score >= 65 else "pressure"),
    }
