import os
import uuid
import asyncio
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi import Path
from app.schemas import QuizRequest
from app.storage.jobs import create_job

router = APIRouter()

# Note: Initially, my router read APP_SECRET at import time, before the .env was loaded.
# Because of that, the variable remained None.
# I fixed it by moving the read to request-time using os.getenv('APP_SECRET') inside the route function.
# This ensures the secret is always read from the active environment after load_dotenv() runs.
APP_SECRET = os.environ.get("secret")

@router.post("/quiz-task")
async def quiz_task(payload: QuizRequest):
    """
    Accepts a QuizRequest JSON body. FastAPI + Pydantic handle parsing and validation.
    Returns:
      - 422 automatically if JSON structure is invalid
      - 400 for missing/invalid content we decide to treat specially
      - 403 for invalid secret
      - 200 for accepted jobs
    """
    # At-request-time read of APP_SECRET (ensures .env has been loaded by main)
    expected_secret = os.getenv("APP_SECRET")
    if expected_secret is None:
        raise HTTPException(status_code=500, detail="Server not configured with secret")

    if payload.secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    job_id = str(uuid.uuid4())
    create_job(job_id, payload.dict())
    from app.workers.runner import run_job
    asyncio.get_event_loop().create_task(run_job(job_id))

    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "accepted", "job_id": job_id})

@router.get("/job/{job_id}")
async def get_job(job_id: str = Path(..., description="Job ID returned on accept")):
    from app.storage.jobs import get_job
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    payload = job.get("payload", {}).copy()
    if "secret" in payload:
        payload["secret"] = "REDACTED"
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "payload": payload,
        "result": job.get("result"),
    }