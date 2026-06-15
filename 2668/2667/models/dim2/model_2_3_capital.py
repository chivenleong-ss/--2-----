"""
Model 2.3: Guarantee & advance-payment fund safety monitoring.

Uses:
- DMP for guarantee arrangement / cash guarantee identification.
- Appendix 1 merged finance fields for actual receipts, balances and due dates.
"""
from datetime import datetime

import pandas as pd

from models.base_model import BaseModel
from utils.helpers import parse_date, safe_float


class Model23Capital(BaseModel):
    model_id = "2.3"
    model_name = "保证金与预收款资金安全监控"
    priority = "P0"
    dimension = "合同质量与风险"

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        exp = self.config.get("experience_warnings", {})
        major_risk_days = exp.get("保证金逾期_重大风险天数", 90)
        payment_config = self.config.get("institutional", {}).get("付款条件底线", {})
        payment_new = payment_config.get("手册0520", payment_config)
        cash_perf_limit = payment_new.get("现金履约保证金_二级单位审批上限_万元", 100)

        df = dmp.copy()
        findings = []
        today = datetime.now()

        for _, row in df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            contract_amt = safe_float(row.get("签约额（元）", 0))
            issues = []

            bid_guarantee_type = str(row.get("投标担保方式", "")).strip()
            bid_guarantee_amt = safe_float(row.get("投标担保金额（万元）", 0))
            if "现金" in bid_guarantee_type and bid_guarantee_amt > 0:
                issues.append({
                    "type": "现金投标保证金",
                    "desc": f"现金投标保证金 {bid_guarantee_amt:.2f} 万元",
                    "amount": bid_guarantee_amt,
                    "severity": "yellow",
                })

            perf_type = str(row.get("履约担保方式", "")).strip()
            perf_amt = safe_float(row.get("履约担保金额（万元）", 0))
            if "现金" in perf_type and perf_amt > 0:
                issues.append({
                    "type": "现金履约保证金",
                    "desc": f"现金履约保证金 {perf_amt:.2f} 万元",
                    "amount": perf_amt,
                    "severity": "red" if perf_amt >= cash_perf_limit else "yellow",
                })

            guarantee_due = _to_datetime(row.get("保证金约定退还日期"))
            guarantee_actual = _to_datetime(row.get("保证金实际回收日期"))
            if guarantee_due is not None and guarantee_due < today and guarantee_actual is None:
                overdue_days = (today - guarantee_due).days
                issues.append({
                    "type": "保证金逾期未回收",
                    "desc": f"保证金约定退还日期为 {guarantee_due:%Y-%m-%d}，截至目前已逾期 {overdue_days} 天仍未记录回收",
                    "amount": perf_amt,
                    "severity": "red" if overdue_days >= major_risk_days else "yellow",
                })
            elif guarantee_due is not None and guarantee_actual is not None and guarantee_actual > guarantee_due:
                overdue_days = (guarantee_actual - guarantee_due).days
                if overdue_days > 0:
                    issues.append({
                        "type": "保证金延期回收",
                        "desc": f"保证金实际回收日期 {guarantee_actual:%Y-%m-%d} 晚于约定日期 {guarantee_due:%Y-%m-%d}，延迟 {overdue_days} 天",
                        "amount": perf_amt,
                        "severity": "red" if overdue_days >= major_risk_days else "yellow",
                    })

            has_advance = str(row.get("是否有预付款", "")).strip()
            advance_pct = safe_float(row.get("预付款比例（%）", 0))
            adv_receivable = safe_float(row.get("预收款应收款", 0))
            adv_received = safe_float(row.get("预收款实收款", 0))
            adv_due = _to_datetime(row.get("预收款约定支付日期"))
            adv_actual = _to_datetime(row.get("预收款实际收款日期"))

            if has_advance == "是" or advance_pct > 0 or adv_receivable > 0:
                if adv_receivable > 0 and adv_received < adv_receivable:
                    shortfall = adv_receivable - adv_received
                    issues.append({
                        "type": "预收款逾期",
                        "desc": f"预收款应收 {adv_receivable:.0f} 元，实收 {adv_received:.0f} 元，差额 {shortfall:.0f} 元",
                        "amount": shortfall,
                        "severity": "red" if shortfall > 1_000_000 else "yellow",
                    })

                if adv_due is not None and adv_due < today and adv_actual is None:
                    overdue_days = (today - adv_due).days
                    issues.append({
                        "type": "预收款逾期未收",
                        "desc": f"预收款约定支付日期为 {adv_due:%Y-%m-%d}，截至目前已逾期 {overdue_days} 天仍未记录收款",
                        "amount": max(adv_receivable - adv_received, 0),
                        "severity": "red" if overdue_days >= major_risk_days else "yellow",
                    })
                elif adv_due is not None and adv_actual is not None and adv_actual > adv_due:
                    overdue_days = (adv_actual - adv_due).days
                    if overdue_days > 0:
                        issues.append({
                            "type": "预收款延期到账",
                            "desc": f"预收款实际收款日期 {adv_actual:%Y-%m-%d} 晚于约定日期 {adv_due:%Y-%m-%d}，延迟 {overdue_days} 天",
                            "amount": adv_received,
                            "severity": "red" if overdue_days >= major_risk_days else "yellow",
                        })

            is_consortium = str(row.get("是否联合体投标", "")).strip() == "是"
            if is_consortium and perf_amt > 0 and contract_amt > 0:
                perf_ratio = (perf_amt * 10000) / contract_amt
                if perf_ratio > 0.10:
                    issues.append({
                        "type": "联合体超额担保",
                        "desc": f"联合体项目履约担保 {perf_amt:.2f} 万元，占合同额 {perf_ratio:.1%}",
                        "amount": perf_amt,
                        "severity": "red",
                    })

            cash_balance = safe_float(row.get("资金结余", 0))
            if cash_balance < 0:
                neg_reason = str(row.get("负流原因分析", ""))
                issues.append({
                    "type": "资金负流",
                    "desc": f"资金结余 {cash_balance:.0f} 元。{neg_reason[:100]}",
                    "amount": abs(cash_balance),
                    "severity": "red" if abs(cash_balance) > 10_000_000 else "yellow",
                })

            for issue in issues:
                findings.append({
                    "模型编号": "2.3",
                    "项目编码": proj_code,
                    "项目名称": proj_name,
                    "问题分类": issue["type"],
                    "严重等级": issue["severity"],
                    "问题描述": issue["desc"],
                    "涉及金额": issue["amount"],
                })

        issues_df = pd.DataFrame(findings)
        if len(issues_df) > 0:
            issues_df = issues_df.sort_values("严重等级")

        summary = {
            "total_checked": len(df),
            "现金保证金": len(issues_df[issues_df["问题分类"].str.contains("保证金", na=False)]) if len(issues_df) > 0 else 0,
            "保证金逾期": len(issues_df[issues_df["问题分类"].str.contains("保证金逾期|保证金延期", na=False)]) if len(issues_df) > 0 else 0,
            "预收款逾期": len(issues_df[issues_df["问题分类"].str.contains("预收款", na=False)]) if len(issues_df) > 0 else 0,
            "联合体担保": len(issues_df[issues_df["问题分类"].str.contains("联合体", na=False)]) if len(issues_df) > 0 else 0,
            "资金负流": len(issues_df[issues_df["问题分类"] == "资金负流"]) if len(issues_df) > 0 else 0,
            "total_issues": len(issues_df),
        }

        logger.set_summary(**summary)
        logger.log_check("保证金风险检查", True, {"count": summary["现金保证金"]})
        logger.log_check("保证金逾期检查", True, {"count": summary["保证金逾期"]})
        logger.log_check("预收款逾期检查", True, {"count": summary["预收款逾期"]})
        logger.log_check("联合体担保检查", True, {"count": summary["联合体担保"]})
        self._check_completed()

        return issues_df, summary


def _to_datetime(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    return parse_date(str(value))
