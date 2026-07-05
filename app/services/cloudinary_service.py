import cloudinary
import cloudinary.uploader
from app.config import settings


def init_cloudinary():
    """Initialize the Cloudinary SDK with credentials from settings."""
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


async def upload_image(file_bytes: bytes, folder: str = "payment_proofs") -> str:
    """
    Upload an image to Cloudinary and return the secure URL.

    Args:
        file_bytes: Raw bytes of the image file.
        folder: Cloudinary folder to store the file in.

    Returns:
        Secure URL of the uploaded image.
    """
    result = cloudinary.uploader.upload(
        file_bytes,
        folder=folder,
        resource_type="image",
        transformation=[{"quality": "auto", "fetch_format": "auto"}],
    )
    return result.get("secure_url", "")


async def upload_raw_file(file_bytes: bytes, folder: str = "original_documents", public_id: str = None) -> str:
    """
    Upload a raw file (PDF/DOCX) to Cloudinary and return the secure URL.

    Args:
        file_bytes: Raw bytes of the file.
        folder: Cloudinary folder to store the file in.
        public_id: Optional public ID for the file.

    Returns:
        Secure URL of the uploaded file.
    """
    upload_opts = {
        "folder": folder,
        "resource_type": "raw",
    }
    if public_id:
        upload_opts["public_id"] = public_id

    result = cloudinary.uploader.upload(file_bytes, **upload_opts)
    return result.get("secure_url", "")
