"""
Model 1.3: Strategic Customer Market Layout (高端客户与战略客户市场布局).

v2.9: 以内嵌制度名单（手册0520附件5+附件10，2026年）为唯一战略客户数据源。
附表6（线下手工填列）不可获取，已移除所有附表6相关逻辑。
后续年份补充：在 _STRATEGIC_CUSTOMERS 字典中追加对应年份数据即可。
"""
import pandas as pd
from models.base_model import BaseModel
from utils.helpers import safe_float


# ====================================================================
# 内嵌制度参考数据 — 来源于手册0520（2026年5月修订版）
# ====================================================================
_STRATEGIC_CUSTOMERS = {
    2026: {
        # 附件5 — 大客户名单（2026年）
        "核心客户": [
            "华为投资控股有限公司", "中国华润有限公司", "中海企业发展集团有限公司",
            "厦门翔业集团有限公司", "广州智都投资控股集团有限公司",
            "贵阳云岩城市建设投资集团有限责任公司", "广州越秀集团股份有限公司",
            "京东集团股份有限公司", "山东济莱控股集团有限公司",
            "广州市城市建设投资集团有限公司", "南宁城市建设投资集团有限责任公司",
            "广州机场建设投资集团有限公司", "合肥市滨湖新区建设投资有限公司",
        ],
        "重点客户": [
            "华侨城集团有限公司", "厦门安居控股集团有限公司",
            "中国光大集团股份公司", "无锡市城南建设投资发展有限公司",
            "广州市天河区建设工程项目代建局", "广东欧加控股有限公司",
            "贵州贵安发展集团有限公司", "广州开发区控股集团有限公司",
            "融捷投资控股集团有限公司", "阳江市城市投资集团有限公司",
        ],
        # 附件10 — 客户专属及主辅维护单位认定清单（2026年）
        "维护单位": {
            "华为投资控股有限公司": {"专属": "总承包公司", "主": None, "辅": None},
            "京东集团股份有限公司": {"专属": "建设投资", "主": None, "辅": None},
            "广州机场建设投资集团有限公司": {"专属": "一公司", "主": None, "辅": None},
            "广州市城市建设投资集团有限公司": {"专属": None, "主": "六公司", "辅": "水利能源"},
        },
    },
    # TODO: 后续年份数据补充（从手册0520更新版附件5/10提取）
    # 2025: { "核心客户": [...], "重点客户": [...], "维护单位": {...} },
}


def _get_strategic_set(year: int) -> set:
    """Return the full set of strategic customer names for a given year."""
    yr_data = _STRATEGIC_CUSTOMERS.get(year, {})
    return set(yr_data.get("核心客户", [])) | set(yr_data.get("重点客户", []))


def _get_customer_tier(name: str, year: int) -> str:
    """Return tier label: 局核心客户 / 局重点客户 / 非战略."""
    yr_data = _STRATEGIC_CUSTOMERS.get(year, {})
    if name in yr_data.get("核心客户", []):
        return "局核心客户"
    if name in yr_data.get("重点客户", []):
        return "局重点客户"
    return "非战略"


