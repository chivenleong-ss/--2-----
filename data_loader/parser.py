"""
Generic parser for pipe-delimited extracted text files.
Handles: multi-line cells, header row detection, transpose section truncation.
"""
import re
import pandas as pd
from typing import List, Tuple


def count_separators(line: str) -> int:
    """Count the number of ' | ' separators in a line."""
    return line.count(" | ")


def parse_extract_text(filepath: str, skip_transpose: bool = True) -> pd.DataFrame:
    """
    Parse an extracted text file with pipe-delimited columns.

    Features:
    - Detects header row by finding first line with delimiters
    - Merges multi-line cells (continuation lines have fewer delimiters)
    - Truncates at '转换成列' marker (DMP files)
    - Handles multi-row headers (for appendix tables)

    Returns a pandas DataFrame.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    # Strip newlines
    lines = [line.rstrip("\n\r") for line in raw_lines]

    # Find the first non-empty data row to determine column count
    # Skip the === Sheet: ... === metadata line
    data_start = 0
    for i, line in enumerate(lines):
        if " | " in line and not line.strip().startswith("==="):
            data_start = i
            break

    if data_start == 0:
        raise ValueError(f"No pipe-delimited data found in {filepath}")

    # Determine the expected number of columns from the header
    header_line = lines[data_start]
    expected_cols = count_separators(header_line) + 1

    # Collect all data rows (before transpose)
    all_rows = []
    current_row = None

    for i in range(data_start, len(lines)):
        line = lines[i].strip()

        # Stop at transpose marker
        if skip_transpose and "转换成列" in line:
            break

        if not line:
            continue

        sep_count = count_separators(line)

        # A line is a "new row" if it has enough separators (not a text continuation).
        # DMP rows may have fewer than expected due to trailing empty cells.
        # Continuation lines (contract text) typically have 0-5 separators.
        # Use >= 10 as threshold — real data rows always have many more.
        is_new_row = sep_count >= max(10, expected_cols * 0.05)

        if is_new_row:
            if current_row is not None:
                all_rows.append(current_row)
            cells = [c.strip() for c in line.split(" | ")]
            current_row = cells
        else:
            # This is a continuation of the previous row's last cell
            if current_row is not None:
                current_row[-1] += "\n" + line

    # Don't forget the last row
    if current_row is not None:
        all_rows.append(current_row)

    if not all_rows:
        return pd.DataFrame()

    # First row is header
    header = all_rows[0]
    data_rows = all_rows[1:]

    # Ensure all data rows have the same number of columns as header
    # Pad or truncate as needed
    ncols = len(header)
    normalized_rows = []
    for row in data_rows:
        if len(row) < ncols:
            row = row + [""] * (ncols - len(row))
        elif len(row) > ncols:
            row = row[:ncols]
        normalized_rows.append(row)

    df = pd.DataFrame(normalized_rows, columns=header)

    # Remove completely empty rows
    df = df.dropna(how="all")
    df = df[~(df.astype(str).apply(lambda row: row.str.strip().eq("").all(), axis=1))]
    df = df[~(df.iloc[:, 0].astype(str).str.strip().eq(""))]

    return df.reset_index(drop=True)


def parse_appendix_with_multiline_header(
    filepath: str,
    header_start_marker: str = None,
    column_mapping: dict = None
) -> pd.DataFrame:
    """
    Parse an appendix text file with complex multi-row headers.

    Strategy: read all lines, find the data section, build a manual column mapping,
    then parse data rows with known column indices.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    text = "".join(lines)

    # Split by the pipe delimiter approach — find all data rows
    data_lines = []
    for line in lines:
        line = line.strip()
        if " | " in line and not line.startswith("==="):
            data_lines.append(line)

    if not data_lines:
        return pd.DataFrame()

    # Parse all lines as pipe-delimited
    rows = []
    for line in data_lines:
        cells = [c.strip() for c in line.split(" | ")]
        rows.append(cells)

    if not rows:
        return pd.DataFrame()

    # Determine max columns
    max_cols = max(len(r) for r in rows)

    # Normalize all rows to max_cols
    normalized = []
    for row in rows:
        if len(row) < max_cols:
            row = row + [""] * (max_cols - len(row))
        normalized.append(row)

    df = pd.DataFrame(normalized)

    # Use the first row as a temporary header
    # The actual column renaming is done by each appendix loader
    df.columns = [str(c).strip() for c in df.iloc[0]]
    df = df.iloc[1:].reset_index(drop=True)

    # Remove empty rows
    df = df.dropna(how="all")
    df = df[~(df.astype(str).apply(lambda row: row.str.strip().eq("").all(), axis=1))]

    return df.reset_index(drop=True)
