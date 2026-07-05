from app.models.plan import Plan
from app.models.user import User, Role
from app.utils.security import hash_password
from app.config import settings


async def seed_plans():
    """Seed the default subscription plans if they don't exist."""
    default_plans = [
        {
            "name": "Basic Plan",
            "slug": "basic",
            "credits": 10,
            "price": 299,
            "description": "Perfect for students. Scan up to 10 documents.",
        },
        {
            "name": "Premium Plan",
            "slug": "premium",
            "credits": 25,
            "price": 599,
            "description": "For professionals. Scan up to 25 documents with priority processing.",
        },
        {
            "name": "Max Plan",
            "slug": "max",
            "credits": 50,
            "price": 999,
            "description": "Enterprise grade. Scan up to 50 documents with full analytics.",
        },
    ]

    for plan_data in default_plans:
        existing = await Plan.find_one(Plan.slug == plan_data["slug"])
        if not existing:
            plan = Plan(**plan_data)
            await plan.insert()
            print(f"  ✓ Seeded plan: {plan.name}")
        else:
            print(f"  · Plan already exists: {plan_data['name']}")


async def seed_admin():
    """Seed the default admin user if it doesn't exist."""
    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        print("  ⚠ ADMIN_EMAIL or ADMIN_PASSWORD not set in .env — skipping admin seed")
        return

    existing = await User.find_one(User.email == settings.ADMIN_EMAIL)
    if not existing:
        admin = User(
            name="Admin",
            email=settings.ADMIN_EMAIL,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            role=Role.ADMIN,
            credits=999,
            account_status="active",
        )
        await admin.insert()
        print(f"  ✓ Seeded admin: {settings.ADMIN_EMAIL}")
    else:
        print(f"  · Admin already exists: {settings.ADMIN_EMAIL}")


async def run_seed():
    """Run all seed operations."""
    print("\n🌱 Seeding database...")
    await seed_plans()
    await seed_admin()
    print("🌱 Seeding complete.\n")
