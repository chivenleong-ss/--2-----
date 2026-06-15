import json
from datetime import datetime
from pathlib import Path


class VerificationLogger:
    """Per-model verification logger: output -> verify -> result"""

    def __init__(self, model_id: str, output_dir: str = "output/verification_logs"):
        self.model_id = model_id
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.warnings = []
        self.errors = []
        self.summary_stats = {}
        self.start_time = datetime.now()

    def log_check(self, description: str, passed: bool, details: dict = None):
        entry = {
            "type": "check",
            "description": description,
            "passed": passed,
            "details": details or {},
            "timestamp": datetime.now().isoformat()
        }
        self.checks.append(entry)

    def log_warning(self, item: str, context: dict = None):
        entry = {
            "type": "warning",
            "item": item,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }
        self.warnings.append(entry)

    def log_error(self, item: str, severity: str = "medium", context: dict = None):
        entry = {
            "type": "error",
            "item": item,
            "severity": severity,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }
        self.errors.append(entry)

    def set_summary(self, **kwargs):
        self.summary_stats.update(kwargs)

    def flush(self):
        """Write verification log to file"""
        passed = sum(1 for c in self.checks if c["passed"])
        failed = sum(1 for c in self.checks if not c["passed"])
        total = len(self.checks)

        report = {
            "model_id": self.model_id,
            "execution_time": {
                "start": self.start_time.isoformat(),
                "end": datetime.now().isoformat(),
                "duration_seconds": (datetime.now() - self.start_time).total_seconds()
            },
            "summary": {
                "total_checks": total,
                "passed": passed,
                "failed": failed,
                "warnings": len(self.warnings),
                "errors": len(self.errors),
                **self.summary_stats
            },
            "checks": self.checks,
            "warnings": self.warnings,
            "errors": self.errors
        }

        log_path = self.output_dir / f"{self.model_id}_verification.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # Also write a human-readable summary
        txt_path = self.output_dir / f"{self.model_id}_summary.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"=== {self.model_id} 验证报告 ===\n\n")
            f.write(f"执行时间: {report['execution_time']['duration_seconds']:.1f}秒\n")
            f.write(f"检查总数: {total} | 通过: {passed} | 未通过: {failed}\n")
            f.write(f"警告: {len(self.warnings)} | 错误: {len(self.errors)}\n\n")
            if self.errors:
                f.write("--- 错误列表 ---\n")
                for e in self.errors:
                    f.write(f"  [{e['severity']}] {e['item']}\n")
            if self.warnings:
                f.write("--- 警告列表 ---\n")
                for w in self.warnings:
                    f.write(f"  - {w['item']}\n")

        return report

    def print_summary(self):
        passed = sum(1 for c in self.checks if c["passed"])
        failed = sum(1 for c in self.checks if not c["passed"])
        print(f"  [{self.model_id}] checks: {len(self.checks)} passed/{failed} failed, "
              f"warnings: {len(self.warnings)}, errors: {len(self.errors)}")
