import re
from datetime import datetime


def parse_amount(value) -> float:
    """Parse a currency amount string to float. Returns 0.0 for empty/invalid."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if not value or value in ("/", "-", "N/A", "nan", "None"):
        return 0.0
    # Remove commas, spaces
    value = value.replace(",", "").replace(" ", "").replace("，", "")
    try:
        return float(value)
    except ValueError:
        return 0.0


def safe_float(value, default=0.0) -> float:
    """Safely convert a value to float."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if not value or value in ("/", "-", "N/A", "nan", "None", ""):
        return default
    try:
        return float(value.replace(",", "").replace(" ", "").replace("，", ""))
    except ValueError:
        return default


def safe_percent_rate(value, default=0.0) -> float:
    """
    Convert a percentage field to a decimal rate for rule comparison.

    Excel exports may provide a percent-formatted cell as 0.05, while text exports
    often provide the same field as 5. Both should mean 5%.
    """
    number = safe_float(value, default)
    if number == default:
        return default
    return number / 100 if abs(number) > 1 else number


def parse_date(value) -> datetime:
    """Parse a date string to datetime. Returns None for invalid."""
    if value is None or str(value).strip() in ("", "/", "-", "N/A", "nan", "None"):
        return None
    value = str(value).strip()
    # Try common formats
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y.%m"]:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def extract_city(address: str) -> str:
    """Extract city name from project address like '中国/江苏省/苏州市/张家港市'."""
    if not address or str(address).strip() in ("", "/", "N/A", "nan"):
        return ""
    address = str(address).strip()
    # Format: 中国/省/市/区  or direct city name
    parts = address.split("/")
    if len(parts) >= 3:
        city = parts[2]
        # Remove "市" suffix for matching
        return city.replace("市", "")
    return address


def extract_province(address: str) -> str:
    """Extract province name from project address."""
    if not address or str(address).strip() in ("", "/", "N/A", "nan"):
        return ""
    parts = str(address).strip().split("/")
    if len(parts) >= 2:
        return parts[1].replace("省", "")
    return ""


def parse_percentage(value) -> float:
    """Parse percentage value. Handles both decimal (0.08) and percentage (8.0) forms."""
    v = safe_float(value)
    if v > 1.0:
        v = v / 100.0
    return v


def is_real_estate(business_type: str, is_real_estate_flag: str) -> bool:
    """Determine if a project is real estate."""
    if str(is_real_estate_flag).strip() == "是":
        return True
    if "地产" in str(business_type or ""):
        return True
    return False
