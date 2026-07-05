from fastapi import APIRouter, Depends, HTTPException, status
from app.models.user import User, AccountStatus
from app.models.plan import Plan
from app.models.payment import Payment, PaymentStatus
from app.models.document import ScanDocument
from app.schemas.admin import (
    RejectPaymentRequest,
    EditCreditsRequest,
    AssignPlanRequest,
    SuspendUserRequest,
    AdminUserResponse,
    AdminUserListResponse,
    AdminDashboardResponse,
    AdminPaymentResponse,
    AdminPaymentListResponse,
)
from app.utils.dependencies import require_admin
from datetime import datetime, timezone

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ─── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=AdminDashboardResponse)
async def admin_dashboard(admin: User = Depends(require_admin)):
    """Get platform-wide analytics for the admin dashboard."""
    total_users = await User.find(User.role == "user").count()
    total_scans = await ScanDocument.find().count()
    completed_scans = await ScanDocument.find(
        ScanDocument.scan_status == "completed"
    ).count()
    pending_payments = await Payment.find(
        Payment.status == PaymentStatus.PENDING
    ).count()

    # Calculate total credits distributed from approved payments
    approved_payments = await Payment.find(
        Payment.status == PaymentStatus.APPROVED
    ).to_list()

    total_credits = 0
    for payment in approved_payments:
        plan = await Plan.get(payment.plan_id)
        if plan:
            total_credits += plan.credits

    # Plans breakdown
    plans = await Plan.find().to_list()
    plans_breakdown = []
    for plan in plans:
        count = await Payment.find(
            Payment.plan_id == str(plan.id),
            Payment.status == PaymentStatus.APPROVED,
        ).count()
        plans_breakdown.append({
            "name": plan.name,
            "slug": plan.slug,
            "credits": plan.credits,
            "price": plan.price,
            "total_subscriptions": count,
        })

    return AdminDashboardResponse(
        total_users=total_users,
        total_scans=total_scans,
        completed_scans=completed_scans,
        pending_payments=pending_payments,
        total_credits_distributed=total_credits,
        plans_breakdown=plans_breakdown,
    )


# ─── Payment Management ──────────────────────────────────────────────────────

@router.get("/payments/pending", response_model=AdminPaymentListResponse)
async def get_pending_payments(admin: User = Depends(require_admin)):
    """Get all pending payment submissions for the verification queue."""
    payments = await Payment.find(
        Payment.status == PaymentStatus.PENDING
    ).sort("-created_at").to_list()

    return await _build_payment_list(payments)


@router.get("/payments/all", response_model=AdminPaymentListResponse)
async def get_all_payments(
    payment_status: str = None,
    admin: User = Depends(require_admin),
):
    """Get all payments, optionally filtered by status."""
    if payment_status:
        payments = await Payment.find(
            Payment.status == payment_status
        ).sort("-created_at").to_list()
    else:
        payments = await Payment.find().sort("-created_at").to_list()

    return await _build_payment_list(payments)


