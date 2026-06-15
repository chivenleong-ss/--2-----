"""
Model 2.5: 施工真实性验证（v2.9从2.2拆分）.
Detects:
  1. 在施项目履约验证（状态+开工时间+产值+收款四维交叉）
  2. 停工/退场/停缓建早期夭折预警（开工≤180天）
  3. 签约后长期微量履约（签约>12月 且 产值转化率<30%）
  4. 签约额与实际产值严重偏离（产值转化率<50%）

Note: 未开工项目检测已由前置过滤器（main.py）承担，本模型接收过滤后数据。
"""
import pandas as pd
from datetime import datetime
from models.base_model import BaseModel
from utils.helpers import safe_float, parse_date


class Model25Construction(BaseModel):
    model_id = "2.5"
    model_name = "施工真实性验证（停工退场预警+在施履约验证+签约履约偏差）"
    priority = "P0"
    dimension = "合同质量与风险"

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        exp = self.config.get("experience_warnings", {})
        df = dmp.copy()
        findings = []
        now = datetime.now()

        for idx, row in df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            contract_amt = safe_float(row.get("签约额（元）", 0))
            project_status = str(row.get("项目状态", "")).strip()
            actual_output = safe_float(row.get("实际完成产值", 0))

            issues = []

            # ─── 1. 在施项目履约验证 ───
            is_construction = any(kw in project_status for kw in ["在施", "在建"])
            if is_construction:
                start_date = row.get("开工时间")
                if isinstance(start_date, str):
                    start_date = parse_date(start_date)
                has_start = start_date is not None and not (isinstance(start_date, float) and pd.isna(start_date))

                cumulative_receipt = safe_float(row.get("累计收款", 0))

                failures = []
                if not has_start:
                    failures.append("开工时间为空")
                if actual_output <= 0:
                    failures.append("实际完成产值=0")
                if cumulative_receipt <= 0:
                    failures.append("累计收款=0")

                if failures:
                    issues.append({
                        "type": "在施状态存疑",
                        "desc": (
                            f"项目状态为「{project_status}」，但{'、'.join(failures)}，"
                            "状态与履约数据矛盾，需核实项目是否真实在施"
                        ),
                        "severity": "red" if len(failures) >= 2 else "yellow"
                    })

            # ─── 2. 停工/退场/停缓建早期夭折检测 ───
            is_stopped = any(kw in project_status for kw in ["停工", "退场", "停缓建"])
            if is_stopped:
                start_date = row.get("开工时间")
                if isinstance(start_date, str):
                    start_date = parse_date(start_date)

                early_death = False
                days_str = ""
                if start_date is not None and not (isinstance(start_date, float) and pd.isna(start_date)):
                    days_since_start = (now - start_date).days
                    days_str = f"，开工仅{days_since_start}天即停工/退场/停缓建"
                    if days_since_start <= 180:
                        early_death = True

                issues.append({
                    "type": "停工退场停缓建预警",
                    "desc": (
                        f"项目状态为「{project_status}」{days_str}"
                        + ("，疑似早期夭折，投标决策失误风险" if early_death else
                           "，需关注停工/停缓建原因及盈利回收情况")
                    ),
                    "severity": "red" if early_death else "yellow"
                })

            # ─── 3. 签约履约偏差检测 ——
            # 仅对已有产值但产值偏低（排除已由前置过滤剔除的完全0产值项目）
            if contract_amt > 0 and actual_output > 0:
                output_ratio = actual_output / contract_amt
                sign_date = row.get("签约时间")
                if isinstance(sign_date, str):
                    sign_date = parse_date(sign_date)
                months_since_sign = None
                if sign_date is not None and not (isinstance(sign_date, float) and pd.isna(sign_date)):
                    months_since_sign = (now - sign_date).days / 30

                # 3a. 签约>12月 且 产值转化率<30% → 签约虚高或履约严重滞后
                if months_since_sign is not None and months_since_sign > 12 and output_ratio < 0.30:
                    issues.append({
                        "type": "签约后长期微量履约",
                        "desc": (
                            f"签约{months_since_sign:.0f}个月，产值转化率仅{output_ratio:.1%}"
                            f"（产值{actual_output/1e8:.2f}亿 / 签约额{contract_amt/1e8:.2f}亿），"
                            "签约虚高或履约严重滞后"
                        ),
                        "severity": "red"
                    })
                # 3b. 产值转化率<50%（排除完全未开工和已在3a覆盖的）
                elif output_ratio < 0.50:
                    issues.append({
                        "type": "签约额与产值偏离",
                        "desc": (
                            f"产值转化率仅{output_ratio:.1%}"
                            f"（产值{actual_output/1e8:.2f}亿 / 签约额{contract_amt/1e8:.2f}亿），"
                            "签约金额大于实际履约金额，存在履约风险"
                        ),
                        "severity": "yellow"
                    })

            # ─── 4. 退场原因补充（附表1·未开工或退场原因） ───
            if is_stopped:
                retreat_reason = str(row.get("未开工或退场原因", ""))
                if retreat_reason and retreat_reason not in ("nan", ""):
                    pass  # field is already in df, provides context

            for issue in issues:
                findings.append({
                    "模型编号": "2.5",
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

        summary = {
            "total_checked": len(df),
            "在施状态存疑": len(issues_df[issues_df["问题分类"].str.contains("在施")]) if len(issues_df) > 0 else 0,
            "停工退场停缓建预警": len(issues_df[issues_df["问题分类"].str.contains("停工退场")]) if len(issues_df) > 0 else 0,
            "签约履约偏差": len(issues_df[issues_df["问题分类"].str.contains("签约")]) if len(issues_df) > 0 else 0,
            "total_issues": len(issues_df),
        }

        logger.set_summary(**summary)
        logger.log_check("施工真实性验证（含签约履约偏差）", True, summary)
        self._check_completed()

        return issues_df, summary
