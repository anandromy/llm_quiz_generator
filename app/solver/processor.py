from typing import Dict, Any
import os
import pdfplumber
import pandas as pd
import re
import math

def _clean_numeric_column(series: pd.Series) -> pd.Series:
    # Remove common thousands separators, currency signs, stray chars, then coerce
    s = series.astype(str).str.replace(r"[^\d\.\-\+eE]", "", regex=True)
    return pd.to_numeric(s, errors="coerce")

def _sum_value_column_from_table(df: pd.DataFrame) -> float:
    # search for a column whose name includes 'value' (case-insensitive)
    for col in df.columns:
        if isinstance(col, str) and "value" in col.lower():
            ser = _clean_numeric_column(df[col])
            return float(ser.sum(skipna=True))
    # if no header match, try to find any numeric column and assume it's value (fallback)
    for col in df.columns:
        ser = _clean_numeric_column(df[col])
        if ser.notna().sum() > 0:
            return float(ser.sum(skipna=True))
    return float("nan")

async def process_task(parsed: Dict[str, Any], resources: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given parsed page dict and downloaded resources, attempt to compute the requested answer.
    For PDF resources, look at page 2 (index 1) and try to extract a table and sum 'value' column.
    Returns {"answer": <number>} or {"error": "..."} on failure.
    """
    # find any PDF file in resources
    pdf_paths = [v["path"] for v in resources.values() if v.get("type") == "pdf" and os.path.exists(v.get("path", ""))]
    if not pdf_paths:
        return {"error": "no pdf resource found"}

    # prefer the first pdf
    pdf_path = pdf_paths[0]

    try:
        with pdfplumber.open(pdf_path) as pdf:
            # page index 1 -> page 2
            if len(pdf.pages) <= 1:
                return {"error": "pdf has fewer than 2 pages"}
            page = pdf.pages[1]
            # try extract_table (single table)
            table = page.extract_table()
            if table:
                header = table[0]
                rows = table[1:]
                df = pd.DataFrame(rows, columns=header)
                total = _sum_value_column_from_table(df)
                if not math.isnan(total):
                    # return integer if close to integer else float
                    if abs(total - round(total)) < 1e-9:
                        return {"answer": int(round(total))}
                    return {"answer": total}

            # fallback: try extracting multiple tables (returns list of tables)
            tables = page.extract_tables()
            for t in tables:
                if not t or len(t) < 2:
                    continue
                header = t[0]
                rows = t[1:]
                df = pd.DataFrame(rows, columns=header)
                total = _sum_value_column_from_table(df)
                if not math.isnan(total):
                    if abs(total - round(total)) < 1e-9:
                        return {"answer": int(round(total))}
                    return {"answer": total}

            # last fallback: try OCR-ish extraction by extracting text and finding numbers (less robust)
            txt = page.extract_text() or ""
            # find all numbers in the page text
            nums = re.findall(r"[-+]?\d[\d,\.]*", txt)
            cleaned = []
            for n in nums:
                nclean = re.sub(r"[^\d\.\-]", "", n)
                try:
                    cleaned.append(float(nclean))
                except Exception:
                    continue
            if cleaned:
                return {"answer": sum(cleaned)}
            return {"error": "no table or numbers found on page 2"}
    except Exception as e:
        return {"error": f"pdf processing failed: {str(e)}"}
