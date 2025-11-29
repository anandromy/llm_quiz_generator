from dotenv import load_dotenv
load_dotenv()
import os

from fastapi import FastAPI
from app.api.quiz_routes import router as quiz_router

app = FastAPI(title="LLM Quiz Solver")

app.include_router(quiz_router)

@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")