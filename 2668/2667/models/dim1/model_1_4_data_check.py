"""
Model 1.4: Marketing Statistics Cross-Validation (营销统计数据多维度交叉验真).
Detects process reversals, abnormal profit rate patterns, data fabrication,
and bid-to-contract timeline inconsistencies.
"""
import pandas as pd
import numpy as np
from datetime import datetime
from models.base_model import BaseModel
from utils.helpers import safe_float


class Model14DataCheck(BaseModel):
    model_id = "1.4"
    model_name = "营销统计数据多维度交叉验真"
    priority = "P3"
    dimension = "战略与布局"

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        df = dmp.copy()

        findings = []

        # Pre-check field availability
        has_bid_time = "中标时间" in df.columns
        has_sign_time = "签约时间" in df.columns
        has_submit_time = "交标时间" in df.columns          # replaces OA bid_review_pass_time
        has_doc_time = "领取招标文件时间" in df.columns     # tender doc pickup time
        has_report_time = "签约报量时间" in df.columns
        has_bid_report_time = "中标报量时间" in df.columns

        # ==================================================================
        # 1. DMP Timeline integrity checks (replaces OA workflow timestamps)
        #    领取招标文件时间 / 交标时间 / 中标时间 / 签约时间 / 签约报量时间
        # ==================================================================

        # 1a. 领取招标文件 → 交标: doc pickup must be before submission
        if has_doc_time and has_submit_time:
            for idx, row in df.iterrows():
                doc_time = row.get("领取招标文件时间")
                submit_time = row.get("交标时间")
                if pd.notna(doc_time) and pd.notna(submit_time):
                    dt = doc_time if hasattr(doc_time, "year") else pd.Timestamp(doc_time)
                    st = submit_time if hasattr(submit_time, "year") else pd.Timestamp(submit_time)
                    if dt > st:
                        findings.append({
                            "模型编号": "1.4",
                            "项目编码": str(row.get("项目编码", "")),
                            "项目名称": str(row.get("项目名称", "")),
                            "问题分类": "招文领取与交标时间倒置",
                            "严重等级": "red",
                            "问题描述": f"领取招标文件({dt.strftime('%Y-%m-%d')})晚于交标时间({st.strftime('%Y-%m-%d')})，时序倒置，疑似事后补录",
                        })

        # 1b. 交标 → 中标: submission must be before winning
        if has_submit_time and has_bid_time:
            for idx, row in df.iterrows():
                submit_time = row.get("交标时间")
                bid_time = row.get("中标时间")
                if pd.notna(submit_time) and pd.notna(bid_time):
                    st = submit_time if hasattr(submit_time, "year") else pd.Timestamp(submit_time)
                    bt = bid_time if hasattr(bid_time, "year") else pd.Timestamp(bid_time)
                    if st > bt:
                        findings.append({
                            "模型编号": "1.4",
                            "项目编码": str(row.get("项目编码", "")),
                            "项目名称": str(row.get("项目名称", "")),
                            "问题分类": "交标与中标时间倒置",
                            "严重等级": "red",
                            "问题描述": f"交标时间({st.strftime('%Y-%m-%d')})晚于中标时间({bt.strftime('%Y-%m-%d')})，时序倒置，疑似事后补录",
                        })

        # 1c. 中标 → 签约: winning must be before signing
        if has_bid_time and has_sign_time:
            for idx, row in df.iterrows():
                bid_time = row.get("中标时间")
                sign_time = row.get("签约时间")
                if pd.notna(bid_time) and pd.notna(sign_time):
                    bt = bid_time if hasattr(bid_time, "year") else pd.Timestamp(bid_time)
                    st = sign_time if hasattr(sign_time, "year") else pd.Timestamp(sign_time)
                    if bt > st:
                        findings.append({
                            "模型编号": "1.4",
                            "项目编码": str(row.get("项目编码", "")),
                            "项目名称": str(row.get("项目名称", "")),
                            "问题分类": "中标与签约时间倒置",
                            "严重等级": "yellow",
                            "问题描述": f"中标时间({bt.strftime('%Y-%m-%d')})晚于签约时间({st.strftime('%Y-%m-%d')})，时序异常",
                        })

        # 1d. 签约 → 签约报量: reporting must be on/after signing
        if has_sign_time and has_report_time:
            for idx, row in df.iterrows():
                sign_time = row.get("签约时间")
                report_time = row.get("签约报量时间")
                if pd.notna(sign_time) and pd.notna(report_time):
                    st = sign_time if hasattr(sign_time, "year") else pd.Timestamp(sign_time)
                    rt = report_time if hasattr(report_time, "year") else pd.Timestamp(report_time)
                    # Allow same-day reporting
                    if st > rt:
                        findings.append({
                            "模型编号": "1.4",
                            "项目编码": str(row.get("项目编码", "")),
                            "项目名称": str(row.get("项目名称", "")),
                            "问题分类": "签约与签约报量时间倒置",
                            "严重等级": "yellow",
                            "问题描述": f"签约时间({st.strftime('%Y-%m-%d')})晚于签约报量时间({rt.strftime('%Y-%m-%d')})，签约报量不应早于签约",
                        })

        # ==================================================================
        # 2. Bid-to-contract cross-validation (中标报量 supplement)
        # ==================================================================
        has_bid_amount = "中标额（元）" in df.columns
        has_expected_sign = "预计签约时间" in df.columns

        if has_bid_amount or has_expected_sign:
            for idx, row in df.iterrows():
                proj_code = str(row.get("项目编码", ""))
                proj_name = str(row.get("项目名称", ""))
                bid_time = row.get("中标时间") if has_bid_time else None
                sign_time = row.get("签约时间") if has_sign_time else None
                bid_amount = safe_float(row.get("中标额（元）", 0))
                contract_amount = safe_float(row.get("签约额（元）", 0))
                expected_sign = row.get("预计签约时间") if has_expected_sign else None

                # 1a. 中标额 vs 签约额 偏离 > 5% — 标后让利检测
                if bid_amount > 0 and contract_amount > 0:
                    deviation = abs(bid_amount - contract_amount) / bid_amount
                    if deviation > 0.05:
                        direction = "降低" if contract_amount < bid_amount else "增加"
                        findings.append({
                            "模型编号": "1.4",
                            "项目编码": proj_code,
                            "项目名称": proj_name,
                            "问题分类": "中标签约金额偏离",
                            "严重等级": "yellow" if deviation < 0.10 else "red",
                            "问题描述": f"中标额{bid_amount/1e4:.0f}万→签约额{contract_amount/1e4:.0f}万，{direction}{deviation:.1%}{'，疑似标后让利' if contract_amount < bid_amount else ''}",
                        })

                # 1b. 预计签约逾期 — 预计签约日已过但未实际签约
                if pd.notna(expected_sign) and (sign_time is None or pd.isna(sign_time)):
                    if hasattr(expected_sign, "year"):
                        es = expected_sign
                    else:
                        es = pd.Timestamp(expected_sign)
                    days_overdue = (datetime.now() - es).days
                    if days_overdue > 30:
                        findings.append({
                            "模型编号": "1.4",
                            "项目编码": proj_code,
                            "项目名称": proj_name,
                            "问题分类": "预计签约逾期",
                            "严重等级": "yellow" if days_overdue <= 90 else "red",
                            "问题描述": f"预计签约{es.strftime('%Y-%m-%d')}，已逾期{days_overdue}天未签约",
                        })

                # 1c. 中标后长期未签约 (> 180天) — 中标即僵尸
                if pd.notna(bid_time) and (sign_time is None or pd.isna(sign_time)):
                    if hasattr(bid_time, "year"):
                        bt = bid_time
                    else:
                        bt = pd.Timestamp(bid_time)
                    days_since_bid = (datetime.now() - bt).days
                    if days_since_bid > 180:
                        findings.append({
                            "模型编号": "1.4",
                            "项目编码": proj_code,
                            "项目名称": proj_name,
                            "问题分类": "中标后长期未签约",
                            "严重等级": "red",
                            "问题描述": f"中标{bt.strftime('%Y-%m-%d')}后{days_since_bid}天仍未签约",
                        })

        # ==================================================================
        # 2. Direct-award bypass detection
        # ==================================================================
        tender_col = "招标方式"
        if tender_col in df.columns:
            direct_award = df[df[tender_col].astype(str).str.contains("直接发包", na=False)]
            if len(direct_award) > 0:
                for _, row in direct_award.iterrows():
                    findings.append({
                        "模型编号": "1.4",
                        "项目编码": str(row.get("项目编码", "")),
                        "项目名称": str(row.get("项目名称", "")),
                        "问题分类": "直接发包规避评审",
                        "严重等级": "yellow",
                        "问题描述": "直接发包项目，需核查招文评审是否合规",
                    })

        # ==================================================================
        # 3. Abnormal profit rate pattern (规律性数值)
        # ==================================================================
        a_vals = df["一次性经营效益率（%）（A值）"].apply(safe_float)
        a_vals = a_vals[a_vals > 0]
        if len(a_vals) >= 5:
            if "申报单位" in df.columns:
                has_time_col = "签约报量时间" in df.columns
                if has_time_col:
                    for (unit, year), group in df.groupby(["申报单位", df["签约报量时间"].astype(str).str[:4]]):
                        vals = group["一次性经营效益率（%）（A值）"].apply(safe_float)
                        vals = vals[vals > 0]
                        if len(vals) >= 5:
                            std = vals.std()
                            if std < 0.005:
                                findings.append({
                                    "模型编号": "1.4",
                                    "申报单位": unit,
                                    "问题分类": "利润率规律性异常",
                                    "严重等级": "red",
                                    "问题描述": f"{unit} {len(vals)}个项目A值标准差仅{std:.3%}（<0.5%），疑似人为编造",
                                })

        # ==================================================================
        # 4. Invited tender ratio check
        # ==================================================================
        if tender_col in df.columns and len(df) > 0:
            for unit, group in df.groupby("申报单位") if "申报单位" in df.columns else [("全局", df)]:
                total = len(group)
                invited = len(group[group[tender_col].astype(str).str.contains("邀请", na=False)])
                if total > 5 and invited / total > 0.70:
                    findings.append({
                        "模型编号": "1.4",
                        "申报单位": unit,
                        "问题分类": "邀请招标比例过高",
                        "严重等级": "yellow",
                        "问题描述": f"{unit}邀请招标{invited}/{total}={invited/total:.0%} > 70%",
                    })

        # ==================================================================
        # 5. 凑量嫌疑：同一客户90天内集中签约多项目
        # ==================================================================
        has_customer = "客户名称" in df.columns
        has_sign_time = "签约时间" in df.columns
        if has_customer and has_sign_time:
            df_signed = df[df["签约时间"].notna()].copy()
            if len(df_signed) >= 2:
                signed_ts = []
                for _, row in df_signed.iterrows():
                    st = row["签约时间"]
                    if hasattr(st, "year"):
                        signed_ts.append(st)
                    else:
                        signed_ts.append(pd.Timestamp(st))
                df_signed = df_signed.copy()
                df_signed["_sign_ts"] = signed_ts
                df_signed = df_signed.sort_values("_sign_ts")
                flagged_custs = set()
                for i_idx, i_row in df_signed.iterrows():
                    cust = str(i_row.get("客户名称", "")).strip()
                    if not cust or cust in flagged_custs:
                        continue
                    ts_i = i_row["_sign_ts"]
                    nearby = df_signed[
                        (df_signed["_sign_ts"] - ts_i).dt.days.abs().between(1, 90)
                    ]
                    same_cust = nearby[nearby["客户名称"].astype(str).str.strip() == cust]
                    if len(same_cust) >= 2:
                        flagged_custs.add(cust)
                        pids = "、".join(same_cust["项目编码"].astype(str).head(5))
                        findings.append({
                            "模型编号": "1.4",
                            "项目编码": str(i_row.get("项目编码", "")),
                            "项目名称": str(i_row.get("项目名称", "")),
                            "问题分类": "凑量嫌疑(同一客户集中签约)",
                            "严重等级": "yellow",
                            "问题描述": f"客户「{cust}」90天内集中签约{len(same_cust)+1}个项目: {pids}，需核查是否存在拆标凑量",
                        })

        issues_df = pd.DataFrame(findings)
        if len(issues_df) > 0:
            issues_df = issues_df.sort_values("严重等级")

        # Count by category
        def _count(cat):
            return len(issues_df[issues_df["问题分类"].str.contains(cat, na=False)]) if len(issues_df) > 0 else 0

        summary = {
            "total_checked": len(df),
            "timeline_reversal_doc_submit": _count("招文领取与交标时间倒置"),
            "timeline_reversal_submit_bid": _count("交标与中标时间倒置"),
            "timeline_reversal_bid_sign": _count("中标与签约时间倒置"),
            "timeline_reversal_sign_report": _count("签约与签约报量时间倒置"),
            "amount_deviation": _count("中标签约金额偏离"),
            "signing_overdue": _count("预计签约逾期"),
            "bid_no_sign": _count("中标后长期未签约"),
            "direct_award_flags": _count("直接发包"),
            "abnormal_patterns": _count("规律性"),
            "batch_signing": _count("凑量嫌疑"),
            "total_issues": len(issues_df),
        }

        logger.set_summary(**summary)
        self._check_completed()

        return issues_df, summary
