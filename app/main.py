from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import init_db
from app.seed import run_seed
from app.services.cloudinary_service import init_cloudinary

# Import route modules
from app.routes.auth import router as auth_router
from app.routes.user import router as user_router
from app.routes.payment import router as payment_router
from app.routes.document import router as document_router
from app.routes.admin import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    # ── Startup ──
    print("🚀 Starting Turnitin Clone API...")

    # Initialize database
    client = await init_db()
    print("✅ MongoDB connected")

    # Initialize Cloudinary
    init_cloudinary()
    print("✅ Cloudinary configured")

    # Seed database
    await run_seed()

    print(f"✅ Server running on port {settings.PORT}")
    print(f"📖 API Docs: http://localhost:{settings.PORT}/docs")

    yield

    # ── Shutdown ──
    client.close()
    print("👋 Server shutdown complete")



app = FastAPI(
    title="Turnitin Clone API",
    description="Plagiarism Detection & AI Writing Analysis Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS Middleware (fully open) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ── Register Routers ──
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(payment_router)
app.include_router(document_router)
app.include_router(admin_router)


# ── Health Check ──
@app.get("/api/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": "Turnitin Clone API",
        "version": "1.0.0",
    }


# ── Plans (Public) ──
from app.models.plan import Plan


@app.get("/api/plans", tags=["Plans"])
async def list_plans():
    """List all active subscription plans."""
    plans = await Plan.find(Plan.is_active == True).to_list()
    return {
        "plans": [
            {
                "id": str(plan.id),
                "name": plan.name,
                "slug": plan.slug,
                "credits": plan.credits,
                "price": plan.price,
                "description": plan.description,
            }
            for plan in plans
        ]
    }


@app.get("/api/plans/{plan_id}", tags=["Plans"])
async def get_plan(plan_id: str):
    """Get details of a specific plan."""
    plan = await Plan.get(plan_id)
    if not plan:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Plan not found")
    return {
        "id": str(plan.id),
        "name": plan.name,
        "slug": plan.slug,
        "credits": plan.credits,
        "price": plan.price,
        "description": plan.description,
    }
