"""
全面数字化营销审计系统 — 端到端执行入口

Usage:
  python main.py                     # Run all models and generate report
  python main.py --phase P0          # Run only P0 models
  python main.py --model 2.1         # Run only model 2.1
  python main.py --report-only       # Generate report from existing outputs
"""
import gc
import sys
import os
import re
import json
import argparse
from datetime import datetime
import pandas as pd

# Enable unbuffered output for real-time logging in web app
# Line-buffering mode (buffering=1) for both stdout and stderr
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_loader import load_dmp, load_all_appendices
from data_loader.region_loader import load_all_years
from data_loader.data_adapter import build_unified_projects, enrich_region_data, load_bid_report, merge_qcc_risk
from models.dim2 import Model21Risk, Model22Profit, Model23Capital, Model24Clause, Model25Construction
from models.dim1 import Model11Region, Model12Business, Model13StrategicCustomer, Model14DataCheck
from models.dim3 import Model31WinLoss, Model32NewCustomer
# Model33Zombie v2.9已合并入Model31WinLoss
from correlation import run_chain_1, run_chain_2, run_chain_3
from models.discrete_analysis import DiscreteAnalyzer
from models.business_analysis import BusinessHealthAnalyzer
from report import generate_report, export_to_excel
from utils.logger import VerificationLogger
from utils.helpers import safe_float
from utils.project_status import (
    APPENDIX_MATCH_COLUMN,
    UNIFIED_STATUS_COLUMN,
    UnifiedProjectStatus,
    assess_project_status,
)


