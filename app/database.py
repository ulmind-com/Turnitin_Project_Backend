from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from app.config import settings
from app.models.user import User
from app.models.plan import Plan
from app.models.payment import Payment
from app.models.document import ScanDocument


async def init_db():
    """Initialize the MongoDB connection and Beanie ODM."""
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.get_default_database()

    await init_beanie(
        database=db,
        document_models=[User, Plan, Payment, ScanDocument],
    )

    return client
