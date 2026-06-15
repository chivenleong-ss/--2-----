"""
Model 3.3: Zombie Customer Cleanup & Rating Audit (僵尸客户清理与客户评级内控核查).

v2.9: 纯DMP数据驱动。客户台账（评级得分/调整记录）不可获取。
  - 僵尸客户检测（>24月无签约产出，DMP中标时间）
  - 中标未签约僵尸（中标报量>12月未签约）
  - 评级-产出失真（优质客户零产出 + 高产出未评优）
  - 高端/优质标记交叉校验（中标报量 vs 签约报量）
"""
import pandas as pd
from datetime import datetime, timedelta
from models.base_model import BaseModel
from utils.helpers import safe_float


class Model33Zombie(BaseModel):
    model_id = "3.3"
    model_name = "僵尸客户清理与客户评级内控核查"
    priority = "P3"
    dimension = "客户健康度"

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        df = dmp.copy()

        findings = []

        if "客户名称" not in df.columns:
            logger.log_warning("缺少客户名称字段，无法执行客户健康度检测")
            return pd.DataFrame(), {"total_issues": 0}

        now = datetime.now()

        # ─── 1. 僵尸客户：签约报量最晚中标时间>24月 ───
        customer_latest = {}
        if "中标时间" in df.columns:
            customer_latest = df.groupby("客户名称")["中标时间"].max()
            for cust, latest in customer_latest.items():
                if latest is None or (isinstance(latest, float) and pd.isna(latest)):
                    continue
                if isinstance(latest, str):
                    try:
                        latest = datetime.strptime(latest[:10], "%Y-%m-%d")
                    except:
                        continue
                if isinstance(latest, datetime):
                    months = (now - latest).days / 30
                    if months > 24:
                        findings.append({
                            "模型编号": "3.3",
                            "客户名称": cust,
                            "问题分类": "僵尸客户",
                            "严重等级": "red",
                            "问题描述": (
                                f"{cust}连续{months:.0f}个月无签约产出"
                                f"（最晚{latest.strftime('%Y-%m')}），建议清理或降级"
                            ),
                        })

        # ─── 2. 中标未签约僵尸（中标报量>12月未签约） ───
        if "中标额（元）" in df.columns and "中标报量时间" in df.columns:
            bid_only = df[
                (df["中标额（元）"].apply(safe_float) > 0)
                & (df["签约额（元）"].apply(safe_float) == 0)
            ]
            if len(bid_only) > 0 and "客户名称" in bid_only.columns:
                bid_cust_times = bid_only.groupby("客户名称")["中标报量时间"].max()
                for cust, latest in bid_cust_times.items():
                    if latest is None or (isinstance(latest, float) and pd.isna(latest)):
                        continue
                    if isinstance(latest, str):
                        try:
                            latest = datetime.strptime(latest[:10], "%Y-%m-%d")
                        except:
                            continue
                    if isinstance(latest, datetime):
                        months = (now - latest).days / 30
                        if months > 12:
                            findings.append({
                                "模型编号": "3.3",
                                "客户名称": cust,
                                "问题分类": "中标未签约僵尸",
                                "严重等级": "red",
                                "问题描述": (
                                    f"客户「{cust}」中标{months:.0f}个月未签约"
                                    f"（{latest.strftime('%Y-%m')}），可能已实质流失"
                                ),
                            })

        # ─── 3. 评级-产出失真 ───
        if "是否优质客户" in df.columns and "签约额（元）" in df.columns:
            cust_output = df.groupby("客户名称").agg(
                output=("签约额（元）", lambda x: x.apply(safe_float).sum()),
                is_quality=("是否优质客户", "first"),
            )
            for cust, row in cust_output.iterrows():
                is_quality = str(row["is_quality"]).strip() == "是"
                output = row["output"]
                if is_quality and output == 0:
                    findings.append({
                        "模型编号": "3.3",
                        "客户名称": cust,
                        "问题分类": "高评级零产出",
                        "严重等级": "yellow",
                        "问题描述": f"{cust}标记为优质客户但签约合同额为零，评级虚高",
                    })
                if not is_quality and output > 200_000_000:
                    findings.append({
                        "模型编号": "3.3",
                        "客户名称": cust,
                        "问题分类": "高产出未评优",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"{cust}签约额{output/1e8:.1f}亿但未标记为优质客户"
                        ),
                    })

        # ─── 4. 高端/优质标记交叉校验（中标报量 vs 签约报量） ───
        if "是否高端客户" in df.columns and "是否优质客户" in df.columns:
            cust_tags = df.groupby("客户名称").agg(
                bid_high=("是否高端客户", lambda x: (x.astype(str).str.strip() == "是").any()),
                dmp_quality=("是否优质客户", lambda x: (x.astype(str).str.strip() == "是").any()),
            )
            for cust, row in cust_tags.iterrows():
                if row["bid_high"] and not row["dmp_quality"]:
                    findings.append({
                        "模型编号": "3.3",
                        "客户名称": cust,
                        "问题分类": "高端/优质标记不一致",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"客户「{cust}」中标报量标记为高端客户，"
                            f"但签约报量未标记为优质客户，存在标记降级"
                        ),
                    })

        # ─── 5. 优质僵尸客户（标记优质但>24月无产出） ───
        if customer_latest and "是否优质客户" in df.columns:
            cutoff = now - timedelta(days=24 * 30)
            for cust, row in cust_output.iterrows():
                is_quality = str(row["is_quality"]).strip() == "是"
                output = row["output"]
                if is_quality and output == 0 and cust in customer_latest:
                    lt = customer_latest[cust]
                    if isinstance(lt, datetime) and lt < cutoff:
                        findings.append({
                            "模型编号": "3.3",
                            "客户名称": cust,
                            "问题分类": "优质僵尸客户",
                            "严重等级": "red",
                            "问题描述": (
                                f"{cust}标记为优质客户但>24月无产出"
                                f"（最近{lt.strftime('%Y-%m')}），优评资格存疑"
                            ),
                        })

        issues_df = pd.DataFrame(findings)
        if len(issues_df) > 0:
            issues_df = issues_df.sort_values("严重等级")

        summary = {
            "zombie_customers": (
                len(issues_df[issues_df["问题分类"].str.contains("僵尸")])
                if len(issues_df) > 0 else 0
            ),
            "rating_mismatch": (
                len(issues_df[issues_df["问题分类"].str.contains("评级|标记|优评")])
                if len(issues_df) > 0 else 0
            ),
            "total_issues": len(issues_df),
            "data_source": "DMP签约报量 + 中标报量",
        }

        logger.set_summary(**summary)
        self._check_completed()
        return issues_df, summary