@router.put("/payments/{payment_id}/approve")
async def approve_payment(
    payment_id: str,
    admin: User = Depends(require_admin),
):
    """
    Approve a pending payment and auto-provision the plan + credits.
    This is the core admin action that activates a user's subscription.
    """
    payment = await Payment.get(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.status != PaymentStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment is already {payment.status}",
        )

    # Get the plan
    plan = await Plan.get(payment.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Associated plan not found")

    # Get the user
    user = await User.get(payment.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Associated user not found")

    # ── Auto-Provision ──
    # 1. Assign plan to user
    user.active_plan = str(plan.id)
    # 2. Add credits (additive — they stack if user has remaining credits)
    user.credits += plan.credits
    # 3. Update account status
    user.account_status = AccountStatus.ACTIVE
    user.updated_at = datetime.now(timezone.utc)
    await user.save()

    # 4. Update payment record
    payment.status = PaymentStatus.APPROVED
    payment.reviewed_by = str(admin.id)
    payment.reviewed_at = datetime.now(timezone.utc)
    await payment.save()

    return {
        "message": f"Payment approved. {plan.credits} credits added to {user.email}.",
        "user_credits": user.credits,
        "plan_name": plan.name,
    }


@router.put("/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: str,
    data: RejectPaymentRequest,
    admin: User = Depends(require_admin),
):
    """Reject a pending payment with an optional admin note."""
    payment = await Payment.get(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.status != PaymentStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment is already {payment.status}",
        )

    payment.status = PaymentStatus.REJECTED
    payment.admin_note = data.admin_note
    payment.reviewed_by = str(admin.id)
    payment.reviewed_at = datetime.now(timezone.utc)
    await payment.save()

    return {"message": "Payment rejected.", "admin_note": data.admin_note}


# ─── User Management ─────────────────────────────────────────────────────────

@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    search: str = None,
    account_status: str = None,
    admin: User = Depends(require_admin),
):
    """List all users with optional search and status filter."""
    query_filters = [User.role == "user"]

    if account_status:
        query_filters.append(User.account_status == account_status)

    users = await User.find(*query_filters).sort("-created_at").to_list()

    # Apply text search if provided (name or email contains search term)
    if search:
        search_lower = search.lower()
        users = [
            u for u in users
            if search_lower in u.name.lower() or search_lower in u.email.lower()
        ]

    user_responses = []
    for user in users:
        plan_data = None
        if user.active_plan:
            plan = await Plan.get(user.active_plan)
            if plan:
                plan_data = {
                    "id": str(plan.id),
                    "name": plan.name,
                    "slug": plan.slug,
                    "credits": plan.credits,
                    "price": plan.price,
                }

        total_scans = await ScanDocument.find(
            ScanDocument.user_id == str(user.id)
        ).count()

        user_responses.append(
            AdminUserResponse(
                id=str(user.id),
                name=user.name,
                email=user.email,
                role=user.role,
                credits=user.credits,
                account_status=user.account_status,
                active_plan=plan_data,
                plan_expires_at=user.plan_expires_at.isoformat() if user.plan_expires_at else None,
                total_scans=total_scans,
                created_at=user.created_at.isoformat(),
            )
        )

    return AdminUserListResponse(users=user_responses, total=len(user_responses))


@router.get("/users/{user_id}", response_model=AdminUserResponse)
async def get_user_details(
    user_id: str,
    admin: User = Depends(require_admin),
):
    """Get detailed info about a specific user."""
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    plan_data = None
    if user.active_plan:
        plan = await Plan.get(user.active_plan)
        if plan:
            plan_data = {
                "id": str(plan.id),
                "name": plan.name,
                "slug": plan.slug,
                "credits": plan.credits,
                "price": plan.price,
            }

    total_scans = await ScanDocument.find(
        ScanDocument.user_id == str(user.id)
    ).count()

    return AdminUserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role,
        credits=user.credits,
        account_status=user.account_status,
        active_plan=plan_data,
        plan_expires_at=user.plan_expires_at.isoformat() if user.plan_expires_at else None,
        total_scans=total_scans,
        created_at=user.created_at.isoformat(),
    )


@router.put("/users/{user_id}/credits")
async def edit_user_credits(
    user_id: str,
    data: EditCreditsRequest,
    admin: User = Depends(require_admin),
):
    """Manually set a user's credit balance."""
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.credits = data.credits
    user.updated_at = datetime.now(timezone.utc)
    await user.save()

    return {"message": f"Credits updated to {data.credits} for {user.email}"}


