# app/llm/adapter.py
import os
import httpx
import asyncio
from typing import Dict, Any

AIPIPE_KEY = os.getenv("AIPIPE_API_KEY") or os.getenv("OPENAI_API_KEY")
AIPIPE_URL = "https://api.openai.com/v1/chat/completions"  # or AIPipe endpoint

ALLOWED_ACTIONS = {"download_and_sum", "scrape_text", "submit_json", "visualize"}

async def ask_planner(prompt: str, model: str="gpt-5-nano") -> Dict[str,Any]:
    headers = {"Authorization": f"Bearer {AIPIPE_KEY}"}
    body = {
      "model": model,
      "messages": [
         {"role":"system", "content":"You are a structured planner. Return only JSON in the schema described."},
         {"role":"user", "content": prompt}
      ],
      "temperature": 0.0,
      "max_tokens": 300
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(AIPIPE_URL, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    # extract assistant text (OpenAI format)
    txt = data["choices"][0]["message"]["content"].strip()
    # Try load JSON (be forgiving)
    import json
    try:
        plan = json.loads(txt)
    except Exception:
        # fallback: try to find JSON substring
        import re
        m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
        plan = json.loads(m.group(0)) if m else {}
    # validate
    if not isinstance(plan, dict):
        raise ValueError("Invalid plan from LLM")
    return plan
