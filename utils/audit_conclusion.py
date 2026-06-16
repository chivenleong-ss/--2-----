"""
审计结论映射引擎 — v1.0

将经营评分、底线触发、置信度、分布等组合映射为标准审计意见。
从"输出分数"到"输出结论"的最后一公里。

三层审计意见：
  ✅ 通过       — 无重大风险，按季度常规跟踪
  ⚠️ 附条件通过 — 存在短板或风险项，需限期整改
  ❌ 否决       — 触碰红线底线，需立即止损

调用方式::

    from utils.audit_conclusion import AuditConclusion
    engine = AuditConclusion(config)
    verdict = engine.assess_unit(
        total_score=68.5,
        module_scores={"模块一":82, ..., "模块六":90},
        veto_triggered=False,
        confidence=85.0,
        internal_dispersion=0.35,  # 标准差
        high_risk_ratio=0.22,      # 高危项目占比
    )
    # → {"opinion": "⚠️ 附条件通过", "reason": "...", "action": "..."}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditVerdict:
    opinion: str           # "通过" | "附条件通过" | "否决"
    reason: str            # 主要判定理由
    confidence: str        # "高" | "中" | "低"
    action: str            # 行动建议
    triggers: list = field(default_factory=list)  # 触发的具体条件


class AuditConclusion:
    """审计结论映射器 — 评分 → 审计意见."""

    def __init__(self, config: dict = None):
        cfg = config or {}
        bh = cfg.get("business_health", {})
        gsb = bh.get("global_score_bands", {})
        tsc = bh.get("total_score_constraints", {})
        bands = bh.get("score_bands", {})

        self.GLOBAL_STRONG = gsb.get("全局强势下限", 75)
        self.GLOBAL_STEADY = gsb.get("全局稳健下限", 60)
        self.MODULE_STRONG = bands.get("强势区下限", 80)
        self.MODULE_STEADY = bands.get("稳健区下限", 65)

        # 否决参数
        self.VETO_FLOOR = tsc.get("红线模块否决封顶", 60)
        self.CONTRACT_VETO_FLOOR = tsc.get("合同底线穿透封顶", 50)
        self.MIN_MODULE_WARN = tsc.get("最低模块阈值", 40)

        # 分布风险参数
        self.DISPERSION_WARN = 0.15  # 标准差 >0.15 标记分化
        self.HIGH_RISK_WARN = 0.20  # 高危占比 >20% 标记系统性风险

        # ── 十五五战略规划基准（供战略偏离检测）──
        strat_cfg = config.get("十五五战略规划", {}) if config else {}
        self._strat_global = strat_cfg.get("局级全局基准", {})

    def assess_unit(
        self,
        total_score: float,
        module_scores: dict[str, float],
        veto_triggered: bool = False,
        contract_veto: bool = False,
        confidence: float = 80.0,
        internal_dispersion: float = 0.0,
        high_risk_ratio: float = 0.0,
        weakest_modules: list[tuple] = None,
        score_trend: str = None,  # "rising" / "falling" / None
    ) -> dict:
        """对单位/公司进行审计定性判断。

        Args:
            total_score: 综合经营得分 (0-100)
            module_scores: {"模块一_区域布局": 82, "模块二_客户稳定": 65, ...}
            veto_triggered: 是否有红线模块被否决
            contract_veto: 是否触发合同底线否决（更严）
            confidence: 数据置信度 (0-100)
            internal_dispersion: 项目间R分标准差（内部一致性）
            high_risk_ratio: 高危项目占比
            weakest_modules: [(模块名, 得分), ...] 最弱模块
            score_trend: "rising" (向上) / "falling" (下滑) / None (无趋势数据)

        Returns:
            AuditVerdict 或 dict
        """
        reasons = []
        triggers = []

        # ── 层级1: 底线否决（最高优先级）──
        if contract_veto:
            reasons.append("合同底线严禁投标触发一票否决")
            triggers.append("contract_veto")
            return self._verdict("否决", reasons, confidence, "立即止损，启动问责：回溯审批链、暂停新增资源投入", triggers)

        if veto_triggered:
            reasons.append("红线模块被否决（合同/履约/资金致命缺陷）")
            triggers.append("redline_veto")
            return self._verdict("否决", reasons, confidence, "触发红线复盘：暂停相关项目推进，限期30天提交整改方案", triggers)

        # ── 层级2: 系统性风险（两极分化 / 高危集中）──
        if high_risk_ratio > self.HIGH_RISK_WARN:
            pct = int(high_risk_ratio * 100)
            reasons.append(f"高危项目占比 {pct}%，超过 {int(self.HIGH_RISK_WARN*100)}% 阈值，存在系统性管控失守风险")
            triggers.append("systemic_risk")
            if total_score >= self.GLOBAL_STRONG:
                reasons.append("⚠️ 注意：综合得分虽高，但长尾项目严重拖累——业绩由少数明星项目支撑，整体失控")
                return self._verdict("附条件通过", reasons, confidence,
                    "建议针对低分长尾项目开展飞行审计，不要被高分项目掩护", triggers)

        if internal_dispersion > self.DISPERSION_WARN:
            reasons.append(f"内部分化度 {internal_dispersion:.2f} 偏高，管理标准不统一")
            triggers.append("high_dispersion")

        # ── 层级3: 短板风险 ──
        if weakest_modules:
            weak_mod, weak_score = weakest_modules[0]
            if weak_score < self.MIN_MODULE_WARN:
                reasons.append(f"最弱模块 {weak_mod} 得分仅 {weak_score:.0f}，严重拖累整体健康")
                triggers.append("weakest_module_critical")
            elif weak_score < self.MODULE_STEADY:
                reasons.append(f"最弱模块 {weak_mod} 得分 {weak_score:.0f}，处于承压区，需专项提升")
                triggers.append("weakest_module_warning")

        # ── 层级4: 趋势风险 ──
        if score_trend == "falling":
            reasons.append("经营评分呈下滑趋势，需关注恶化速度")
            triggers.append("falling_trend")
        elif score_trend == "rising":
            reasons.append("经营评分呈上升趋势，改进行动初见成效")
            triggers.append("rising_trend")

        # ── 层级5: 综合得分判断 ──
        if total_score < self.GLOBAL_STEADY:
            reasons.append(f"综合得分 {total_score:.1f} 处于全局承压区 (<{self.GLOBAL_STEADY})")
            triggers.append("global_pressure")
            return self._verdict("附条件通过", reasons, confidence,
                "纳入重点经营诊断清单，建议分管领导牵头启动专项治理",
                triggers)

        if total_score < self.GLOBAL_STRONG:
            reasons.append(f"综合得分 {total_score:.1f} 处于全局稳健区 ({self.GLOBAL_STEADY}-{self.GLOBAL_STRONG})")
            triggers.append("global_steady")

            if ["high_dispersion", "weakest_module_warning", "falling_trend"]:
                return self._verdict("附条件通过", reasons, confidence,
                    "整体可控但存在短板/分化/下滑趋势，建议针对性提升",
                    triggers)
            return self._verdict("通过", reasons, confidence,
                "按季度常规跟踪即可，关注短板模块不放任恶化",
                triggers)

        # ── 强势区 ──
        reasons.append(f"综合得分 {total_score:.1f} 处于强势区 (≥{self.GLOBAL_STRONG})")
        triggers.append("global_strong")

        if triggers:  # 有分化或其他负面信号
            return self._verdict("通过", reasons, confidence,
                "整体优秀，管理理念成熟——但留意内部分化不放松",
                triggers)

        return self._verdict("通过", reasons, confidence,
            "经营健康，继续巩固核心竞争力。可作为标杆单位推广经验。",
            triggers)

    def assess_project(
        self,
        r_score: float,
        e_score: float,
        grid_name: str,
        veto_reason: str = "",
        confidence: float = 80.0,
    ) -> dict:
        """对单个项目进行审计定性判断。

        Args:
            r_score: R轴得分 (1-3, 越大越差)
            e_score: E轴得分 (1-3, 越大越好)
            grid_name: 九宫格位置名称
            veto_reason: 一票否决原因（如有）
            confidence: 数据置信度

        Returns:
            verdict dict
        """
        reasons = []
        triggers = []

        # 底线否决
        if veto_reason:
            reasons.append(veto_reason)
            triggers.append("veto")
            return self._verdict("否决", reasons, confidence,
                "立即停止新资源投入，启动红线复盘与专项整改",
                triggers)

        # 九宫格判断
        if r_score >= 2.5:
            triggers.append("high_risk")
            if e_score <= 1.5:
                reasons.append(f"处于 {grid_name}（高风险+低收益）——典型饮鸩止渴式营销")
                return self._verdict("否决", reasons, confidence,
                    "原则上建议退出，释放资源投向优质项目；如确需保留则需提交专项论证",
                    triggers)
            reasons.append(f"处于 {grid_name}（高风险）——建议限定观察窗口，逾期未改则启动撤退")
            return self._verdict("附条件通过", reasons, confidence,
                "限期6个月整改，逾期未改善则转入淘汰处置",
                triggers)

        if e_score >= 2.5:
            triggers.append("high_return")
            if r_score <= 1.5:
                reasons.append(f"处于 {grid_name}（低风险+高收益）——优质项目")
                return self._verdict("通过", reasons, confidence,
                    "可考虑资源倾斜，扩大市场份额，提炼经验模板推广",
                    triggers)
            reasons.append(f"处于 {grid_name}（高收益但中高风险）——值得培育但需管控下行风险")
            return self._verdict("附条件通过", reasons, confidence,
                "加强过程监控，月度跟踪资金回收与履约进度",
                triggers)

        # 中间地带
        if confidence < 50:
            reasons.append(f"数据置信度仅 {confidence:.0f}%，结论可靠性有限——建议补全数据后复评")
            triggers.append("low_confidence")
            return self._verdict("附条件通过", reasons, confidence,
                "建议优先补充缺失字段，提高数据质量后重新审计",
                triggers)

        reasons.append(f"处于 {grid_name}，按既有策略管理")
        return self._verdict("通过", reasons, confidence,
            "常规管理，按既有九宫格处置策略执行。次季度复查未恶化即可",
            triggers)

    def assess_scope(
        self,
        scope_name: str,
        total_score: float,
        module_scores: dict,
        veto_triggered: bool = False,
        confidence: float = 80.0,
        strategic_deviation: dict = None,
        secondary_unit_type: str = None,  # "优质型"|"均衡型"|"风险型"
    ) -> dict:
        """对单位/城市进行完整审计评估（带战略维度）。

        Args:
            scope_name: 单位/城市名称
            total_score: 综合经营得分
            module_scores: 六模块得分
            veto_triggered: 是否有红线触发
            confidence: 数据置信度
            strategic_deviation: {"板块": {"actual": 0.20, "target": 0.35}, ...}
            secondary_unit_type: 离散分析得出的单位类型

        Returns:
            完整审计结论 dict
        """
        reasons = []
        triggers = []

        # ── 合同底线 ──
        if veto_triggered:
            weak = min(module_scores.items(), key=lambda x: x[1]) if module_scores else (None, 0)
            reasons.append(f"红线触发——模块 {weak[0]} 被否决（{weak[1]}分）")
            return self._verdict("否决", reasons, confidence,
                "暂停该单位新增项目审批，启动全面合规复查", triggers)

        # ── 战略偏离 ──
        if strategic_deviation:
            for sector, dev in strategic_deviation.items():
                gap = abs(dev.get("actual", 0) - dev.get("target", 0))
                if gap > 0.10:  # 偏离超10%
                    reasons.append(
                        f"十五五战略偏离：{sector} 实际占比 {dev['actual']:.1%}，"
                        f"目标 {dev['target']:.1%}，偏差 {gap:.1%}"
                    )
                    triggers.append("strategic_deviation")

        # ── 分数分带 ──
        if total_score < self.GLOBAL_STEADY:
            reasons.append(f"综合健康分 {total_score:.1f} 承压区")
            action = "纳入重点治理清单"
        elif total_score < self.GLOBAL_STRONG:
            reasons.append(f"综合健康分 {total_score:.1f} 稳健区")
            action = "针对短板模块专项提升"
        else:
            reasons.append(f"综合健康分 {total_score:.1f} 强势区")
            action = "巩固优势，警惕战略偏离"

        # ── 单位类型强化 ──
        if secondary_unit_type == "风险型":
            triggers.append("unit_type_risky")
            if total_score > self.GLOBAL_STEADY:
                reasons.append("⛔ 离散分析将本单位归为『风险型』，即使综合分尚可也必须重视项目质量分化")
                action += "；建议对九宫格淘汰区项目逐个过筛"
        elif secondary_unit_type == "均衡型":
            triggers.append("unit_type_balanced")

        # ── 战略偏离特殊处理 ──
        if "strategic_deviation" in triggers:
            action += "；建议向战略部门预警，限制传统房建接单配额"

        # ── 置信度 ──
        if confidence < 50:
            reasons.append(f"数据置信度 {confidence:.0f}%，结论可靠性受限")
            triggers.append("low_confidence")

        opinion = "否决" if veto_triggered else (
            "附条件通过" if (total_score < self.GLOBAL_STRONG or triggers)
            else "通过"
        )

        return self._verdict(opinion, reasons, confidence, action, triggers)

    # ── 批量方法 ──

    def batch_assess_units(self, units: list[dict]) -> list[dict]:
        """批量审计单位，返回带结论的单位列表."""
        results = []
        for u in units:
            verdict = self.assess_scope(
                scope_name=u.get("名称", u.get("申报单位", "未知")),
                total_score=u.get("综合得分", 0),
                module_scores={k: u.get(k, 0) for k in
                    ["模块一_得分", "模块二_得分", "模块三_得分", "模块四_得分", "模块五_得分", "模块六_得分"]
                },
                veto_triggered=u.get("红线触发", False),
                confidence=u.get("平均置信度", u.get("数据置信度", 80)),
                secondary_unit_type=u.get("单位类型"),
            )
            results.append({**u, "审计意见": verdict})
        return results

    # ── 内部方法 ──

    def _verdict(self, opinion, reasons, confidence, action, triggers=None) -> dict:
        """构建统一意见结构."""
        conf_label = "高" if confidence >= 80 else ("中" if confidence >= 50 else "低")
        return {
            "opinion": opinion,
            "reason": "；".join(reasons) if reasons else "结构健康，常规管理",
            "confidence": conf_label,
            "confidence_score": round(confidence, 1),
            "action": action,
            "triggers": triggers or [],
        }


# ═══════════════════════════════════════════════════════════════════
# 便捷工厂
# ═══════════════════════════════════════════════════════════════════


def create_audit_conclusion(config_path: str = None) -> AuditConclusion:
    """从配置文件创建审计结论引擎."""
    import json
    import os

    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "rules.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    return AuditConclusion(config)


# ═══════════════════════════════════════════════════════════════════
# 自检
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    import os

    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "rules.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    engine = AuditConclusion(config)

    print("=" * 60)
    print("  AuditConclusion 审计结论映射自检")
    print("=" * 60)

    # 情景A：底线穿透
    print("\n[情景A] 合同底线穿透")
    v = engine.assess_unit(72.0,
        {"模块一_区域布局": 80, "模块二_客户稳定": 75, "模块三_合同质量": 0,
         "模块四_履约盈利": 70, "模块五_资金效率": 65, "模块六_数据质量": 85},
        contract_veto=True, confidence=90)
    print(f"  → {v['opinion']}: {v['reason']}")
    print(f"  → 行动: {v['action']}")

    # 情景B：系统失控
    print("\n[情景B] 系统失控（高分+高分化）")
    v = engine.assess_unit(85.0,
        {"模块一_区域布局": 90, "模块二_客户稳定": 88, "模块三_合同质量": 82,
         "模块四_履约盈利": 92, "模块五_资金效率": 78, "模块六_数据质量": 95},
        internal_dispersion=0.45, high_risk_ratio=0.28, confidence=88,
        weakest_modules=[("模块五_资金效率", 78)])
    print(f"  → {v['opinion']}: {v['reason']}")
    print(f"  → 行动: {v['action']}")

    # 情景C：健康
    print("\n[情景C] 全面健康")
    v = engine.assess_unit(90.0,
        {"模块一_区域布局": 92, "模块二_客户稳定": 88, "模块三_合同质量": 95,
         "模块四_履约盈利": 90, "模块五_资金效率": 85, "模块六_数据质量": 92},
        internal_dispersion=0.08, high_risk_ratio=0.05, confidence=95)
    print(f"  → {v['opinion']}: {v['reason']}")
    print(f"  → 行动: {v['action']}")

    # 单项目
    print("\n[情景D] 单项目评估")
    v = engine.assess_project(3.0, 1.0, "淘汰区",
        veto_reason="合同底线严禁投标", confidence=85)
    print(f"  → {v['opinion']}: {v['reason']}")
    print(f"  → 行动: {v['action']}")
