# app/solver/parser.py
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup
import re
import json
from urllib.parse import urljoin

# heuristics to decide what looks like a submit URL
SUBMIT_HINTS = ("submit", "/api/", "answer", "post")

# regex to find absolute URLs and simple relative paths
URL_RE = re.compile(r"https?://[^\s'\"<>]+|/[\w\-/]+", re.IGNORECASE)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _find_submit_link(soup: BeautifulSoup, base_url: Optional[str]) -> Optional[str]:
    """Find <a> or <form> elements that look like submit endpoints."""
    # preferred: <a href="/submit"> or similar
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").lower()
        if any(h in href.lower() for h in SUBMIT_HINTS) or any(h in text for h in SUBMIT_HINTS):
            return urljoin(base_url or "", href)

    # fallback: <form action="...">
    form = soup.find("form", action=True)
    if form:
        return urljoin(base_url or "", form["action"])

    return None


def _collect_links(soup: BeautifulSoup, base_url: Optional[str]) -> List[Dict[str, str]]:
    """Collect all <a href> links."""
    out: List[Dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url or "", a["href"])
        out.append({"type": "url", "url": href, "text": (a.get_text() or "").strip()})
    return out


def _collect_pre_blocks(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Collect <pre> and <code> blocks."""
    out = []
    for pre in soup.find_all(["pre", "code"]):
        txt = pre.get_text().strip()
        if txt:
            out.append({"type": "embedded", "content": txt})
    return out


def _collect_atob_payloads(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Find atob(`...`) inside scripts and extract the base64 payload."""
    out = []
    script_texts = " ".join(
        s.get_text(separator=" ") for s in soup.find_all("script")
    )
    for m in re.finditer(r'atob\(\s*([`\'"])(.+?)\1\s*\)', script_texts, flags=re.DOTALL):
        payload = m.group(2).strip()
        if payload:
            out.append({"type": "embedded_base64", "content": payload})
    return out


def _find_url_in_text(text: str) -> Optional[str]:
    """Find generic URLs in visible text."""
    if not text:
        return None
    m = URL_RE.search(text)
    if m:
        return m.group(0)
    return None


# ------------------------------------------------------------
# Main function
# ------------------------------------------------------------

def parse_quiz_page(html: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Parse rendered HTML and return:
      - question_text: visible text
      - submit_url: best guess URL
      - resources: list of detected resources (URLs, <pre>, atob)
    """
    soup = BeautifulSoup(html, "html.parser")

    # ------------------ Extract visible text ------------------
    body = soup.body
    if body:
        text = body.get_text(separator="\n").strip()
    else:
        text = soup.get_text(separator="\n").strip()

    # collapse excessive blank lines
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    question_text = text[:8000]  # safety limit

    # ------------------ Try anchor/form submit ------------------
    submit_url = _find_submit_link(soup, base_url)

    # ------------------ Collect resources ------------------
    links = _collect_links(soup, base_url)
    pre_blocks = _collect_pre_blocks(soup)
    atob_blocks = _collect_atob_payloads(soup)

    resources: List[Dict[str, str]] = []
    resources.extend(pre_blocks)
    resources.extend(atob_blocks)
    resources.extend(links)

    # ------------------------------------------------------------
    # Priority logic:
    # 1) prefer an explicit /submit-like token in visible text
    # 2) otherwise use anchor/form detection (submit_url already may have it)
    # 3) otherwise consider embedded JSON 'url' value as candidate
    # 4) finally fallback to generic URL detection
    # ------------------------------------------------------------

    # 1) scan visible text for /submit-like tokens
    submit_candidate_from_text: Optional[str] = None
    tokens = question_text.split()
    for token in tokens:
        if token.startswith("/") and any(h in token.lower() for h in SUBMIT_HINTS):
            submit_candidate_from_text = token.strip()
            break

    if submit_candidate_from_text:
        submit_url = urljoin(base_url or "", submit_candidate_from_text)
    else:
        # 2) submit_url may already be set from anchors/forms (_find_submit_link)
        if not submit_url:
            # 3) attempt to find a candidate from embedded JSON (do not return immediately)
            embedded_json_candidate: Optional[str] = None
            for r in resources:
                if r.get("type") == "embedded":
                    txt = r.get("content", "").strip()
                    try:
                        obj = json.loads(txt)
                        for key in ("submit", "submit_url", "url", "endpoint", "action"):
                            if key in obj and obj[key]:
                                candidate = str(obj[key]).strip()
                                embedded_json_candidate = urljoin(base_url or "", candidate)
                                break
                        if embedded_json_candidate:
                            break
                    except Exception:
                        pass
            if embedded_json_candidate:
                submit_url = embedded_json_candidate

    # 4) final fallback: find any URL-like token in visible text
    if submit_url is None:
        candidate = _find_url_in_text(question_text)
        if candidate:
            submit_url = urljoin(base_url or "", candidate)

    # ------------------ Final return ------------------
    return {
        "question_text": question_text,
        "submit_url": submit_url,
        "resources": resources,
    }


