"""
Unified project lifecycle status for marketing audit pipeline.

Sources (priority):
1. Audit appendix 项目状态 (when matched)
2. Inferred rules (e.g. signed in DMP but no appendix row → 未开工)
3. Pre-filter 3D signals (开工时间 / 产值 / 收款)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Iterable

import pandas as pd

from utils.helpers import safe_float


class UnifiedProjectStatus(StrEnum):
    UNSIGNED = "未签约"
    NOT_STARTED = "未开工"
    IN_PROGRESS = "在施"
    STOPPED = "停工"
    EXITED = "退场"
    SLOWED = "停缓建"
    COMPLETED_UNSETTLED = "已完未竣"   # 附表标记完工
    COMPLETED_UNCLOSED = "竣工未结"    # 附表标记已竣工
    COMPLETED_CLOSED = "竣工已结"      # 附表标记已结算


APPENDIX_STATUS_MAP = {
    # 在施 / 在建
    "在建": UnifiedProjectStatus.IN_PROGRESS,
    "在施": UnifiedProjectStatus.IN_PROGRESS,
    "正常施工": UnifiedProjectStatus.IN_PROGRESS,
    # 未开工
    "未开工": UnifiedProjectStatus.NOT_STARTED,
    # 停工 / 退场 / 停缓建
    "停工": UnifiedProjectStatus.STOPPED,
    "退场": UnifiedProjectStatus.EXITED,
    "停缓建": UnifiedProjectStatus.SLOWED,
    # 完工 → 已完未竣
    "完工": UnifiedProjectStatus.COMPLETED_UNSETTLED,
    "已完未竣": UnifiedProjectStatus.COMPLETED_UNSETTLED,
    # 竣工 → 竣工未结（默认）
    "竣工": UnifiedProjectStatus.COMPLETED_UNCLOSED,
    "已竣工": UnifiedProjectStatus.COMPLETED_UNCLOSED,
    "竣工未结": UnifiedProjectStatus.COMPLETED_UNCLOSED,
    "已竣未结": UnifiedProjectStatus.COMPLETED_UNCLOSED,
    # 竣工已结
    "竣工已结": UnifiedProjectStatus.COMPLETED_CLOSED,
    "已结算": UnifiedProjectStatus.COMPLETED_CLOSED,
    "竣结": UnifiedProjectStatus.COMPLETED_CLOSED,
    "竣工结算": UnifiedProjectStatus.COMPLETED_CLOSED,
    "完工已竣": UnifiedProjectStatus.COMPLETED_UNCLOSED,  # 已完工且已竣工，默认未结算
    "已完已竣": UnifiedProjectStatus.COMPLETED_UNCLOSED,
}

OPERATIONAL_TERMINATION_STATUSES = {
    UnifiedProjectStatus.STOPPED,
    UnifiedProjectStatus.EXITED,
    UnifiedProjectStatus.SLOWED,
}

_COMPLETION_STATUSES = {
    UnifiedProjectStatus.COMPLETED_UNSETTLED,
    UnifiedProjectStatus.COMPLETED_UNCLOSED,
    UnifiedProjectStatus.COMPLETED_CLOSED,
}

UNIFIED_STATUS_COLUMN = "统一项目状态"
APPENDIX_MATCH_COLUMN = "附表已匹配"


def normalize_appendix_status(raw_status: str) -> UnifiedProjectStatus | None:
    text = str(raw_status or "").strip()
    if not text or text.lower() == "nan":
        return None
    if text in APPENDIX_STATUS_MAP:
        return APPENDIX_STATUS_MAP[text]
    for keyword, status in APPENDIX_STATUS_MAP.items():
        if keyword in text:
            return status
    return None


def is_operational_termination(status: str) -> bool:
    try:
        return UnifiedProjectStatus(str(status).strip()) in OPERATIONAL_TERMINATION_STATUSES
    except ValueError:
        return any(keyword in str(status) for keyword in ("停工", "退场", "停缓建"))


def is_not_started_status(status: str) -> bool:
    return str(status).strip() == UnifiedProjectStatus.NOT_STARTED


@dataclass
class ProjectStatusAssessment:
    unified_status: str
    prefilter_exclude: bool = False
    prefilter_keep_for_profit_analysis: bool = False
    reasons: list[str] = field(default_factory=list)
    status_source: str = ""
    cross_check_warning: bool = False


def assess_three_dimension_unstarted(
    row: pd.Series,
    *,
    now: datetime | None = None,
    has_output: bool = True,
    has_receipt: bool = True,
) -> tuple[bool, list[str]]:
    """Return whether 3D rule says unstarted, plus reason fragments."""
    now = now or datetime.now()
    reasons: list[str] = []

    start = row.get("开工时间")
    sign = row.get("签约时间")
    if sign is None or (isinstance(sign, float) and pd.isna(sign)):
        return False, reasons

    try:
        sign_dt = pd.Timestamp(sign)
    except Exception:
        return False, reasons

    days_since_sign = (now - sign_dt).days
    has_start = start is not None and not (isinstance(start, float) and pd.isna(start))
    if not has_start:
        reasons.append("无开工时间")

    output = safe_float(row.get("实际完成产值", 0)) if has_output else None
    no_output_180 = (days_since_sign > 180) and (output is None or output <= 0)
    if no_output_180:
        reasons.append(f"签约{days_since_sign}天无产值")

    receipt = safe_float(row.get("累计收款", 0)) if has_receipt else None
    no_receipt_360 = (days_since_sign > 360) and (receipt is None or receipt <= 0)
    if no_receipt_360:
        reasons.append(f"签约{days_since_sign}天无收款")

    if has_output and has_receipt:
        is_unstarted = no_output_180 and no_receipt_360
    elif has_output:
        is_unstarted = no_output_180 and not has_start
    else:
        is_unstarted = not has_start and days_since_sign > 180

    return is_unstarted, reasons


def assess_project_status(
    row: pd.Series,
    *,
    has_output: bool = True,
    has_receipt: bool = True,
    has_status: bool = True,
    now: datetime | None = None,
) -> ProjectStatusAssessment:
    """Resolve unified lifecycle status and pre-filter disposition for one project."""
    sign = row.get("签约时间")
    if sign is None or (isinstance(sign, float) and pd.isna(sign)):
        return ProjectStatusAssessment(
            unified_status=UnifiedProjectStatus.UNSIGNED,
            status_source="无签约时间",
        )

    appendix_matched = bool(row.get(APPENDIX_MATCH_COLUMN, False))
    raw_status = str(row.get("项目状态", "")).strip() if has_status else ""
    mapped_status = normalize_appendix_status(raw_status) if appendix_matched else None

    if appendix_matched and mapped_status in OPERATIONAL_TERMINATION_STATUSES:
        return ProjectStatusAssessment(
            unified_status=mapped_status,
            prefilter_keep_for_profit_analysis=True,
            status_source="附表项目状态",
            reasons=[raw_status] if raw_status else [str(mapped_status)],
        )

    if appendix_matched and mapped_status == UnifiedProjectStatus.NOT_STARTED:
        return ProjectStatusAssessment(
            unified_status=UnifiedProjectStatus.NOT_STARTED,
            prefilter_exclude=True,
            status_source="附表项目状态",
            reasons=["附表标记为未开工"],
        )

    if appendix_matched and mapped_status == UnifiedProjectStatus.IN_PROGRESS:
        # Cross-verify: 附表标记"在施"但产值/收款/开工信号显示可能未开工
        is_unstarted_3d, cross_reasons = assess_three_dimension_unstarted(
            row,
            now=now,
            has_output=has_output,
            has_receipt=has_receipt,
        )
        if is_unstarted_3d:
            return ProjectStatusAssessment(
                unified_status=UnifiedProjectStatus.IN_PROGRESS,
                status_source="附表在施+三维交叉验证存疑",
                reasons=cross_reasons,
                cross_check_warning=True,
            )
        return ProjectStatusAssessment(
            unified_status=UnifiedProjectStatus.IN_PROGRESS,
            status_source="附表项目状态",
        )

    if appendix_matched and mapped_status in _COMPLETION_STATUSES:
        return ProjectStatusAssessment(
            unified_status=mapped_status,
            status_source="附表项目状态",
        )

    if not appendix_matched:
        return ProjectStatusAssessment(
            unified_status=UnifiedProjectStatus.NOT_STARTED,
            prefilter_exclude=True,
            status_source="附表缺失推断",
            reasons=["签约报量有记录、审计附表无匹配"],
        )

    is_unstarted, reasons = assess_three_dimension_unstarted(
        row,
        now=now,
        has_output=has_output,
        has_receipt=has_receipt,
    )
    if is_unstarted:
        return ProjectStatusAssessment(
            unified_status=UnifiedProjectStatus.NOT_STARTED,
            prefilter_exclude=True,
            status_source="前置三维检测",
            reasons=reasons,
        )

    if mapped_status:
        return ProjectStatusAssessment(
            unified_status=mapped_status,
            status_source="附表项目状态",
        )

    return ProjectStatusAssessment(
        unified_status=UnifiedProjectStatus.IN_PROGRESS,
        status_source="默认在施",
    )


def apply_unified_status_columns(
    df: pd.DataFrame,
    assessments: Iterable[ProjectStatusAssessment],
) -> pd.DataFrame:
    out = df.copy()
    assessment_list = list(assessments)
    out[UNIFIED_STATUS_COLUMN] = [item.unified_status for item in assessment_list]
    out["_status_source"] = [item.status_source for item in assessment_list]
    return out
