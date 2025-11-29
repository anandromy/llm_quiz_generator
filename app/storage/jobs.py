import time
from typing import Dict, Any

# Simple in-memory store. Key: job_id (str), Value: dict metadata.
JOB_STORE: Dict[str, Dict[str, Any]] = {}

def create_job(job_id: str, payload: Dict[str, Any]) -> None:
    JOB_STORE[job_id] = {
        "status": "queued",
        "created_at": time.time(),
        "payload": payload,
        "result": None,
        "updated_at": time.time(),
    }

def set_job_status(job_id: str, status: str) -> None:
    if job_id in JOB_STORE:
        JOB_STORE[job_id]["status"] = status
        JOB_STORE[job_id]["updated_at"] = time.time()

def set_job_result(job_id: str, result: Dict[str, Any]) -> None:
    if job_id in JOB_STORE:
        JOB_STORE[job_id]["result"] = result
        JOB_STORE[job_id]["updated_at"] = time.time()

def get_job(job_id: str):
    return JOB_STORE.get(job_id)
