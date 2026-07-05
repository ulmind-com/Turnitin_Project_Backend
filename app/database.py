from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from app.config import settings
from app.models.user import User
from app.models.plan import Plan
from app.models.payment import Payment
from app.models.document import ScanDocument
from app.models.repository import SubmittedPaper


async def init_db():
    """Initialize the MongoDB connection and Beanie ODM."""
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client["scanvault"]

    await init_beanie(
        database=db,
        document_models=[User, Plan, Payment, ScanDocument, SubmittedPaper],
    )

    return client
