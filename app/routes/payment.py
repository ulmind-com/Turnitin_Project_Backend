from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from app.models.user import User
from app.models.plan import Plan
from app.models.payment import Payment, PaymentStatus
from app.schemas.payment import PaymentResponse, PaymentListResponse
from app.services.cloudinary_service import upload_image
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/api/payments", tags=["Payments"])


@router.post("/submit", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def submit_payment(
    plan_id: str = Form(...),
    transaction_id: str = Form(...),
    screenshot: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Submit payment proof for a plan subscription.
    Requires: plan_id, transaction_id, and a screenshot image upload.
    """
    # Validate plan exists
    plan = await Plan.get(plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found or inactive",
        )

    # Check for existing pending payment
    existing_pending = await Payment.find_one(
        Payment.user_id == str(current_user.id),
        Payment.status == PaymentStatus.PENDING,
    )
    if existing_pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a pending payment under review",
        )

    # Validate file type
    allowed_types = ["image/jpeg", "image/png", "image/webp", "image/jpg"]
    if screenshot.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Screenshot must be a JPEG, PNG, or WebP image",
        )

    # Upload screenshot to Cloudinary
    file_bytes = await screenshot.read()
    screenshot_url = await upload_image(file_bytes, folder="payment_proofs")

    if not screenshot_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload screenshot",
        )

    # Create payment record
    payment = Payment(
        user_id=str(current_user.id),
        plan_id=str(plan.id),
        transaction_id=transaction_id,
        screenshot_url=screenshot_url,
        status=PaymentStatus.PENDING,
    )
    await payment.insert()

    return PaymentResponse(
        id=str(payment.id),
        user_id=str(payment.user_id),
        plan_id=str(payment.plan_id),
        plan_name=plan.name,
        transaction_id=payment.transaction_id,
        screenshot_url=payment.screenshot_url,
        status=payment.status,
        admin_note=payment.admin_note,
        reviewed_at=payment.reviewed_at.isoformat() if payment.reviewed_at else None,
        created_at=payment.created_at.isoformat(),
    )


@router.get("/my", response_model=PaymentListResponse)
async def get_my_payments(current_user: User = Depends(get_current_user)):
    """Get the current user's payment history."""
    payments = await Payment.find(
        Payment.user_id == str(current_user.id),
    ).sort("-created_at").to_list()

    payment_responses = []
    for payment in payments:
        # Get plan name
        plan = await Plan.get(payment.plan_id)
        plan_name = plan.name if plan else "Unknown Plan"

        payment_responses.append(
            PaymentResponse(
                id=str(payment.id),
                user_id=str(payment.user_id),
                plan_id=str(payment.plan_id),
                plan_name=plan_name,
                transaction_id=payment.transaction_id,
                screenshot_url=payment.screenshot_url,
                status=payment.status,
                admin_note=payment.admin_note,
                reviewed_at=payment.reviewed_at.isoformat() if payment.reviewed_at else None,
                created_at=payment.created_at.isoformat(),
            )
        )

    return PaymentListResponse(
        payments=payment_responses,
        total=len(payment_responses),
    )


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get a specific payment's details."""
    payment = await Payment.get(payment_id)
    if not payment or payment.user_id != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )

    plan = await Plan.get(payment.plan_id)
    plan_name = plan.name if plan else "Unknown Plan"

    return PaymentResponse(
        id=str(payment.id),
        user_id=str(payment.user_id),
        plan_id=str(payment.plan_id),
        plan_name=plan_name,
        transaction_id=payment.transaction_id,
        screenshot_url=payment.screenshot_url,
        status=payment.status,
        admin_note=payment.admin_note,
        reviewed_at=payment.reviewed_at.isoformat() if payment.reviewed_at else None,
        created_at=payment.created_at.isoformat(),
    )
