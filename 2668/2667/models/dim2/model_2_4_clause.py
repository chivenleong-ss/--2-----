"""
Model 2.4: Contract Clause Risk Penetration (合同条款风险穿透).
NLP-style keyword analysis of contract clause text for high-risk terms.
"""
import pandas as pd
from models.base_model import BaseModel
from utils.helpers import safe_float


class Model24Clause(BaseModel):
    model_id = "2.4"
    model_name = "合同条款风险穿透"
    priority = "P1"
    dimension = "合同质量与风险"

    HIGH_RISK_KEYWORDS = [
        "无上限", "无限", "一切损失", "全部损失", "所有损失",
        "无条件", "放弃", "无权", "不得",
    ]

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        df = dmp.copy()

        findings = []

        for idx, row in df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            contract_amt = safe_float(row.get("签约额（元）", 0))

            issues = []

            # 1. Check承包人违约责任 for unlimited penalties
            contractor_liability = str(row.get("承包人的违约责任", ""))
            has_unlimited = any(kw in contractor_liability for kw in ["无上限", "无限", "一切损失"])
            if has_unlimited:
                issues.append({
                    "type": "承包人违约责任无上限",
                    "desc": "承包人违约责任条款包含无上限/无限/一切损失表述",
                    "severity": "red"
                })

            # 2. Check工期违约金 upper limit
            penalty_clause = str(row.get("工期奖罚条款", ""))
            if "无上限" in penalty_clause or "不设上限" in penalty_clause:
                issues.append({
                    "type": "工期违约金无上限",
                    "desc": "工期违约金条款无上限",
                    "severity": "red"
                })

            # 3. Check发包人违约责任 for虛化 (only references general terms)
            owner_liability = str(row.get("发包人的违约责任", ""))
            has_substantive = len(owner_liability) > 50 and not all(
                kw in owner_liability for kw in ["按通用条款执行", "/"]
            )
            if not has_substantive and "违约责任" in owner_liability:
                issues.append({
                    "type": "发包人违约责任虚化",
                    "desc": "发包人违约责任条款缺乏实质内容，仅引用通用条款",
                    "severity": "yellow"
                })

            # 4.放弃优先受偿权
            if str(row.get("是否放弃优先受偿权", "")).strip() == "是":
                issues.append({
                    "type": "放弃优先受偿权",
                    "desc": "合同约定放弃优先受偿权",
                    "severity": "red"
                })

            # 5.停缓建权利
            no_stop = str(row.get("发包方是否无条件禁止承包方停/缓建", "")).strip() == "是"
            no_claim = str(row.get("承包方因发包方无条件停缓建是否有索赔权利", "")).strip() == "否"
            if no_stop and no_claim:
                issues.append({
                    "type": "极端不利停缓建条款",
                    "desc": "禁止停缓建且无索赔权利",
                    "severity": "red"
                })

            # 6.三证不全已开工
            planning = str(row.get("是否有规划许可证", "")).strip()
            land_use = str(row.get("是否有建设用地许可证", "")).strip()
            land_own = str(row.get("是否有土地使用证", "")).strip()
            is_started = row.get("开工时间") is not None and str(row.get("开工时间", "")).strip() not in ("", "/", "N/A")

            if is_started and (planning == "否" or land_use == "否" or land_own == "否"):
                issues.append({
                    "type": "三证不全已开工",
                    "desc": f"规划:{planning} 建设:{land_use} 土地:{land_own} — 实质性开工前三证不全",
                    "severity": "red"
                })

            # 7. 质保金 high ratio
            warranty_pct = safe_float(row.get("质保金支付比例(%)", 0))
            if warranty_pct > 5:
                issues.append({
                    "type": "质保金比例偏高",
                    "desc": f"质保金{warranty_pct:.0f}% > 5%",
                    "severity": "yellow"
                })

            # 8.结算周期过长
            settlement_months = safe_float(row.get("结算周期（月）", 0))
            if settlement_months > 6:
                issues.append({
                    "type": "结算周期过长",
                    "desc": f"结算周期{settlement_months}月 > 6月",
                    "severity": "yellow"
                })

            for issue in issues:
                findings.append({
                    "模型编号": "2.4",
                    "项目编码": proj_code,
                    "项目名称": proj_name,
                    "问题分类": issue["type"],
                    "严重等级": issue["severity"],
                    "问题描述": issue["desc"],
                    "签约额（元）": contract_amt,
                })

        issues_df = pd.DataFrame(findings)
        if len(issues_df) > 0:
            issues_df = issues_df.sort_values("严重等级")

        red_count = len(issues_df[issues_df["严重等级"] == "red"]) if len(issues_df) > 0 else 0
        summary = {
            "total_checked": len(df),
            "red_clauses": red_count,
            "yellow_clauses": len(issues_df) - red_count if len(issues_df) > 0 else 0,
            "total_issues": len(issues_df),
        }

        logger.set_summary(**summary)
        logger.log_check("承包人违约责任检查", True, {"red": red_count})
        self._check_completed()

        return issues_df, summary
