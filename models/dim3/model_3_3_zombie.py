"""Model 3.3: Zombie customer cleanup and rating control checks."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from models.base_model import BaseModel
from utils.helpers import safe_float


class Model33Zombie(BaseModel):
    model_id = "3.3"
    model_name = "僵尸客户清理与客户评级内控核查"
    priority = "P3"
    dimension = "客户健康度"

    @staticmethod
    def _parse_datetime(val):
        """Safely parse mixed date-like values."""
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            text = val.strip()
            if not text or text.lower() == "nat":
                return None
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y%m%d"):
                try:
                    return datetime.strptime(text[: len(fmt)], fmt)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _is_yes(val) -> bool:
        return str(val).strip() in {"是", "Y", "YES", "True", "true", "1"}

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        df = dmp.copy()
        findings = []

        if "客户名称" not in df.columns:
            logger.log_warning("缺少客户名称字段，无法执行客户健康度检测")
            summary = {"total_issues": 0, "data_source": "DMP签约报量 + 中标报量"}
            logger.set_summary(**summary)
            self._check_completed()
            return pd.DataFrame(), summary

        now = datetime.now()

        customer_latest = pd.Series(dtype="object")
        if "中标时间" in df.columns:
            customer_latest = df.groupby("客户名称")["中标时间"].max()
            for cust, latest in customer_latest.items():
                parsed = self._parse_datetime(latest)
                if parsed is None:
                    continue
                months = (now - parsed).days / 30
                if months > 24:
                    findings.append(
                        {
                            "模型编号": "3.3",
                            "客户名称": cust,
                            "问题分类": "僵尸客户",
                            "严重等级": "red",
                            "问题描述": f"{cust} 连续 {months:.0f} 个月无中标产出，最近记录 {parsed.strftime('%Y-%m')}",
                        }
                    )

        if "签约时间" in df.columns:
            zombie_threshold_days = 180
            for _, row in df.iterrows():
                sign_dt = self._parse_datetime(row.get("签约时间"))
                if sign_dt is None:
                    continue
                start_dt = self._parse_datetime(row.get("开工时间"))
                if start_dt is not None:
                    continue
                days_since_sign = (now - sign_dt).days
                if days_since_sign > zombie_threshold_days:
                    findings.append(
                        {
                            "模型编号": "3.3",
                            "项目名称": str(row.get("项目名称", "")),
                            "客户名称": str(row.get("客户名称", "")),
                            "问题分类": "已签约未开工僵尸项目",
                            "严重等级": "red",
                            "问题描述": f"项目签约后 {days_since_sign} 天仍未开工，签约日 {sign_dt.strftime('%Y-%m-%d')}",
                        }
                    )

        if "中标额（元）" in df.columns and "中标报量时间" in df.columns and "签约额（元）" in df.columns:
            bid_only = df[(df["中标额（元）"].apply(safe_float) > 0) & (df["签约额（元）"].apply(safe_float) == 0)]
            if not bid_only.empty and "客户名称" in bid_only.columns:
                bid_cust_times = bid_only.groupby("客户名称")["中标报量时间"].max()
                for cust, latest in bid_cust_times.items():
                    parsed = self._parse_datetime(latest)
                    if parsed is None:
                        continue
                    months = (now - parsed).days / 30
                    if months > 12:
                        findings.append(
                            {
                                "模型编号": "3.3",
                                "客户名称": cust,
                                "问题分类": "中标未签约僵尸客户",
                                "严重等级": "red",
                                "问题描述": f"{cust} 中标后 {months:.0f} 个月未签约，最近中标时间 {parsed.strftime('%Y-%m')}",
                            }
                        )

        cust_output = None
        if "是否优质客户" in df.columns and "签约额（元）" in df.columns:
            cust_output = df.groupby("客户名称").agg(
                output=("签约额（元）", lambda x: x.apply(safe_float).sum()),
                is_quality=("是否优质客户", "first"),
            )
            for cust, row in cust_output.iterrows():
                is_quality = self._is_yes(row["is_quality"])
                output = safe_float(row["output"])
                if is_quality and output == 0:
                    findings.append(
                        {
                            "模型编号": "3.3",
                            "客户名称": cust,
                            "问题分类": "高评级零产出",
                            "严重等级": "yellow",
                            "问题描述": f"{cust} 被标记为优质客户，但签约合同额为 0",
                        }
                    )
                if (not is_quality) and output > 200_000_000:
                    findings.append(
                        {
                            "模型编号": "3.3",
                            "客户名称": cust,
                            "问题分类": "高产出未评优",
                            "严重等级": "yellow",
                            "问题描述": f"{cust} 签约额 {output / 1e8:.1f} 亿元，但未标记为优质客户",
                        }
                    )

        if "是否高端客户" in df.columns and "是否优质客户" in df.columns:
            cust_tags = df.groupby("客户名称").agg(
                bid_high=("是否高端客户", lambda x: any(self._is_yes(v) for v in x)),
                dmp_quality=("是否优质客户", lambda x: any(self._is_yes(v) for v in x)),
            )
            for cust, row in cust_tags.iterrows():
                if row["bid_high"] and not row["dmp_quality"]:
                    findings.append(
                        {
                            "模型编号": "3.3",
                            "客户名称": cust,
                            "问题分类": "高端/优质标记不一致",
                            "严重等级": "yellow",
                            "问题描述": f"{cust} 在中标侧被标记为高端客户，但在签约侧未标记为优质客户",
                        }
                    )

        has_customer_latest = hasattr(customer_latest, "empty") and not customer_latest.empty
        if has_customer_latest and cust_output is not None:
            cutoff = now - timedelta(days=24 * 30)
            for cust, row in cust_output.iterrows():
                is_quality = self._is_yes(row["is_quality"])
                output = safe_float(row["output"])
                if is_quality and output == 0 and cust in customer_latest:
                    parsed = self._parse_datetime(customer_latest[cust])
                    if parsed is not None and parsed < cutoff:
                        findings.append(
                            {
                                "模型编号": "3.3",
                                "客户名称": cust,
                                "问题分类": "优质僵尸客户",
                                "严重等级": "red",
                                "问题描述": f"{cust} 被标记为优质客户，但超过 24 个月无产出，最近记录 {parsed.strftime('%Y-%m')}",
                            }
                        )

        issues_df = pd.DataFrame(findings)
        if not issues_df.empty and "严重等级" in issues_df.columns:
            issues_df = issues_df.sort_values("严重等级")

        summary = {
            "zombie_customers": int(issues_df["问题分类"].astype(str).str.contains("僵尸客户").sum()) if not issues_df.empty else 0,
            "stagnant_projects": int(issues_df["问题分类"].astype(str).str.contains("已签约未开工").sum()) if not issues_df.empty else 0,
            "rating_mismatch": int(issues_df["问题分类"].astype(str).str.contains("评优|标记").sum()) if not issues_df.empty else 0,
            "total_issues": len(issues_df),
            "data_source": "DMP签约报量 + 中标报量",
        }

        logger.set_summary(**summary)
        self._check_completed()
        return issues_df, summary