@router.put("/users/{user_id}/plan")
async def assign_plan(
    user_id: str,
    data: AssignPlanRequest,
    admin: User = Depends(require_admin),
):
    """
    Manually assign a plan to a user (manual override).
    Use for VIP clients, testing, or offline sales.
    """
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    plan = await Plan.get(data.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    user.active_plan = str(plan.id)
    user.credits += plan.credits
    user.account_status = AccountStatus.ACTIVE
    user.updated_at = datetime.now(timezone.utc)
    await user.save()

    return {
        "message": f"{plan.name} assigned to {user.email}. {plan.credits} credits added.",
        "user_credits": user.credits,
    }


@router.put("/users/{user_id}/suspend")
async def suspend_user(
    user_id: str,
    data: SuspendUserRequest,
    admin: User = Depends(require_admin),
):
    """Suspend or unsuspend a user account."""
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot suspend an admin account",
        )

    user.account_status = AccountStatus.SUSPENDED if data.suspended else AccountStatus.ACTIVE
    user.updated_at = datetime.now(timezone.utc)
    await user.save()

    status_text = "suspended" if data.suspended else "unsuspended"
    return {"message": f"User {user.email} has been {status_text}"}


# ─── Document Management ─────────────────────────────────────────────────────

@router.get("/documents")
async def list_all_documents(admin: User = Depends(require_admin)):
    """List all scanned documents across the platform."""
    docs = await ScanDocument.find().sort("-created_at").to_list()

    documents = []
    for doc in docs:
        user = await User.get(doc.user_id)
        documents.append({
            "id": str(doc.id),
            "user_id": doc.user_id,
            "user_email": user.email if user else "Unknown",
            "original_file_name": doc.original_file_name,
            "file_type": doc.file_type,
            "scan_status": doc.scan_status,
            "plagiarism_score": doc.scan_result.plagiarism_score if doc.scan_result else 0,
            "ai_score": doc.scan_result.ai_score if doc.scan_result else 0,
            "scanned_at": doc.scanned_at.isoformat() if doc.scanned_at else None,
            "created_at": doc.created_at.isoformat(),
        })

    return {"documents": documents, "total": len(documents)}


@router.get("/documents/{document_id}")
async def get_document_details(
    document_id: str,
    admin: User = Depends(require_admin),
):
    """View any document's full scan report (admin access)."""
    doc = await ScanDocument.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    user = await User.get(doc.user_id)

    return {
        "id": str(doc.id),
        "user_id": doc.user_id,
        "user_email": user.email if user else "Unknown",
        "original_file_name": doc.original_file_name,
        "file_type": doc.file_type,
        "extracted_text": doc.extracted_text,
        "scan_status": doc.scan_status,
        "scan_result": doc.scan_result.model_dump() if doc.scan_result else None,
        "scanned_at": doc.scanned_at.isoformat() if doc.scanned_at else None,
        "created_at": doc.created_at.isoformat(),
    }


# ─── Helper ───────────────────────────────────────────────────────────────────

async def _build_payment_list(payments: list[Payment]) -> AdminPaymentListResponse:
    """Build an admin payment list response with user and plan details."""
    payment_responses = []
    for payment in payments:
        user = await User.get(payment.user_id)
        plan = await Plan.get(payment.plan_id)

        payment_responses.append(
            AdminPaymentResponse(
                id=str(payment.id),
                user_id=str(payment.user_id),
                user_name=user.name if user else "Unknown",
                user_email=user.email if user else "Unknown",
                plan_id=str(payment.plan_id),
                plan_name=plan.name if plan else "Unknown",
                plan_credits=plan.credits if plan else 0,
                transaction_id=payment.transaction_id,
                screenshot_url=payment.screenshot_url,
                status=payment.status,
                admin_note=payment.admin_note,
                reviewed_by=str(payment.reviewed_by) if payment.reviewed_by else None,
                reviewed_at=payment.reviewed_at.isoformat() if payment.reviewed_at else None,
                created_at=payment.created_at.isoformat(),
            )
        )

    return AdminPaymentListResponse(
        payments=payment_responses,
        total=len(payment_responses),
    )
