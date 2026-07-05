from fastapi import APIRouter, Depends, HTTPException, status
from app.models.user import User
from app.models.plan import Plan
from app.models.document import ScanDocument
from app.models.payment import Payment, PaymentStatus
from app.schemas.user import UpdateProfileRequest, DashboardResponse
from app.schemas.auth import UserResponse
from app.utils.dependencies import get_current_user
from datetime import datetime, timezone

router = APIRouter(prefix="/api/user", tags=["User"])


@router.get("/profile", response_model=UserResponse)
async def get_profile(current_user: User = Depends(get_current_user)):
    """Get the current user's profile with plan details."""
    plan_data = None
    if current_user.active_plan:
        plan = await Plan.get(current_user.active_plan)
        if plan:
            plan_data = {
                "id": str(plan.id),
                "name": plan.name,
                "slug": plan.slug,
                "credits": plan.credits,
                "price": plan.price,
            }

    return UserResponse(
        id=str(current_user.id),
        name=current_user.name,
        email=current_user.email,
        role=current_user.role,
        credits=current_user.credits,
        account_status=current_user.account_status,
        active_plan=plan_data,
        plan_expires_at=current_user.plan_expires_at.isoformat() if current_user.plan_expires_at else None,
        created_at=current_user.created_at.isoformat(),
    )


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    data: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
):
    """Update the current user's profile (name, email)."""
    if data.name is not None:
        current_user.name = data.name
    if data.email is not None:
        # Check for duplicate email
        existing = await User.find_one(User.email == data.email)
        if existing and str(existing.id) != str(current_user.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already in use",
            )
        current_user.email = data.email

    current_user.updated_at = datetime.now(timezone.utc)
    await current_user.save()

    plan_data = None
    if current_user.active_plan:
        plan = await Plan.get(current_user.active_plan)
        if plan:
            plan_data = {
                "id": str(plan.id),
                "name": plan.name,
                "slug": plan.slug,
                "credits": plan.credits,
                "price": plan.price,
            }

    return UserResponse(
        id=str(current_user.id),
        name=current_user.name,
        email=current_user.email,
        role=current_user.role,
        credits=current_user.credits,
        account_status=current_user.account_status,
        active_plan=plan_data,
        plan_expires_at=current_user.plan_expires_at.isoformat() if current_user.plan_expires_at else None,
        created_at=current_user.created_at.isoformat(),
    )


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(current_user: User = Depends(get_current_user)):
    """Get dashboard stats: credits, scans, plan status, pending payments."""
    user_id = str(current_user.id)

    # Count documents
    total_scans = await ScanDocument.find(ScanDocument.user_id == user_id).count()
    # A scan is "completed" if BOTH engines have completed
    completed_scans = await ScanDocument.find(
        ScanDocument.user_id == user_id,
        ScanDocument.ai_scan_status == "completed",
        ScanDocument.plagiarism_scan_status == "completed",
    ).count()

    # Check for pending payment
    pending_payment = await Payment.find_one(
        Payment.user_id == user_id,
        Payment.status == PaymentStatus.PENDING,
    )

    # Get active plan
    plan_data = None
    if current_user.active_plan:
        plan = await Plan.get(current_user.active_plan)
        if plan:
            plan_data = {
                "id": str(plan.id),
                "name": plan.name,
                "slug": plan.slug,
                "credits": plan.credits,
                "price": plan.price,
            }

    # Get recent documents (last 5)
    recent_docs = await ScanDocument.find(
        ScanDocument.user_id == user_id,
    ).sort("-created_at").limit(5).to_list()

    recent_documents = []
    for doc in recent_docs:
        # Derive a combined scan_status for the frontend
        if doc.ai_scan_status == "completed" and doc.plagiarism_scan_status == "completed":
            scan_status = "completed"
        elif doc.ai_scan_status == "failed" or doc.plagiarism_scan_status == "failed":
            scan_status = "failed"
        elif doc.ai_scan_status or doc.plagiarism_scan_status:
            scan_status = "processing"
        else:
            scan_status = "pending"

        recent_documents.append({
            "id": str(doc.id),
            "original_file_name": doc.original_file_name,
            "scan_status": scan_status,
            "plagiarism_score": doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0,
            "ai_score": doc.ai_result.ai_score if doc.ai_result else 0,
            "created_at": doc.created_at.isoformat(),
        })

    return DashboardResponse(
        credits=current_user.credits,
        total_scans=total_scans,
        completed_scans=completed_scans,
        active_plan=plan_data,
        account_status=current_user.account_status,
        pending_payment=pending_payment is not None,
        recent_documents=recent_documents,
    )
