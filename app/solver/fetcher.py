import os
import httpx
import tempfile
from typing import Dict, Any, List
from urllib.parse import urljoin, urlparse

TMP_DIR = "/tmp/llm_quiz"
os.makedirs(TMP_DIR, exist_ok=True)

async def fetch_resources(parsed: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    """
    Look through parsed['resources'] and try to download any files (pdf/csv/json).
    Returns a dict mapping resource keys to local file paths and metadata.
    """
    results: Dict[str, Any] = {}
    resources = parsed.get("resources", [])
    async with httpx.AsyncClient(timeout=30) as client:
        for i, r in enumerate(resources):
            rtype = r.get("type")
            # URLs (a tags)
            if rtype == "url":
                href = r.get("url")
                if not href:
                    continue
                # resolve relative urls if any
                if href.startswith("/"):
                    href = urljoin(base_url, href)
                # only download likely file types (pdf, csv, xls, xlsx)
                path_lower = href.lower()
                if path_lower.endswith(".pdf") or ".pdf?" in path_lower:
                    try:
                        resp = await client.get(href)
                        if resp.status_code == 200:
                            fname = os.path.join(TMP_DIR, f"res_{i}.pdf")
                            with open(fname, "wb") as fh:
                                fh.write(resp.content)
                            results[f"res_{i}"] = {"type": "pdf", "path": fname, "url": href}
                    except Exception:
                        continue
                # consider CSV/JSON too in future
            # embedded base64 (maybe a PDF encoded)
            elif rtype == "embedded_base64":
                content = r.get("content", "")
                # if content looks like a base64-encoded PDF, try to decode
                try:
                    import base64
                    data = base64.b64decode(content)
                    # quick check for PDF magic bytes
                    if data[:4] == b"%PDF":
                        fd, fname = tempfile.mkstemp(suffix=".pdf", dir=TMP_DIR)
                        os.write(fd, data)
                        os.close(fd)
                        results[f"embedded_{i}"] = {"type": "pdf", "path": fname}
                except Exception:
                    continue
            # embedded text blocks may contain direct file URLs — try to find them
            elif rtype == "embedded":
                txt = r.get("content", "")
                # quick URL detection
                for token in txt.split():
                    if token.lower().endswith(".pdf") or ".pdf?" in token.lower():
                        href = token.strip().strip('",')
                        if href.startswith("/"):
                            href = urljoin(base_url, href)
                        try:
                            resp = await client.get(href)
                            if resp.status_code == 200:
                                fname = os.path.join(TMP_DIR, f"res_emb_{i}.pdf")
                                with open(fname, "wb") as fh:
                                    fh.write(resp.content)
                                results[f"res_emb_{i}"] = {"type": "pdf", "path": fname, "url": href}
                                break
                        except Exception:
                            continue
    return results
