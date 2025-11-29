# app/workers/runner.py
import time
import json
import re
import base64
import httpx
from pathlib import Path
from typing import Dict, Any, Optional

# local modules (ensure these exist)
from app.storage.jobs import get_job, set_job_status, set_job_result
from app.browser.page_loader import load_page_html
from app.solver.parser import parse_quiz_page
from app.solver.fetcher import fetch_resources
from app.solver.pdf_utils import extract_pdf_text

import os

# Configure OpenAI / AIPipe endpoint and key via env
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("AIPIPE_API_KEY")
OPENAI_URL = os.getenv("OPENAI_URL", "https://api.openai.com/v1/chat/completions")
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")  # change if needed

TMP_DIR = Path("/tmp/llm_quiz")
TMP_DIR.mkdir(parents=True, exist_ok=True)


def _safe_json_load(s: str) -> Dict[str, Any]:
    """
    Try to parse JSON from string; if fails, try to extract a JSON substring.
    Return dict or {} on failure.
    """
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
        return {}


def _truncate_for_prompt(obj: Any, max_chars: int = 15000) -> str:
    j = json.dumps(obj, ensure_ascii=False)
    if len(j) <= max_chars:
        return j
    return j[:max_chars]


async def _call_llm(prompt_text: str, system: str = "You are a data analysis assistant. Output only JSON.", timeout: int = 40) -> Dict[str, Any]:
    """
    Call the configured LLM/chat completion endpoint with a two-message chat.
    Returns parsed JSON (dict) or raises.
    """
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY / AIPIPE_API_KEY not set in environment")

    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt_text},
        ],
        "temperature": 0.0,
        "max_tokens": 800,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(OPENAI_URL, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()

    # extract assistant content (OpenAI-format)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned no choices")
    txt = choices[0].get("message", {}).get("content", "")
    parsed = _safe_json_load(txt)
    return parsed


async def run_job(job_id: str):
    """
    Main async worker entrypoint. Called with create_task(run_job(job_id)).
    Behavior:
      - load job (payload must include email, secret, url)
      - fetch page HTML (Playwright)
      - parse page for question, submit_url, resources
      - fetch resources
      - extract text from resources
      - call LLM with full context (LLM returns answer JSON)
      - submit to submit_url and follow chain/retry logic per spec
      - write job result & status
    """
    job = get_job(job_id)
    if not job:
        return

    set_job_status(job_id, "running")
    payload: Dict[str, Any] = job.get("payload", {})
    try:
        raw_url = payload.get("url")
        url = str(raw_url) if raw_url is not None else None
        if not url:
            raise ValueError("Job payload missing 'url'")

        # --- load page & parse
        html = await load_page_html(url)
        parsed = parse_quiz_page(html, base_url=url)

        # --- fetch resources (pdfs, csvs, etc.)
        resources = await fetch_resources(parsed, base_url=url)  # expected dict {rid: {type,path,url}}
        # resources sample: { "res_2": {"type":"pdf","path":"/tmp/...","url":"..."} }

        # --- extract text/b64 from resources
        extracted_texts: Dict[str, Any] = {}
        for rid, info in (resources or {}).items():
            itype = info.get("type")
            path = info.get("path")
            try:
                if itype == "pdf" and path:
                    txt = extract_pdf_text(path)
                    extracted_texts[rid] = {"type": "pdf", "text": txt}
                elif itype in ("txt", "csv") and path:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        extracted_texts[rid] = {"type": itype, "text": fh.read()}
                elif path:
                    # fallback -> base64 encode raw bytes
                    with open(path, "rb") as fh:
                        b64 = base64.b64encode(fh.read()).decode()
                    extracted_texts[rid] = {"type": itype or "bin", "b64": b64}
                else:
                    extracted_texts[rid] = {"type": itype or "unknown", "url": info.get("url")}
            except Exception as e:
                extracted_texts[rid] = {"type": itype or "error", "error": str(e)}

        # --- build the main LLM prompt (delegate full solving to LLM)
        question_text = parsed.get("question_text", "")
        submit_url = parsed.get("submit_url")

        prompt = f"""
You are an expert data analysis assistant. You will be given a quiz question and extracted resource contents (PDF/CSV/text).
Task: Understand the question, perform any necessary data processing, and RETURN ONLY a single JSON object appropriate for submission.

Important:
- Use only the provided extracted resources (do NOT invent data).
- If the answer is numeric, compute carefully and double-check your math.
- Match the JSON structure required by the quiz instructions if shown on the page.
- Output must be valid JSON (e.g. {{ "answer": ... }} or other JSON where the main answer appears under the 'answer' field if the quiz's JSON shows that).
- Do not include any extra text.

QUESTION:
{question_text}

RESOURCES (truncated):
{_truncate_for_prompt(extracted_texts, max_chars=15000)}

Return only valid JSON now.
"""

        # --- call LLM to get initial answer
        try:
            answer_payload = await _call_llm(prompt)
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")

        # --- Normalise answer_payload to dict
        if not isinstance(answer_payload, dict):
            answer_payload = {"answer": answer_payload}

        # --- Submission & chain/retry loop per spec
        submission_response: Optional[Any] = None
        submit_attempts = 0
        MAX_ITER = 10
        MAX_DURATION = 3 * 60  # 3 minutes
        first_post_time: Optional[float] = None

        def make_submission_body(email: str, secret: str, source_url: str, answer_val: Any):
            return {
                "email": email,
                "secret": secret,
                "url": source_url,
                "answer": answer_val,
            }

        def payload_size_ok(obj: Dict[str, Any]) -> bool:
            s = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            return len(s) < 1_000_000

        current_submit_url = submit_url
        current_answer = answer_payload.get("answer")
        current_source_url = url  # the url field we include in the submission body

        while True:
            # stop if no submit target
            if not current_submit_url:
                submission_response = "no submit_url provided; no submission attempted"
                break

            # time gating
            if first_post_time is None:
                first_post_time = time.time()
            if time.time() - first_post_time > MAX_DURATION:
                submission_response = f"stopped: exceeded {MAX_DURATION} seconds window"
                break

            submit_attempts += 1
            if submit_attempts > MAX_ITER:
                submission_response = f"stopped: reached {MAX_ITER} attempts"
                break

            body = make_submission_body(payload.get("email"), payload.get("secret"), current_source_url, current_answer)
            if not payload_size_ok(body):
                submission_response = "submission payload too large (>1MB)"
                break

            try:
                async with httpx.AsyncClient(timeout=40) as client:
                    resp = await client.post(current_submit_url, json=body)
            except Exception as e:
                submission_response = f"submission error: {e}"
                break

            # attempt to decode JSON response from grader
            try:
                resp_json = resp.json()
            except Exception:
                # non-JSON response -> record text and stop
                submission_response = resp.text
                break

            submission_response = resp_json  # store last grader response

            # if correct: proceed to next URL if provided (or finish)
            if resp_json.get("correct") is True:
                next_url = resp_json.get("url")
                if next_url:
                    # follow the new URL and solve it (re-run pipeline for that URL)
                    # update current_source_url and current_submit_url accordingly
                    current_source_url = next_url
                    html = await load_page_html(current_source_url)
                    parsed = parse_quiz_page(html, base_url=current_source_url)
                    resources = await fetch_resources(parsed, base_url=current_source_url)
                    # extract resource texts for new page
                    extracted_texts = {}
                    for rid, info in (resources or {}).items():
                        itype = info.get("type")
                        path = info.get("path")
                        try:
                            if itype == "pdf" and path:
                                extracted_texts[rid] = {"type": "pdf", "text": extract_pdf_text(path)}
                            elif itype in ("txt", "csv") and path:
                                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                                    extracted_texts[rid] = {"type": itype, "text": fh.read()}
                            elif path:
                                with open(path, "rb") as fh:
                                    extracted_texts[rid] = {"type": itype or "bin", "b64": base64.b64encode(fh.read()).decode()}
                            else:
                                extracted_texts[rid] = {"type": itype or "unknown", "url": info.get("url")}
                        except Exception as e:
                            extracted_texts[rid] = {"type": itype or "error", "error": str(e)}
                    # ask LLM again for new page
                    question_text = parsed.get("question_text", "")
                    prompt_follow = f"""
You are an expert data analysis assistant. This is a FOLLOW-UP quiz at {current_source_url}.
Use the question and the extracted resources below and return only a JSON object appropriate for submission.

QUESTION:
{question_text}

RESOURCES:
{_truncate_for_prompt(extracted_texts, max_chars=15000)}
"""
                    try:
                        new_answer_payload = await _call_llm(prompt_follow)
                    except Exception as e:
                        submission_response = f"LLM failed on follow-up: {e}"
                        break
                    current_answer = (new_answer_payload or {}).get("answer")
                    # update submit target (the new page will include its submit_url)
                    current_submit_url = parsed.get("submit_url")
                    # loop back to submit the new answer
                    continue
                else:
                    # correct and no new url → done
                    break

            # incorrect -> the grader allowed us to resubmit (within 3 minutes)
            if resp_json.get("correct") is False:
                reason = resp_json.get("reason")
                # ask LLM to refine using reason + extracted_texts
                feedback_prompt = f"""
The grader rejected the previous answer for this quiz (reason: {reason}).
Please re-compute the correct answer for the following quiz and return only JSON appropriate for submission.

QUESTION:
{parsed.get('question_text')}

RESOURCES:
{_truncate_for_prompt(extracted_texts, max_chars=15000)}

Return only JSON.
"""
                try:
                    new_answer_payload = await _call_llm(feedback_prompt)
                except Exception as e:
                    submission_response = f"LLM failed during refinement: {e}"
                    break
                current_answer = (new_answer_payload or {}).get("answer")
                # re-submit to same current_submit_url
                continue

            # otherwise, grader response not explicit -> stop
            break

        # --- finalize result and save
        result = {
            "success": True,
            "parsed": parsed,
            "resources": resources,
            "extracted_texts": extracted_texts,
            "answer_payload": {"answer": current_answer},
            "submission_response": submission_response,
            "html_preview": html[:500],
            "finished_at": time.time(),
        }

        set_job_result(job_id, result)
        set_job_status(job_id, "done")
        return

    except Exception as e:
        # catch-all failure
        result = {
            "success": False,
            "error": str(e),
            "finished_at": time.time(),
        }
        set_job_result(job_id, result)
        set_job_status(job_id, "failed")
        return



