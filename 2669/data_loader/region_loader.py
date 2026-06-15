"""
Load multi-year regional authorization tables (经营区域认定).

Supports 2023, 2024, 2025, 2026 designations:
  - 2023: 附件1：2023年局属生产单位国内经营区域认定情况 (PDF→CSV)
  - 2024: 中建四市字〔2024〕143号 附件1 (PDF→CSV)
  - 2025: 经营区域认定情况_2025.csv
  - 2026: 中建四局市场营销管理手册0520 附件2/4 (PDF→CSV)

Each year's designation may differ — region assignments evolve across years.
Models should match a project's signing year to the corresponding designation.

2026 changes (per handbook 0520):
  - 4-tier classification: 核心/重点/常规/普通 (previously 3-tier)
  - Cross-region threshold: 5亿 (previously 10亿)
  - Installation company professional sub-contracting exempt
  - Infrastructure projects not subject to region restrictions
"""
import pandas as pd
from pathlib import Path

BASE = Path(__file__).parent.parent

# Supported designation years
SUPPORTED_YEARS = [2023, 2024, 2025, 2026]


def load_region_authorization(filepath: str = None, year: int = None) -> pd.DataFrame:
    """
    Load regional authorization CSV for a specific year.

    If year is None, returns the 2026 designation (latest).
    If year is specified (2023/2024/2025/2026), loads the corresponding file.

    Columns: 局属二级单位, 局属三级单位, 核心城市(深耕区域),
             重点城市(深耕区域), 常规区域(省/市), [普通区域], 备注, 年份
    """
    if filepath is None:
        if year in SUPPORTED_YEARS:
            filepath = str(BASE / f"经营区域认定情况_{year}.csv")
        else:
            # Fallback to latest
            filepath = str(BASE / "经营区域认定情况_2026.csv")
            year = 2026

    df = pd.read_csv(filepath, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]

    # Ensure year column exists
    if "年份" not in df.columns:
        df["年份"] = year or 2026

    return df


def load_all_years() -> dict:
    """
    Load all available years of region designations.
    Returns dict: {2023: DataFrame, 2024: DataFrame, 2025: DataFrame, 2026: DataFrame}
    """
    result = {}
    for year in SUPPORTED_YEARS:
        try:
            result[year] = load_region_authorization(year=year)
        except FileNotFoundError:
            result[year] = pd.DataFrame()
    return result


def load_region_for_project_year(project_year: int) -> pd.DataFrame:
    """
    Load the region designation that matches a project's signing year.

    Maps signing year to the closest applicable designation:
    - 2023 and earlier → 2023 designation
    - 2024 → 2024 designation (中建四市字〔2024〕143号)
    - 2025 → 2025 designation
    - 2026 and later → 2026 designation (handbook 0520)
    """
    if project_year <= 2023:
        return load_region_authorization(year=2023)
    elif project_year == 2024:
        return load_region_authorization(year=2024)
    elif project_year == 2025:
        return load_region_authorization(year=2025)
    else:
        return load_region_authorization(year=2026)