def load_config(config_path: str = "config/rules.json") -> dict:
    """Load configuration from JSON file."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_model_instance(model_id: str, config: dict, output_dir: str):
    """Factory function to get model instance by ID."""
    model_map = {
        "1.1": Model11Region,
        "1.2": Model12Business,
        "1.3": Model13StrategicCustomer,
        "1.4": Model14DataCheck,
        "2.1": Model21Risk,
        "2.2": Model22Profit,
        "2.3": Model23Capital,
        "2.4": Model24Clause,
        "2.5": Model25Construction,  # v2.9从2.2拆分
        "3.1": Model31WinLoss,
        "3.2": Model32NewCustomer,
        # 3.3 v2.9已合并入3.1
    }
    cls = model_map.get(model_id)
    if cls is None:
        raise ValueError(f"Unknown model: {model_id}")
    return cls(config=config, output_dir=output_dir)


def run_phase(phase: str, config: dict, dmp, appendices, region_auth, output_dir: str, data_logger: VerificationLogger):
    """Run all models in a priority phase."""
    phase_models = {
        "P0": ["2.1", "2.2", "2.3", "2.5"],
        "P1": ["1.1", "2.4"],
        "P2": ["1.2", "1.3", "3.1"],
        "P3": ["1.4", "3.2"],  # 3.3 v2.9已合并入3.1
    }
    model_ids = phase_models.get(phase, [])
    results = {}
    for mid in model_ids:
        print(f"\n{'='*60}")
        print(f"Running Model {mid}...")
        print(f"{'='*60}")
        model = get_model_instance(mid, config, output_dir)
        issues_df, summary = model.run(dmp, appendices, region_auth)
        model.save_output(issues_df)
        results[mid] = (issues_df, summary)
        data_logger.log_check(f"Model {mid} completed", True, {"issues": len(issues_df)})
    return results


def main():
    parser = argparse.ArgumentParser(description="全面数字化营销审计系统")
    parser.add_argument("--phase", choices=["P0", "P1", "P2", "P3", "ALL"], default="ALL",
                        help="Which priority phase to run")
    parser.add_argument("--model", type=str, help="Run model(s) — single (e.g., '2.1') or comma-separated (e.g., '1.1,2.1,2.2')")
    parser.add_argument("--prefilter-only", action="store_true", help="Run only the pre-filter layer and save its summary")
    parser.add_argument("--report-only", action="store_true", help="Generate report from existing outputs only")
    parser.add_argument("--output-dir", type=str, default="output", help="Output directory")
    parser.add_argument("--data-dir", type=str, default=".", help="Data directory (for extracted files)")
    args = parser.parse_args()

    # Initialize logging first thing
    print(f"[MAIN] Process started at {datetime.now().isoformat()}", flush=True)
    print(f"[MAIN] Args: phase={args.phase}, model={args.model}, prefilter_only={args.prefilter_only}", flush=True)

    os.makedirs(args.output_dir, exist_ok=True)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print("=" * 60)
    print("  全面数字化营销审计系统 — v2.9 (DMP数据驱动)")
    print("=" * 60)

    # Track overall data loading
    data_logger = VerificationLogger("data_pipeline", f"{args.output_dir}/verification_logs")

    if not args.report_only:
        # ===== Load Configuration =====
        print("\n[1/5] Loading configuration...")
        config = load_config()
        data_logger.log_check("Config loaded", True, {"rules": len(json.dumps(config))})

        # ===== Load Data =====
        print("\n[2/5] Loading data sources...")
        print("  Loading DMP data...")
        dmp_raw = load_dmp()
        print(f"  -> DMP: {len(dmp_raw)} projects, {len(dmp_raw.columns)} fields")

        print("  Loading appendix tables...")
        appendices = load_all_appendices()
        data_logger.log_check("Appendices loaded", len(appendices) == 6, {"sheets": list(appendices.keys())})

        print("  Loading multi-year regional authorization (2023/2024/2025)...")
        region_auth = load_all_years()
        total_rows = sum(len(df) for df in region_auth.values() if not df.empty)
        data_logger.log_check("Multi-year region auth loaded", total_rows > 0,
                              {"years": list(region_auth.keys()), "total_rows": total_rows})

        print("  Building unified project dataset (DMP primary + App1 supplement + 中标报量)...")
        bid_report = load_bid_report()
        print(f"  -> 中标报量: {len(bid_report)} projects")
        dmp = build_unified_projects(dmp_raw, appendices, bid_report)
        dmp = merge_qcc_risk(dmp)

        # Enrich with year-appropriate region designation
        # Derive signing year from 签约时间 / 合同签订年度
        def _get_signing_year(row):
            sign_time = row.get("签约时间")
            if pd.notna(sign_time):
                try:
                    return sign_time.year
                except Exception:
                    pass
            sign_year_str = str(row.get("合同签订年度", ""))
            m = re.search(r"(\d{4})", sign_year_str)
            if m:
                return int(m.group(1))
            return 2026  # fallback

        dmp["_sign_year"] = dmp.apply(_get_signing_year, axis=1)

        enriched_frames = []
        for year, group in dmp.groupby("_sign_year"):
            year_int = int(year)
            if year_int < 2023:
                year_int = 2023
            elif year_int > 2026:
                year_int = 2026
            from data_loader.region_loader import load_region_for_project_year
            region_df = load_region_for_project_year(year_int)
            if not region_df.empty:
                group = enrich_region_data(group, region_df)
            enriched_frames.append(group)

        dmp = pd.concat(enriched_frames, ignore_index=True)
        dmp = dmp.drop(columns=["_sign_year"])
        print(f"  -> Unified: {len(dmp)} projects, {len(dmp.columns)} fields")
        print(f"  -> Unique projects: {dmp['项目编码'].nunique()}")
        print(f"  -> Subsidiaries: {dmp['申报单位'].nunique()}")
        data_logger.log_check("Unified data built", len(dmp) > 0, {"rows": len(dmp), "projects": dmp["项目编码"].nunique()})

        # ===== 前置模型：合同额基础过滤与未开工检测（v2.8 + 统一项目状态） =====
        # 统一状态优先级：
        #   1. 附表项目状态（停工/退场/停缓建/未开工/在建）
        #   2. 签约有记录但附表无匹配 → 未开工
        #   3. 三维检测（开工时间 + 180天产值 + 360天收款）
        #
        # 剔除：统一状态 = 未开工
        # 保留但标记：停工 / 退场 / 停缓建（供模型2.2盈利分析）

        before_count = len(dmp)
        before_amt = dmp["签约额（元）"].apply(safe_float).sum()
        now = datetime.now()

        has_output = "实际完成产值" in dmp.columns
        has_receipt = "累计收款" in dmp.columns
        has_status = "项目状态" in dmp.columns
        has_appendix_flag = APPENDIX_MATCH_COLUMN in dmp.columns

        if not has_output:
            print("  -> [WARN] 缺少实际完成产值字段，未开工检测退化为仅看开工时间")
        if not has_receipt:
            print("  -> [WARN] 缺少累计收款字段，未开工检测退化为仅看开工时间+产值")
        if not has_appendix_flag:
            dmp[APPENDIX_MATCH_COLUMN] = False

        unstarted_reasons = []
        stopped_projects = []
        assessments = []

        for idx, row in dmp.iterrows():
            code = str(row.get("项目编码", ""))
            name = str(row.get("项目名称", ""))
            contract_amt = safe_float(row.get("签约额（元）", 0))
            assessment = assess_project_status(
                row,
                has_output=has_output,
                has_receipt=has_receipt,
                has_status=has_status,
                now=now,
            )
            assessments.append(assessment)

            sign = row.get("签约时间")
            days_since_sign = 0
            if sign is not None and not (isinstance(sign, float) and pd.isna(sign)):
                try:
                    days_since_sign = (now - pd.Timestamp(sign)).days
                except Exception:
                    days_since_sign = 0

            if assessment.prefilter_keep_for_profit_analysis:
                status_label = str(row.get("项目状态", assessment.unified_status)).strip()
                stopped_projects.append(
                    (code, name, status_label, contract_amt, days_since_sign, assessment.unified_status)
                )
                continue

            if assessment.prefilter_exclude:
                reason_str = "、".join(assessment.reasons) if assessment.reasons else str(assessment.status_source)
                unstarted_reasons.append((code, name, contract_amt, days_since_sign, [reason_str]))

        # Collect cross-check warnings (附表在施但3D怀疑未开工)
        cross_check_warnings = []
        for item in assessments:
            if item.cross_check_warning:
                cross_check_warnings.append({
                    "unified_status": str(item.unified_status),
                    "status_source": item.status_source,
                    "reasons": item.reasons,
                })

        dmp[UNIFIED_STATUS_COLUMN] = [item.unified_status for item in assessments]
        dmp["_status_source"] = [item.status_source for item in assessments]

        appendix_missing_count = sum(
            1 for item in assessments if item.status_source == "附表缺失推断"
        )
        if appendix_missing_count:
            print(f"  -> 附表无匹配(推断未开工): {appendix_missing_count}个")

        # === 输出：交叉验证告警（附表在施但3D疑似未开工）===
        if cross_check_warnings:
            print(f"  -> 三维交叉验证告警（附表在施但产值/收款/开工存疑）: {len(cross_check_warnings)}个")
            for w in cross_check_warnings[:5]:  # 仅展示前5个
                print(f"     [交叉验证] {w['status_source']} | {'；'.join(w['reasons'])}")
            if len(cross_check_warnings) > 5:
                print(f"     ... 等共{len(cross_check_warnings)}个")

        # === 输出：未开工项目 ===
        excluded_count = len(unstarted_reasons)
        excluded_amt = sum(safe_float(r[2]) for r in unstarted_reasons)

        if excluded_count > 0:
            print(f"  -> 剔除未开工项目: {excluded_count}个, {excluded_amt/1e8:.1f}亿")
            for code, name, amt, days, reasons in unstarted_reasons:
                reason_str = "、".join(reasons)
                print(f"     [未开工] {code} {str(name)[:30]} 签约{days}天 "
                      f"{amt/1e8:.1f}亿 | {reason_str}")

            excluded_codes = set(r[0] for r in unstarted_reasons)
            dmp = dmp[~dmp["项目编码"].astype(str).isin(excluded_codes)].copy()

        after_amt = dmp["签约额（元）"].apply(safe_float).sum()
        print(f"  -> 过滤后: {len(dmp)}个项目, {after_amt/1e8:.1f}亿 (剔除{excluded_count}个未开工)")

        # === 输出：停工/退场/停缓建项目（保留但供模型2.2关注） ===
        if stopped_projects:
            stopped_amt = sum(safe_float(r[3]) for r in stopped_projects)
            print(f"  -> 停工/退场/停缓建项目(保留分析): {len(stopped_projects)}个, {stopped_amt/1e8:.1f}亿")
            for code, name, status, amt, days, unified_status in stopped_projects:
                print(f"     [{status}] {code} {str(name)[:30]} 签约{days}天 {amt/1e8:.1f}亿")

        data_logger.log_check("Pre-filter: unstarted detection", True, {
            "before": before_count, "after": len(dmp),
            "excluded_unstarted": excluded_count,
            "stopped_退场停工停缓建": len(stopped_projects),
            "excluded_amt_亿": round(excluded_amt/1e8, 1)
        })

        prefilter_summary = {
            "prefilter_completed": True,
            "completed_at": datetime.now().isoformat(),
            "before_project_count": int(before_count),
            "after_project_count": int(len(dmp)),
            "excluded_unstarted_count": int(excluded_count),
            "excluded_unstarted_amount_yi": round(excluded_amt / 1e8, 4),
            "before_amount_yi": round(before_amt / 1e8, 4),
            "after_amount_yi": round(after_amt / 1e8, 4),
            "stopped_project_count": int(len(stopped_projects)),
            "stopped_project_amount_yi": round(sum(safe_float(r[3]) for r in stopped_projects) / 1e8, 4) if stopped_projects else 0.0,
            "has_output_field": bool(has_output),
            "has_receipt_field": bool(has_receipt),
            "has_status_field": bool(has_status),
            "has_appendix_match_field": bool(has_appendix_flag),
            "appendix_missing_unstarted_count": int(appendix_missing_count),
            "cross_check_warning_count": int(len(cross_check_warnings)),
            "cross_check_warnings": cross_check_warnings,
            "unified_status_counts": {
                str(status.value): int(sum(1 for item in assessments if item.unified_status == status))
                for status in UnifiedProjectStatus
            },
            "unstarted_projects": [
                {
                    "project_code": str(code),
                    "project_name": str(name),
                    "status": UnifiedProjectStatus.NOT_STARTED.value,
                    "contract_amount_yi": round(safe_float(amt) / 1e8, 4),
                    "days_since_sign": int(days),
                    "reasons": [str(x) for x in reasons],
                    "unified_status": UnifiedProjectStatus.NOT_STARTED.value,
                }
                for code, name, amt, days, reasons in unstarted_reasons
            ],
            "stopped_projects": [
                {
                    "project_code": str(code),
                    "project_name": str(name),
                    "status": str(status),
                    "contract_amount_yi": round(safe_float(amt) / 1e8, 4),
                    "days_since_sign": int(days),
                    "unified_status": str(unified_status),
                }
                for code, name, status, amt, days, unified_status in stopped_projects
            ],
        }

        dmp = dmp.drop(columns=["_status_source"], errors="ignore")
        prefilter_path = os.path.join(args.output_dir, "model_outputs", "_prefilter_summary.json")
        os.makedirs(os.path.dirname(prefilter_path), exist_ok=True)
        with open(prefilter_path, "w", encoding="utf-8") as f:
            json.dump(prefilter_summary, f, ensure_ascii=False, indent=2)

        if args.prefilter_only:
            data_logger.flush()
            print("\n[Prefilter-only] Pre-filter layer complete.")
            print(f"  Summary JSON: {prefilter_path}")
            print(f"  Before projects: {before_count}")
            print(f"  After projects:  {len(dmp)}")
            print(f"  Excluded:        {excluded_count}")
            print(f"  Stopped/kept:    {len(stopped_projects)}")
            return

        # ===== Run Models =====
        print("\n[3/5] Running audit models...")
        all_results = {}

        if args.model:
            # Run specified models (single or comma-separated)
            model_ids = [m.strip() for m in args.model.split(",") if m.strip()]
            print(f"\n  Running {len(model_ids)} model(s): {model_ids}")
            for mid in model_ids:
                print(f"\n  --- Model {mid} ---")
                model = get_model_instance(mid, config, args.output_dir)
                issues_df, summary = model.run(dmp, appendices, region_auth)
                model.save_output(issues_df)
                all_results[mid] = (issues_df, summary)
                print(f"  Model {mid} complete: {len(issues_df)} issues found")
        else:
            # Run by phase
            phases_to_run = ["P0", "P1", "P2", "P3"] if args.phase == "ALL" else [args.phase]
            for phase in phases_to_run:
                print(f"\n  --- Phase {phase} ---")
                results = run_phase(phase, config, dmp, appendices, region_auth, args.output_dir, data_logger)
                all_results.update(results)

        data_logger.flush()

        # ===== Save All Results =====
        print("\n[3.5/5] Saving model outputs...")
        import pickle
        cache_path = os.path.join(args.output_dir, "model_outputs", "_all_results.pkl")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            pickle.dump(all_results, f)
        print(f"  Cached to {cache_path}")

    else:
        # Report-only mode: load cached results
        print("\n[Report-only] Loading cached model outputs...")
        import pickle
        cache_path = os.path.join(args.output_dir, "model_outputs", "_all_results.pkl")
        if not os.path.exists(cache_path):
            print(f"ERROR: No cached results found at {cache_path}")
            print("Run full pipeline first: python main.py")
            sys.exit(1)
        with open(cache_path, "rb") as f:
            all_results = pickle.load(f)
        config = load_config()  # Still need config for report context
        print(f"  Loaded {len(all_results)} model outputs")

    # ===== Run Correlations =====
    print("\n[4/5] Running cross-model correlations...")
    chain_logger = VerificationLogger("correlation_chains", f"{args.output_dir}/verification_logs")
    chain_results = {}
    chain_results["chain_1"] = run_chain_1(all_results, chain_logger)
    chain_results["chain_2"] = run_chain_2(all_results, chain_logger)
    chain_results["chain_3"] = run_chain_3(all_results, chain_logger)
    chain_logger.flush()
    print(f"  Chains complete: {len(chain_results)} correlation chains analyzed")

    import pickle
    if args.report_only:
        print("\n[4.2/5] Loading cached discrete/business analysis...")
        disc_path = os.path.join(args.output_dir, "model_outputs", "_discrete_results.pkl")
        biz_path = os.path.join(args.output_dir, "model_outputs", "_business_results.pkl")
        if os.path.exists(disc_path):
            with open(disc_path, "rb") as f:
                discrete_results = pickle.load(f)
        else:
            discrete_results = {"summary": {"total_projects": 0}, "projects": [], "cities": [], "subsidiaries": []}
        if os.path.exists(biz_path):
            with open(biz_path, "rb") as f:
                business_results = pickle.load(f)
        else:
            business_results = {"summary": {"total_projects": 0}, "overview": {}, "subsidiaries": [], "cities": []}
        print(f"  Discrete cached projects: {discrete_results.get('summary', {}).get('total_projects', 0)}")
        print(f"  Business cached projects: {business_results.get('summary', {}).get('total_projects', 0)}")
    else:
        # ===== v2.10: Business Health Analysis (六模块) — 先于离散分析 =====
        print("\n[4.2/5] Running business health analysis (6 modules)...")
        business_analyzer = BusinessHealthAnalyzer(config)
        business_analyzer._set_model_cache(all_results)  # v2.10: 注入模型缓存
        business_results = business_analyzer.run(all_results, dmp)
        business_summary = business_results.get("summary", {})
        print(f"  Business health: {business_summary.get('covered_units', 0)} subsidiaries / {business_summary.get('covered_cities', 0)} cities")
        if business_summary.get("top_unit"):
            print(f"  Top subsidiary: {business_summary['top_unit']} ({business_summary.get('top_unit_score', 0)} points)")
        biz_path = os.path.join(args.output_dir, "model_outputs", "_business_results.pkl")
        with open(biz_path, "wb") as f:
            pickle.dump(business_results, f)

        # ===== v2.10: Discrete Analysis (九宫格) — 消费六模块结果 =====
        print("\n[4.3/5] Running discrete risk-return analysis (from 6-module indicators)...")
        appendix_df = appendices.get("appendix_1", pd.DataFrame())
        discrete_analyzer = DiscreteAnalyzer(config)
        discrete_results = discrete_analyzer.run_with_module_scores(
            all_results, dmp, business_results, appendix_df, region_auth
        )
        discrete_summary = discrete_results.get("summary", {})
        mode = discrete_results.get("_mode", "legacy")
        print(f"  Discrete analysis [{mode}]: {discrete_summary.get('total_projects', 0)} projects -> 9-grid")
        if discrete_summary.get("high_risk_count", 0) > 0:
            print(f"  High risk: {discrete_summary['high_risk_count']} ({discrete_summary.get('high_risk_pct', 0)}%)")
        if discrete_summary.get("avg_confidence"):
            print(f"  Avg confidence: {discrete_summary['avg_confidence']:.1f} | Low confidence projects: {discrete_summary.get('low_confidence_count', 0)}")
        disc_path = os.path.join(args.output_dir, "model_outputs", "_discrete_results.pkl")
        with open(disc_path, "wb") as f:
            pickle.dump(discrete_results, f)

        # ===== v2.10: 分阶段内存释放 =====
        print("\n[Memory] Releasing large DataFrames after analysis...")
        del dmp, dmp_raw, appendices, bid_report
        gc.collect()
        collected = gc.collect()
        print(f"  GC collected {collected} objects")

    # ===== Compliance Mapping (手册0520 附件6/7) =====
    print("\n[4.5/5] Mapping findings to supervision compliance framework...")
    from correlation.supervision_compliance import map_findings_to_violations, generate_compliance_summary
    compliance_df = map_findings_to_violations(all_results, config)
    compliance_summary = generate_compliance_summary(compliance_df)
    print(f"  Compliance mapping: {len(compliance_df)} findings -> {len(compliance_summary.get('by_category', {}))} violation categories")
    if compliance_summary.get("重大_violations", 0) > 0:
        print(f"  Major violations: {compliance_summary['重大_violations']} items")

    # ===== Generate Report =====
    print("\n[5/5] Generating audit reports...")
    report_path = generate_report(all_results, chain_results, f"{args.output_dir}/reports",
                                   compliance_df=compliance_df, compliance_summary=compliance_summary,
                                   discrete_results=discrete_results, business_results=business_results)
    excel_path = export_to_excel(all_results, chain_results, f"{args.output_dir}/reports",
                                  compliance_df=compliance_df, discrete_results=discrete_results,
                                  business_results=business_results)

    print("\n" + "=" * 60)
    print("  AUDIT COMPLETE")
    print("=" * 60)
    print(f"  Report (Markdown): {report_path}")
    print(f"  Report (Excel):    {excel_path}")
    print(f"  Verification logs: {args.output_dir}/verification_logs/")

    # Print summary
    total_issues = sum(len(df) for df, _ in all_results.values())
    total_red = sum(
        len(df[df["严重等级"].str.contains("red|严禁", na=False)])
        for df, _ in all_results.values() if len(df) > 0
    )
    print(f"\n  Total issues found: {total_issues}")
    print(f"  Red/critical:       {total_red}")
    print(f"  Models executed:    {len(all_results)}")
    print()


if __name__ == "__main__":
    main()
