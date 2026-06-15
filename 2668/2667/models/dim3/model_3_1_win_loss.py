"""
Model 3.1: 客户全生命周期监控（v2.9合并版：原3.1中标流失 + 原3.3僵尸评级核查）.

v2.9: 纯DMP数据驱动。附表3/客户台账不可获取。
  - 中标→签约转化率（DMP中标报量 vs 签约报量）
  - 客户活跃度分级（流失预警>12月 → 僵尸>24月）
  - 中标未签约客户（按时间分级严重度）
  - 优质客户占比 + 评级失真 + 标记交叉校验
"""
import pandas as pd
from datetime import datetime, timedelta
from models.base_model import BaseModel
from utils.helpers import safe_float


class Model31WinLoss(BaseModel):
    model_id = "3.1"
    model_name = "客户全生命周期监控（中标转化+流失分级+评级核查）"
    priority = "P2"
    dimension = "客户健康度"

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        df = dmp.copy()
        exp = self.config.get("experience_warnings", {})
        loss_months = exp.get("客户流失_连续未合作月数", 12)
        conv_rate_yellow = exp.get("中标转化率", {}).get("黄色预警", 0.60)

        findings = []
        now = datetime.now()

        # ==================================================================
        # 一、中标→签约转化率（DMP中标报量 vs 签约报量）
        # ==================================================================
        has_bid_data = "中标额（元）" in df.columns

        if has_bid_data and "签约额（元）" in df.columns:
            bid_projects = df[df["中标额（元）"].apply(safe_float) > 0]
            bid_total = len(bid_projects)
            if bid_total > 0:
                signed = len(bid_projects[
                    bid_projects["签约额（元）"].apply(safe_float) > 0
                ])
                conv_rate = signed / bid_total
                if conv_rate < conv_rate_yellow:
                    findings.append({
                        "模型编号": "3.1",
                        "问题分类": "中标转化率偏低",
                        "严重等级": "red" if conv_rate < 0.40 else "yellow",
                        "问题描述": (
                            f"中标→签约转化率{conv_rate:.1%}（{signed}/{bid_total}）"
                            f" < {conv_rate_yellow:.0%}"
                        ),
                    })

            # 按申报单位统计
            if "申报单位" in df.columns:
                unit_stats = bid_projects.groupby("申报单位").agg(
                    中标数=("中标额（元）", lambda x: (x.apply(safe_float) > 0).sum()),
                    签约数=("签约额（元）", lambda x: (x.apply(safe_float) > 0).sum()),
                )
                for unit, row in unit_stats.iterrows():
                    if row["中标数"] >= 3:
                        u_conv = row["签约数"] / row["中标数"]
                        if u_conv < conv_rate_yellow:
                            findings.append({
                                "模型编号": "3.1",
                                "申报单位": str(unit),
                                "问题分类": "中标转化率偏低",
                                "严重等级": "yellow",
                                "问题描述": (
                                    f"{unit}中标→签约转化率{u_conv:.0%}"
                                    f"（{int(row['签约数'])}/{int(row['中标数'])}）"
                                ),
                            })

        # ==================================================================
        # 二、客户活跃度分级（统一检测：>12月流失预警 → >24月僵尸）
        # ==================================================================
        customer_latest = {}
        if "客户名称" in df.columns and "中标时间" in df.columns:
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
                        "模型编号": "3.1",
                        "客户名称": cust,
                        "问题分类": "僵尸客户",
                        "严重等级": "red",
                        "问题描述": (
                            f"{cust}连续{months:.0f}个月无签约产出"
                            f"（最晚{latest.strftime('%Y-%m')}），建议清理或降级"
                        ),
                    })
                elif months > loss_months:
                    findings.append({
                        "模型编号": "3.1",
                        "客户名称": cust,
                        "问题分类": "客户流失预警",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"{cust}最近合作距今{months:.0f}月"
                            f" > {loss_months}月，存在流失风险"
                        ),
                    })

        # ==================================================================
        # 三、中标未签约客户（统一入口，按时间分级严重度）
        # ==================================================================
        if has_bid_data and "签约额（元）" in df.columns and "客户名称" in df.columns:
            bid_only = df[
                (df["中标额（元）"].apply(safe_float) > 0)
                & (df["签约额（元）"].apply(safe_float) == 0)
            ]
            if len(bid_only) > 0:
                # 如果中标报量时间可用，按时间分级
                has_bid_report_time = "中标报量时间" in df.columns
                for cust in bid_only["客户名称"].dropna().unique():
                    cust_rows = bid_only[bid_only["客户名称"] == cust]
                    months_since = None
                    if has_bid_report_time:
                        times = cust_rows["中标报量时间"].dropna()
                        if len(times) > 0:
                            latest_bid = times.max()
                            if isinstance(latest_bid, str):
                                try:
                                    latest_bid = datetime.strptime(latest_bid[:10], "%Y-%m-%d")
                                except:
                                    latest_bid = None
                            if isinstance(latest_bid, datetime):
                                months_since = (now - latest_bid).days / 30

                    if months_since is not None and months_since > 12:
                        severity = "red"
                        desc = f"客户「{cust}」中标{months_since:.0f}个月未签约，已实质流失"
                    else:
                        severity = "yellow"
                        desc = f"客户「{cust}」存在中标但无签约记录，可能已流失"

                    findings.append({
                        "模型编号": "3.1",
                        "客户名称": str(cust),
                        "问题分类": "中标未签约客户",
                        "严重等级": severity,
                        "问题描述": desc,
                    })

        # ==================================================================
        # 四、优质客户合同额占比
        # ==================================================================
        if "是否优质客户" in df.columns and "签约额（元）" in df.columns:
            total_amt = df["签约额（元）"].apply(safe_float).sum()
            quality_amt = (
                df[df["是否优质客户"].astype(str).str.strip() == "是"]["签约额（元）"]
                .apply(safe_float).sum()
            )
            quality_pct = quality_amt / total_amt if total_amt > 0 else 0
            if quality_pct < 0.35:
                findings.append({
                    "模型编号": "3.1",
                    "问题分类": "客户结构降级",
                    "严重等级": "yellow",
                    "问题描述": f"优质客户合同额占比{quality_pct:.1%} < 35%",
                })

        # ==================================================================
        # 五、评级-产出失真（高评级零产出 + 高产出未评优）
        # ==================================================================
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
                        "模型编号": "3.1",
                        "客户名称": cust,
                        "问题分类": "高评级零产出",
                        "严重等级": "yellow",
                        "问题描述": f"{cust}标记为优质客户但签约合同额为零，评级虚高",
                    })
                if not is_quality and output > 200_000_000:
                    findings.append({
                        "模型编号": "3.1",
                        "客户名称": cust,
                        "问题分类": "高产出未评优",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"{cust}签约额{output/1e8:.1f}亿但未标记为优质客户"
                        ),
                    })

        # ==================================================================
        # 六、高端/优质标记交叉校验（中标报量 vs 签约报量）
        # ==================================================================
        if "是否高端客户" in df.columns and "是否优质客户" in df.columns:
            cust_tags = df.groupby("客户名称").agg(
                bid_high=("是否高端客户", lambda x: (x.astype(str).str.strip() == "是").any()),
                dmp_quality=("是否优质客户", lambda x: (x.astype(str).str.strip() == "是").any()),
            )
            for cust, row in cust_tags.iterrows():
                if row["bid_high"] and not row["dmp_quality"]:
                    findings.append({
                        "模型编号": "3.1",
                        "客户名称": cust,
                        "问题分类": "高端/优质标记不一致",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"客户「{cust}」中标报量标记为高端客户，"
                            f"但签约报量未标记为优质客户，存在标记降级"
                        ),
                    })

        # ==================================================================
        # 七、优质僵尸客户（优质但>24月无产出）
        # ==================================================================
        if len(customer_latest) > 0 and "是否优质客户" in df.columns:
            cutoff = now - timedelta(days=24 * 30)
            quality_mask = df["是否优质客户"].astype(str).str.strip() == "是"
            quality_custs = df.loc[quality_mask, "客户名称"].unique()
            for cust in quality_custs:
                if cust in customer_latest:
                    lt = customer_latest[cust]
                    if isinstance(lt, datetime) and lt < cutoff:
                        # Check if actually zero recent output
                        cust_rows = df[df["客户名称"] == cust]
                        total_out = cust_rows["签约额（元）"].apply(safe_float).sum()
                        if total_out == 0:
                            findings.append({
                                "模型编号": "3.1",
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
            "win_rate_issues": (
                len(issues_df[issues_df["问题分类"].str.contains("转化率")])
                if len(issues_df) > 0 else 0
            ),
            "churn_warnings": (
                len(issues_df[issues_df["问题分类"].str.contains("流失|未签约|僵尸")])
                if len(issues_df) > 0 else 0
            ),
            "rating_issues": (
                len(issues_df[issues_df["问题分类"].str.contains("评级|标记|优评")])
                if len(issues_df) > 0 else 0
            ),
            "total_issues": len(issues_df),
            "data_source": "DMP中标报量→签约报量（合并3.1+3.3）",
        }

        logger.set_summary(**summary)
        self._check_completed()
        return issues_df, summary
