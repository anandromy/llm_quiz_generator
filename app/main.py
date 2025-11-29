from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from app.api.quiz_routes import router as quiz_router

app = FastAPI(title="LLM Quiz Solver")

app.include_router(quiz_router)

@app.get("/health")
async def health():
    return {"status": "ok"}