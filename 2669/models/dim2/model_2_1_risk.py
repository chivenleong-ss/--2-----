"""
Model 2.1: Risk Classification Red-Line Detection (风险分级红线检测).
Checks projects against 12 prohibited bidding conditions per policy.

Supports dual-regime year-scoping:
  - Old regime (pre-2026): 2020版《市场营销风险分级管理办法》
    Payment & profit thresholds tiered by 监管档次 (红/橙/黄/绿)
  - New regime (2026+): 手册0520
    Uniform thresholds + 组合判定 + 审批权下放
"""
import pandas as pd
from models.base_model import BaseModel
from utils.helpers import safe_float, safe_percent_rate, parse_date


class Model21Risk(BaseModel):
    model_id = "2.1"
    model_name = "风险分级红线检测与审批穿透"
    priority = "P0"
    dimension = "合同质量与风险"

    @staticmethod
    def _extract_signing_year(row) -> int:
        """Extract signing year from available date fields."""
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

    @staticmethod
    def _yes(value) -> bool:
        return str(value).strip() in {"是", "Y", "y", "yes", "Yes", "TRUE", "True", "true", "1"}

    def _build_restricted_bid_reason(self, row, payment_rules=None, is_re=False, reg_tier="", is_new=True) -> str:
        reasons = []

        monthly_pct = safe_float(row.get("月进度付款比例（%）", 0))
        non_cash = safe_float(row.get("非现金支付比例(%)", 0))
        c_value = safe_percent_rate(row.get("目标效益率（%）（C值）", 0))
        settlement_months = safe_float(row.get("结算周期（月）", 0))

        if self._yes(row.get("是否付款条件不达标")):
            reasons.append("付款条件被系统标记为不达标")
        if self._yes(row.get("是否垫资")):
            reasons.append("存在垫资")
        if self._yes(row.get("是否放弃优先受偿权")):
            reasons.append("存在放弃优先受偿权")

        perf_type = str(row.get("履约担保方式", "")).strip()
        if "现金" in perf_type:
            reasons.append(f"履约担保为现金方式（{perf_type}）")

        if payment_rules:
            if is_re:
                threshold = payment_rules.get(
                    "地产类_橙档_月进度付款比例下限" if reg_tier == "橙" else "地产类_黄绿档_月进度付款比例下限",
                    0.75 if reg_tier == "橙" else 0.70,
                )
                if 0 < monthly_pct < threshold:
                    reasons.append(f"月进度付款比例 {monthly_pct:.0%} 低于阈值 {threshold:.0%}")

                nc_limit = payment_rules.get("地产类_非现金支付上限", 0.15)
                if non_cash > nc_limit:
                    reasons.append(f"非现金支付比例 {non_cash:.0%} 高于阈值 {nc_limit:.0%}")
            else:
                ml = payment_rules.get("非地产类_月进度付款比例下限", 0.70)
                nl = payment_rules.get("非地产类_非现金支付上限", 0.15)
                if 0 < monthly_pct < ml:
                    reasons.append(f"月进度付款比例 {monthly_pct:.0%} 低于阈值 {ml:.0%}")
                if non_cash > nl:
                    reasons.append(f"非现金支付比例 {non_cash:.0%} 高于阈值 {nl:.0%}")

        if is_re and reg_tier == "红":
            reasons.append("地产类红档企业严禁承接新项目")
        if c_value != 0:
            reasons.append(f"目标效益率(C值)为 {c_value:.1%}")
        if settlement_months > 6:
            reasons.append(f"结算周期 {settlement_months:.0f} 个月，超过 6 个月")

        if not reasons:
            fallback_fields = [
                ("监管档次", row.get("监管档次")),
                ("月进度付款比例", row.get("月进度付款比例（%）")),
                ("非现金支付比例", row.get("非现金支付比例(%)")),
                ("目标效益率(C值)", row.get("目标效益率（%）（C值）")),
                ("履约担保方式", row.get("履约担保方式")),
            ]
            filled = [f"{name}={value}" for name, value in fallback_fields if pd.notna(value) and str(value).strip() != ""]
            if filled:
                return "系统标记为限制投标风险，相关字段：" + "；".join(filled[:5])
            return "系统标记为限制投标风险，但未回填明确触发字段，请核查源数据标记依据"

        deduped = []
        for item in reasons:
            if item not in deduped:
                deduped.append(item)
        return "系统标记为限制投标风险，触发原因：" + "；".join(deduped[:6])

    def run(self, dmp, appendices, region_auth=None):
        logger = self.logger
        forbidden_rules = self.config.get("institutional", {}).get("风险分级_严禁投标", {})
        payment_config = self.config.get("institutional", {}).get("付款条件底线", {})
        profit_config = self.config.get("institutional", {}).get("盈利底线", {})
        regime_year = self.config.get("institutional", {}).get("制度切换年份", 2026)

        # Pre-select regime sub-blocks (per-row selection happens in loop)
        payment_new = payment_config.get("手册0520", payment_config)
        payment_old = payment_config.get("历史_2020版", payment_config)
        profit_new = profit_config.get("手册0520", profit_config)
        profit_old = profit_config.get("历史_2020版", profit_config)

        df = dmp.copy()
        findings = []

        for idx, row in df.iterrows():
            proj_code = str(row.get("项目编码", ""))
            proj_name = str(row.get("项目名称", ""))
            customer = str(row.get("客户名称", ""))
            is_re = str(row.get("是否地产类项目", "")).strip() == "是"
            reg_tier = str(row.get("监管档次", "")).strip()
            contract_amt = safe_float(row.get("签约额（元）", 0))

            # --- Year-scoped regime selection ---
            signing_year = self._extract_signing_year(row)
            is_new = signing_year >= regime_year if signing_year else True
            payment_rules = payment_new if is_new else payment_old
            profit_rules = profit_new if is_new else profit_old

            violations = []

            # ==================================================================
            # Real Estate Red Lines
            # ==================================================================
            if is_re:
                # 1. Red-tier enterprise — always forbidden regardless of regime
                if reg_tier == "红":
                    violations.append({
                        "rule": "地产类_红档企业",
                        "desc": f"红档企业{customer}，严禁承接新项目",
                        "severity": "严禁投标"
                    })

                # 2. Monthly progress payment check
                monthly_pct = safe_float(row.get("月进度付款比例（%）", 0))
                if is_new:
                    # 手册0520附件4第11.1条：橙档<75%→严禁, 黄绿档<70%→严禁
                    if reg_tier == "橙":
                        threshold = payment_rules.get("地产类_橙档_月进度付款比例下限", 0.75)
                    else:
                        threshold = payment_rules.get("地产类_黄绿档_月进度付款比例下限", 0.70)
                    if 0 < monthly_pct < threshold:
                        violations.append({
                            "rule": "地产类_付款条件不达标",
                            "desc": f"月进度付款{monthly_pct:.0%} < {threshold:.0%}（{reg_tier}档·手册0520）",
                            "severity": "严禁投标"
                        })
                else:
                    # Old regime: tiered by 监管档次
                    if reg_tier == "橙":
                        threshold = payment_rules.get("地产类_橙档_月进度付款比例下限", 0.75)
                    else:
                        threshold = payment_rules.get("地产类_黄绿档_月进度付款比例下限", 0.70)
                    if 0 < monthly_pct < threshold:
                        violations.append({
                            "rule": "地产类_付款条件不达标",
                            "desc": f"月进度付款{monthly_pct:.0%} < {threshold:.0%}（{reg_tier}档·2020版）",
                            "severity": "严禁投标"
                        })

                # 3. Non-cash payment check
                # 手册0520附件4第11.5条: 非现金支付>15%→严禁（地产类）
                non_cash = safe_float(row.get("非现金支付比例(%)", 0))
                nc_limit = payment_rules.get("地产类_非现金支付上限", 0.15)
                if non_cash > nc_limit:
                    violations.append({
                        "rule": "地产类_非现金支付比例超标",
                        "desc": f"非现金支付{non_cash:.0%} > {nc_limit:.0%}",
                        "severity": "严禁投标"
                    })

                # 4. 垫资 — always forbidden for real estate
                if str(row.get("是否垫资", "")).strip() == "是":
                    violations.append({
                        "rule": "地产类_垫资",
                        "desc": "项目存在垫资",
                        "severity": "严禁投标"
                    })

                # 5. Profit rate check — tiered by regime
                c_value = safe_percent_rate(row.get("目标效益率（%）（C值）", 0))
                if is_new:
                    # 手册0520附件4第6条: 承接效益率≤0% → 严禁投标（全项目通用）
                    strict_universal = profit_rules.get("承接效益率_严禁投标_上限", 0.0)
                    if c_value <= strict_universal:
                        violations.append({
                            "rule": "地产类_预期净利润率不达标",
                            "desc": f"承接效益率{c_value:.1%} ≤ {strict_universal:.0%}，严禁投标（手册0520附件4第6条）",
                            "severity": "严禁投标"
                        })
                    # 手册0520附件5第9条: 橙档额外限制 0%<C值≤5% → 限制投标
                    if reg_tier == "橙" and c_value > strict_universal:
                        restrict = profit_rules.get("橙档地产_限制投标_利润率上限", 0.05)
                        if c_value <= restrict:
                            violations.append({
                                "rule": "地产类_橙档限制投标",
                                "desc": f"橙档承接效益率{c_value:.1%} 在({strict_universal:.0%}, {restrict:.0%}]，限制投标（手册0520附件5第9条）",
                                "severity": "限制投标"
                            })
                    else:
                        # Old regime: tiered by 监管档次
                        if reg_tier == "橙":
                            limit = profit_rules.get("地产类_橙档_净利润率下限", 0.05)
                        else:
                            limit = profit_rules.get("地产类_黄绿档_净利润率下限", 0.04)
                        if c_value < limit:
                            violations.append({
                                "rule": "地产类_预期净利润率不达标",
                                "desc": f"目标效益率{c_value:.1%} < {limit:.0%}（{reg_tier}档·2020版）",
                                "severity": "严禁投标"
                            })

                # 6. 放弃优先受偿权
                if str(row.get("是否放弃优先受偿权", "")).strip() == "是":
                    violations.append({
                        "rule": "地产类_放弃优先受偿权",
                        "desc": "放弃优先受偿权",
                        "severity": "严禁投标"
                    })

                # 7. 现金履约保证金
                perf_type = str(row.get("履约担保方式", "")).strip()
                if "现金" in perf_type:
                    violations.append({
                        "rule": "地产类_现金履约保证金",
                        "desc": f"履约担保为现金方式: {perf_type}",
                        "severity": "严禁投标"
                    })

                # 8. 结算审核期 > 6个月
                settlement_months = safe_float(row.get("结算周期（月）", 0))
                settlement_set = str(row.get("是否约定结算周期", "")).strip()
                if settlement_months > 6 or (settlement_set == "否" and is_re):
                    violations.append({
                        "rule": "地产类_结算审核期超6个月或未明确",
                        "desc": f"结算周期{settlement_months}月或未明确",
                        "severity": "严禁投标"
                    })

            # ==================================================================
            # Non-Real Estate
            # ==================================================================
            else:
                monthly_pct = safe_float(row.get("月进度付款比例（%）", 0))
                non_cash = safe_float(row.get("非现金支付比例(%)", 0))
                ml = payment_rules.get("非地产类_月进度付款比例下限", 0.70)
                # 手册0520附件4第9.3条: 非地产非现金支付>15%（组合判定条件之一）
                nl = payment_rules.get("非地产类_非现金支付上限", 0.15)

                if 0 < monthly_pct < ml:
                    violations.append({
                        "rule": "非地产类_付款比例不达标",
                        "desc": f"月进度付款{monthly_pct:.0%} < {ml:.0%}",
                        "severity": "限制投标"
                    })
                if non_cash > nl:
                    violations.append({
                        "rule": "非地产类_非现金支付超标",
                        "desc": f"非现金支付{non_cash:.0%} > {nl:.0%}",
                        "severity": "限制投标"
                    })

            # ==================================================================
            # Common checks (not year-scoped)
            # ==================================================================
            # 三证不全检查已移至模型2.4（合同条款风险穿透），制度依据：
            #   《市场营销风险分级管理办法》1.3.3（实质性开工前必须取得三证）
            #   《投标管理办法》2.9（投标总结复盘要求）
            # 2.4 仅对"已开工+三证不全"标记red，未开工项目不做准入拦截

            if str(row.get("是否限制投标风险", "")).strip() == "是":
                violations.append({
                    "rule": "限制投标风险",
                    "desc": self._build_restricted_bid_reason(
                        row,
                        payment_rules=payment_rules,
                        is_re=is_re,
                        reg_tier=reg_tier,
                        is_new=is_new,
                    ),
                    "severity": "限制投标"
                })

            # Record findings
            for v in violations:
                findings.append({
                    "模型编号": "2.1",
                    "项目编码": proj_code,
                    "项目名称": proj_name,
                    "客户名称": customer,
                    "问题分类": v["rule"],
                    "严重等级": v["severity"],
                    "问题描述": v["desc"],
                    "规则依据": v["rule"],
                    "签约额（元）": contract_amt,
                })

        # ==================================================================
        # 付款条件不达标标记交叉校验（v2.9新增）
        # DMP系统导出「是否付款条件不达标」字段，需经制度条件校验其填列是否正确
        # ==================================================================
        pay_fail_col = "是否付款条件不达标"
        if pay_fail_col in df.columns:
            for idx, row in df.iterrows():
                dmp_flag = str(row.get(pay_fail_col, "")).strip()
                if dmp_flag not in ("是", "否"):
                    continue  # skip empty / unset

                proj_code = str(row.get("项目编码", ""))
                proj_name = str(row.get("项目名称", ""))
                is_re = str(row.get("是否地产类项目", "")).strip() == "是"
                signing_year = self._extract_signing_year(row)
                is_new = signing_year >= regime_year if signing_year else True
                payment_rules_verify = payment_new if is_new else payment_old

                # Recalculate whether payment conditions SHOULD fail based on rules
                rule_should_fail = False
                fail_reasons = []

                monthly_pct = safe_float(row.get("月进度付款比例（%）", 0))
                non_cash = safe_float(row.get("非现金支付比例(%)", 0))

                if is_re:
                    if is_new:
                        # 手册0520附件4第11.1条: 橙档<75%, 黄绿档<70% → 严禁
                        if str(row.get("监管档次", "")).strip() == "橙":
                            threshold = payment_rules_verify.get("地产类_橙档_月进度付款比例下限", 0.75)
                        else:
                            threshold = payment_rules_verify.get("地产类_黄绿档_月进度付款比例下限", 0.70)
                    else:
                        if str(row.get("监管档次", "")).strip() == "橙":
                            threshold = payment_rules_verify.get("地产类_橙档_月进度付款比例下限", 0.75)
                        else:
                            threshold = payment_rules_verify.get("地产类_黄绿档_月进度付款比例下限", 0.70)
                    if 0 < monthly_pct < threshold:
                        rule_should_fail = True
                        fail_reasons.append(f"月进度付款{monthly_pct:.0%}<{threshold:.0%}")

                    # 手册0520附件4第11.5条: 非现金支付>15% → 严禁
                    nc_limit = payment_rules_verify.get("地产类_非现金支付上限", 0.15)
                    if non_cash > nc_limit:
                        rule_should_fail = True
                        fail_reasons.append(f"非现金支付{non_cash:.0%}>{nc_limit:.0%}")

                    # 手册0520附件4第11.4条: 地产类存在任何垫资 → 严禁
                    if str(row.get("是否垫资", "")).strip() == "是":
                        rule_should_fail = True
                        fail_reasons.append("存在垫资")

                else:
                    ml = payment_rules_verify.get("非地产类_月进度付款比例下限", 0.70)
                    # 手册0520附件4第9.3条: 非现金支付>15%（组合判定条件之一）
                    nl = payment_rules_verify.get("非地产类_非现金支付上限", 0.15)
                    if 0 < monthly_pct < ml:
                        rule_should_fail = True
                        fail_reasons.append(f"月进度付款{monthly_pct:.0%}<{ml:.0%}")
                    if non_cash > nl:
                        rule_should_fail = True
                        fail_reasons.append(f"非现金支付{non_cash:.0%}>{nl:.0%}")

                # Cross-validate
                if rule_should_fail and dmp_flag == "否":
                    findings.append({
                        "模型编号": "2.1",
                        "项目编码": proj_code,
                        "项目名称": proj_name,
                        "客户名称": str(row.get("客户名称", "")),
                        "问题分类": "付款条件标记校验不一致",
                        "严重等级": "red",
                        "问题描述": (
                            f"DMP标记为「否」（达标），但制度校验应判定为不达标：{'；'.join(fail_reasons)}"
                            f"（{'手册0520' if is_new else '2020版'}），疑似DMP填列错误或规则未同步"
                        ),
                        "规则依据": "付款条件不达标_制度交叉校验",
                        "签约额（元）": safe_float(row.get("签约额（元）", 0)),
                    })
                elif not rule_should_fail and dmp_flag == "是":
                    findings.append({
                        "模型编号": "2.1",
                        "项目编码": proj_code,
                        "项目名称": proj_name,
                        "客户名称": str(row.get("客户名称", "")),
                        "问题分类": "付款条件标记校验不一致",
                        "严重等级": "yellow",
                        "问题描述": (
                            f"DMP标记为「是」（不达标），但制度校验月进度{monthly_pct:.0%}/"
                            f"非现金{non_cash:.0%}未触发红线，请核实DMP标记依据"
                        ),
                        "规则依据": "付款条件不达标_制度交叉校验",
                        "签约额（元）": safe_float(row.get("签约额（元）", 0)),
                    })

        issues_df = pd.DataFrame(findings)
        if len(issues_df) > 0:
            issues_df = issues_df.sort_values("严重等级")

        red_count = len(issues_df[issues_df["严重等级"] == "严禁投标"]) if len(issues_df) > 0 else 0
        restrict_count = len(issues_df[issues_df["严重等级"] == "限制投标"]) if len(issues_df) > 0 else 0
        affected_projects = issues_df["项目编码"].nunique() if len(issues_df) > 0 else 0

        summary = {
            "total_projects_checked": len(df),
            "total_violations": len(issues_df),
            "严禁投标_red_line": red_count,
            "限制投标_restricted": restrict_count,
            "affected_projects": affected_projects,
        }

        logger.set_summary(**summary)
        logger.log_check("红档企业检查", red_count > 0, {"count": red_count})
        logger.log_check("付款条件底线检查", True, {"checked": len(df)})
        self._check_completed()

        return issues_df, summary
