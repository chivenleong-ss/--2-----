"""
Model 2.2: 盈利底线检测（v2.9拆分版：原停工退场+在施验证已拆至2.5）.
Detects:
  1. 备案合同价误用（正常招议标项目使用备案合同价而非执行合同价）
  2. A值低于制度底线（承接即亏损）
  3. A值 vs 实际利润率偏差（仅2020版旧制度）

Supports dual-regime year-scoping:
  - Old regime (pre-2026): 2020版 profit floors + A值偏差
  - New regime (2026+): 手册0520 unified profit floors
"""
import pandas as pd
from models.base_model import BaseModel
from utils.helpers import safe_float, safe_percent_rate, parse_date


class Model22Profit(BaseModel):
    model_id = "2.2"
    model_name = "盈利底线检测"
    priority = "P0"
    dimension = "合同质量与风险"

    @staticmethod
    def _extract_signing_year(row) -> int:
        for col in ["签约时间", "中标时间", "签约报量时间"]:
            val = row.get(col)
            if pd.notna(val):
                try:
                    if hasattr(val, "year"):
                        return val.year
                    dt = parse_date(str(val))
                    if dt:
                        return dt.year
                except Exception:
                    pass
        year_str = str(row.get("合同签订年度", ""))
        import re
        m = re.search(r"(\d{4})", year_str)
        if m:
            return int(m.group(1))
        return 2026

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        config = self.config
        profit_config = config.get("institutional", {}).get("盈利底线", {})
        cost_config = config.get("institutional", {}).get("成本管理", {})
        regime_year = config.get("institutional", {}).get("制度切换年份", 2026)

        profit_new = profit_config.get("手册0520", profit_config)
        profit_old = profit_config.get("历史_2020版", profit_config)
        cost_old = cost_config.get("历史_2020版", {})
        a_dev_limit = cost_old.get("A值偏差上限", 0.01)

        df = dmp.copy()
        findings = []

        for idx, row in df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            is_re = str(row.get("是否地产类项目", "")).strip() == "是"
            reg_tier = str(row.get("监管档次", "")).strip()
            a_value = safe_float(row.get("一次性经营效益率（%）（A值）", 0))
            c_value = safe_percent_rate(row.get("目标效益率（%）（C值）", 0))
            contract_amt = safe_float(row.get("签约额（元）", 0))

            signing_year = self._extract_signing_year(row)
            is_new = signing_year >= regime_year if signing_year else True
            profit_rules = profit_new if is_new else profit_old

            business_type = str(row.get("业务类型", ""))
            is_infra = "基础设施" in business_type
            monthly_pct = safe_float(row.get("月进度付款比例（%）", 0))
            payment_bad = 0 < monthly_pct < 0.70

            # --- Determine profit floor by regime ---
            if is_re:
                if is_new:
                    floor = profit_rules.get("承接效益率_严禁投标_上限", 0.0)
                else:
                    floor = profit_rules.get("地产类_橙档_净利润率下限", 0.05) if reg_tier == "橙" else profit_rules.get("地产类_黄绿档_净利润率下限", 0.04)
            elif is_infra:
                if is_new:
                    floor = profit_rules.get("承接效益率_严禁投标_上限", 0.0)
                else:
                    floor = profit_rules.get("非地产_付款不达标_基础设施净利润下限", 0.10) if payment_bad else profit_rules.get("非地产_常规_基础设施净利润下限", 0.06)
            else:
                if is_new:
                    advance_amt = safe_float(row.get("预估垫资金额（万元）", 0))
                    floor = profit_rules.get("垫资超5000万_非地产_房建下限", 0.04) if advance_amt >= 5000 else profit_rules.get("承接效益率_严禁投标_上限", 0.0)
                else:
                    floor = profit_rules.get("非地产_付款不达标_房建净利润下限", 0.08) if payment_bad else profit_rules.get("非地产_常规_房建净利润下限", 0.04)

            issues = []

            # 1. 备案合同价检测
            contract_nature = str(row.get("合同性质", "")).strip()
            tender_method = str(row.get("招标方式", "")).strip()
            is_formal_bid = any(kw in tender_method for kw in ["公开", "邀请"])
            is_registration = "备案" in contract_nature and "一致" not in contract_nature
            if is_registration and is_formal_bid and contract_amt > 0:
                issues.append({
                    "type": "备案合同合同额存疑",
                    "desc": f"招标方式「{tender_method}」（正常招议标），合同性质「{contract_nature}」，合同额{contract_amt/1e8:.1f}亿为备案合同价。按制度正常招议标应使用执行合同总价，需核实是否存在执行合同",
                    "severity": "red"
                })
            elif is_registration:
                issues.append({
                    "type": "备案合同合同额",
                    "desc": f"合同性质「{contract_nature}」，合同额{contract_amt/1e8:.1f}亿为备案合同价。按制度无执行合同时可暂按备案价计收入，需确认是否已有执行合同",
                    "severity": "yellow"
                })

            # 2. A值底线
            if a_value > 0 and a_value < floor:
                regime_label = "手册0520" if is_new else "2020版"
                issues.append({
                    "type": "承接即亏损",
                    "desc": f"A值{a_value:.1%} < 底线{floor:.0%}（{regime_label}）",
                    "severity": "red"
                })

            # 3. A值偏差（仅旧制度）
            if not is_new and cost_old.get("A值偏差检查启用", True):
                actual_profit_rate = safe_float(row.get("最近一期成本分析利润率", 0))
                if actual_profit_rate > 0 and a_value > 0:
                    deviation = abs(a_value - actual_profit_rate)
                    if deviation > a_dev_limit:
                        issues.append({
                            "type": "效益偏差",
                            "desc": f"A值{a_value:.1%} vs 实际{actual_profit_rate:.1%}，偏差{deviation:.1%} > {a_dev_limit:.0%}（2020版）",
                            "severity": "red" if deviation > 0.05 else "yellow"
                        })

            for issue in issues:
                findings.append({
                    "模型编号": "2.2",
                    "项目编码": proj_code,
                    "项目名称": proj_name,
                    "问题分类": issue["type"],
                    "严重等级": issue["severity"],
                    "问题描述": issue["desc"],
                    "A值(%)": a_value,
                    "签约额（元）": contract_amt,
                })

        issues_df = pd.DataFrame(findings)
        if len(issues_df) > 0:
            issues_df = issues_df.sort_values("严重等级")

        summary = {
            "total_checked": len(df),
            "承接即亏损": len(issues_df[issues_df["问题分类"] == "承接即亏损"]) if len(issues_df) > 0 else 0,
            "效益偏差": len(issues_df[issues_df["问题分类"] == "效益偏差"]) if len(issues_df) > 0 else 0,
            "备案合同价存疑": len(issues_df[issues_df["问题分类"].str.contains("备案")]) if len(issues_df) > 0 else 0,
            "total_issues": len(issues_df),
        }

        logger.set_summary(**summary)
        logger.log_check("A值底线校验", True, summary)
        self._check_completed()

        return issues_df, summary
