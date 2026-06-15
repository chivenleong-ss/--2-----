"""
Model 3.2: New Customer Quality Assessment (新客户质量评估与客户结构优化).

v2.9: 纯DMP数据驱动。附表6不可获取。
  战略补录建议使用内嵌制度名单（复用模型1.3的_STRATEGIC_CUSTOMERS）。
  其余全部检查基于DMP字段（客户性质/地产依赖/优质占比/粘性/行业分散度）。
"""
import pandas as pd
from datetime import datetime
from models.base_model import BaseModel
from utils.helpers import safe_float


class Model32NewCustomer(BaseModel):
    model_id = "3.2"
    model_name = "新客户质量评估与客户结构优化"
    priority = "P3"
    dimension = "客户健康度"

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        df = dmp.copy()

        findings = []

        # ─── 1. 新客户质量：政府/国企占比 ───
        new_customers = pd.DataFrame()
        if "是否首次合作" in df.columns:
            new_customers = df[df["是否首次合作"].astype(str).str.strip() == "是"]

        if len(new_customers) > 0:
            # 1a. 政府/国企新客户占比
            if "客户性质" in df.columns:
                gov_soe = new_customers[
                    new_customers["客户性质"].astype(str).str.contains(
                        "政府|国企|事业", na=False, regex=True
                    )
                ]
                gov_share = len(gov_soe) / len(new_customers)
                if gov_share < 0.50:
                    findings.append({
                        "模型编号": "3.2",
                        "问题分类": "新客户质量偏低",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"新客户中政府/国企仅占{gov_share:.0%}"
                            f"（{len(gov_soe)}/{len(new_customers)}）"
                        ),
                        "涉及金额": (
                            new_customers["签约额（元）"].apply(safe_float).sum()
                            if "签约额（元）" in new_customers.columns else 0
                        ),
                    })

            # 1b. 地产类新客户占比
            if "是否地产类项目" in df.columns:
                re_new = new_customers[
                    new_customers["是否地产类项目"].astype(str).str.strip() == "是"
                ]
                re_share = len(re_new) / len(new_customers)
                if re_share > 0.50:
                    findings.append({
                        "模型编号": "3.2",
                        "问题分类": "新客户地产依赖",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"新客户中地产类占{re_share:.0%}"
                            f"（{len(re_new)}/{len(new_customers)}） > 50%"
                        ),
                    })

            # 1c. 优质新客户占比
            if "是否优质客户" in df.columns:
                quality_new = new_customers[
                    new_customers["是否优质客户"].astype(str).str.strip() == "是"
                ]
                q_share = len(quality_new) / len(new_customers)
                if q_share < 0.30:
                    findings.append({
                        "模型编号": "3.2",
                        "问题分类": "优质新客户不足",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"优质新客户仅占{q_share:.0%}"
                            f"（{len(quality_new)}/{len(new_customers)}）"
                        ),
                    })

        # ─── 2. 新客户二次合作粘性 ───
        if (
            "客户名称" in df.columns
            and "中标时间" in df.columns
            and "是否首次合作" in df.columns
        ):
            new_cust_names = (
                df[df["是否首次合作"].astype(str).str.strip() == "是"]["客户名称"]
                .dropna().unique()
            )
            for cust in new_cust_names:
                cust_projects = df[
                    df["客户名称"].astype(str).str.strip() == str(cust).strip()
                ]
                if len(cust_projects) >= 2:
                    times = []
                    for _, cp in cust_projects.iterrows():
                        t = cp["中标时间"]
                        if pd.notna(t):
                            if isinstance(t, datetime):
                                times.append(t)
                            elif isinstance(t, str):
                                try:
                                    times.append(datetime.strptime(t[:10], "%Y-%m-%d"))
                                except:
                                    pass
                    if len(times) >= 2:
                        times.sort()
                        months_between = (times[1] - times[0]).days / 30
                        if months_between > 12:
                            findings.append({
                                "模型编号": "3.2",
                                "客户名称": str(cust).strip(),
                                "问题分类": "新客户二次合作间隔过长",
                                "严重等级": "yellow",
                                "问题描述": (
                                    f"新客户「{cust}」首次合作{times[0].strftime('%Y-%m-%d')}，"
                                    f"二次合作间隔{months_between:.0f}月 > 12月，客户粘性不足"
                                ),
                            })

        # ─── 3. 新客户战略补录建议（内嵌制度名单） ───
        try:
            from models.dim1.model_1_3_strategic_customer import _get_strategic_set
            strategic_names = _get_strategic_set(2026)
        except ImportError:
            strategic_names = set()
            logger.log_warning("无法加载内嵌制度名单，跳过高签约额新客户战略补录检查")

        if strategic_names and "客户名称" in df.columns and "是否首次合作" in df.columns:
            new_custs = df[df["是否首次合作"].astype(str).str.strip() == "是"]
            for _, nc in new_custs.iterrows():
                cname = str(nc.get("客户名称", "")).strip()
                output = safe_float(nc.get("签约额（元）", 0))
                if cname and output > 500_000_000 and cname not in strategic_names:
                    findings.append({
                        "模型编号": "3.2",
                        "客户名称": cname,
                        "问题分类": "新客户战略补录建议",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"新客户「{cname}」签约额{output/1e8:.1f}亿"
                            f"但未在制度战略名单中，建议评估是否补录"
                        ),
                    })

        # ─── 4. 客户行业分散度（HHI） ───
        if "客户性质" in df.columns and "签约额（元）" in df.columns:
            type_amt = df.groupby("客户性质")["签约额（元）"].apply(
                lambda x: x.apply(safe_float).sum()
            )
            total = type_amt.sum()
            if total > 0:
                hhi = sum((v / total) ** 2 for v in type_amt)
                if hhi > 0.50:
                    top_type = type_amt.idxmax()
                    top_pct = type_amt.max() / total
                    findings.append({
                        "模型编号": "3.2",
                        "问题分类": "客户行业集中度过高",
                        "严重等级": "yellow" if hhi < 0.70 else "red",
                        "问题描述": (
                            f"客户行业HHI={hhi:.2f}，{top_type}占比{top_pct:.0%}"
                        ),
                    })

        issues_df = pd.DataFrame(findings)

        summary = {
            "new_customer_count": len(new_customers),
            "quality_issues": len(issues_df),
            "data_source": "DMP签约报量 + 内嵌制度名单(附件5,2026)",
        }

        logger.set_summary(**summary)
        self._check_completed()
        return issues_df, summary
