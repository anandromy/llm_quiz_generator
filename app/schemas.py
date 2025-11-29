from pydantic import BaseModel, HttpUrl, Field

class QuizRequest(BaseModel):
    email: str = Field(..., description="Student email address")
    secret: str = Field(..., min_length=1, description="Secret provided in the Google Form")
    url: HttpUrl = Field(..., description="URL of the quiz page to solve")