class Model13StrategicCustomer(BaseModel):
    model_id = "1.3"
    model_name = "高端客户与战略客户市场布局"
    priority = "P2"
    dimension = "战略与布局"

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        df = dmp.copy()
        rules = self.config.get("institutional", {}).get("客户管理", {})
        exp = self.config.get("experience_warnings", {})
        concentration_limit = exp.get("客户集中度_前5大占比上限", 0.60)

        findings = []
        audit_year = 2026
        strategic_set = _get_strategic_set(audit_year)

        # ─── 1. 客户集中度：前5大占比 ───
        if "客户名称" in df.columns and "签约额（元）" in df.columns:
            customer_amt = df.groupby("客户名称")["签约额（元）"].apply(
                lambda x: x.apply(safe_float).sum()
            ).sort_values(ascending=False)
            total = customer_amt.sum()
            if total > 0:
                top5_share = customer_amt.head(5).sum() / total
                if top5_share > concentration_limit:
                    findings.append({
                        "模型编号": "1.3",
                        "问题分类": "客户集中度过高",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"前5大客户合同额占比{top5_share:.1%}"
                            f" > {concentration_limit:.0%}"
                        ),
                        "涉及金额": customer_amt.head(5).sum(),
                    })

        # ─── 2. 战略客户合同额占比（内嵌制度名单） ───
        if "客户名称" in df.columns and "签约额（元）" in df.columns:
            dmp_strategic = df[df["客户名称"].isin(strategic_set)]
            strategic_amt = dmp_strategic["签约额（元）"].apply(safe_float).sum()
            total_amt = df["签约额（元）"].apply(safe_float).sum()

            if total_amt > 0:
                strategic_pct = strategic_amt / total_amt
                target = rules.get("战略客户合同额占比目标", 0.35)
                if strategic_pct < target:
                    findings.append({
                        "模型编号": "1.3",
                        "问题分类": "战略客户合同额占比低",
                        "严重等级": "red" if strategic_pct < 0.20 else "yellow",
                        "问题描述": (
                            f"战略客户合同额占比{strategic_pct:.1%} < 目标{target:.0%}"
                            f"（基于{audit_year}年制度名单，共{len(strategic_set)}家）"
                        ),
                    })

            # 制度名单内战略客户有签约的统计
            strategic_with_contract = dmp_strategic["客户名称"].nunique()
            if strategic_with_contract < len(strategic_set):
                missing = strategic_set - set(dmp_strategic["客户名称"].unique())
                findings.append({
                    "模型编号": "1.3",
                    "问题分类": "制度战略客户无签约",
                    "严重等级": "yellow",
                    "问题描述": (
                        f"{audit_year}年制度战略客户共{len(strategic_set)}家，"
                        f"本批DMP有签约的仅{strategic_with_contract}家。"
                        f"无签约：{'、'.join(sorted(list(missing))[:5])}"
                        f"{'...' if len(missing) > 5 else ''}"
                    ),
                })

        # ─── 3. 战略客户主责维护检查（内嵌附件10） ───
        yr_data = _STRATEGIC_CUSTOMERS.get(audit_year, {})
        maint_map = yr_data.get("维护单位", {})
        for cust_name, assign in maint_map.items():
            has_maintainer = assign.get("专属") or assign.get("主")
            if not has_maintainer:
                findings.append({
                    "模型编号": "1.3",
                    "客户名称": cust_name,
                    "问题分类": "制度战略客户无维护单位",
                    "严重等级": "red",
                    "问题描述": (
                        f"制度战略客户「{cust_name}」在附件10中未指定专属/主维护单位"
                    ),
                })

        # ─── 4. 优质客户合同额占比 ───
        if "是否优质客户" in df.columns and "签约额（元）" in df.columns:
            quality_amt = (
                df[df["是否优质客户"].astype(str).str.strip() == "是"]["签约额（元）"]
                .apply(safe_float).sum()
            )
            total_amt = df["签约额（元）"].apply(safe_float).sum()
            if total_amt > 0:
                quality_pct = quality_amt / total_amt
                target = rules.get("战略客户合同额占比目标", 0.35)
                if quality_pct < target:
                    findings.append({
                        "模型编号": "1.3",
                        "问题分类": "优质客户合同额占比低",
                        "严重等级": "red" if quality_pct < 0.20 else "yellow",
                        "问题描述": f"优质客户合同额占比{quality_pct:.1%} < 目标{target:.0%}",
                    })

        # ─── 5. DMP覆盖统计 ───
        if "客户名称" in df.columns:
            dmp_cust_set = set(df["客户名称"].unique())
            matched_core = [c for c in yr_data.get("核心客户", []) if c in dmp_cust_set]
            matched_key = [c for c in yr_data.get("重点客户", []) if c in dmp_cust_set]
            logger.log_check(
                f"DMP覆盖{audit_year}年制度战略客户", True,
                {"核心": f"{len(matched_core)}/{len(yr_data.get('核心客户',[]))}",
                 "重点": f"{len(matched_key)}/{len(yr_data.get('重点客户',[]))}"}
            )

        issues_df = pd.DataFrame(findings)

        summary = {
            "customer_count": (
                df["客户名称"].nunique() if "客户名称" in df.columns else 0
            ),
            "concentration_warning": (
                len(issues_df[issues_df["问题分类"].str.contains("集中")])
                if len(issues_df) > 0 else 0
            ),
            "total_issues": len(issues_df),
            "data_source": f"制度内嵌(手册0520附件5/10, {audit_year}年)",
        }

        logger.set_summary(**summary)
        self._check_completed()
        return issues_df, summary
