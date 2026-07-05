from beanie import Document
from pydantic import Field
from datetime import datetime, timezone


class SubmittedPaper(Document):
    document_id: str
    user_id: str
    extracted_text: str
    ngram_hashes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "submitted_papers"
        indexes = [
            "document_id",
            "user_id",
            # Index on ngram_hashes array for high-performance overlapping lookup
            "ngram_hashes",
        ]
