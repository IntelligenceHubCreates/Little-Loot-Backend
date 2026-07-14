import logging
from app.settings import settings
import cloudinary
import cloudinary.uploader
import cloudinary.api

logger = logging.getLogger(__name__)

cloudinary.config(
    cloud_name=settings.cloudinary_cloud_name,
    api_key=settings.cloudinary_api_key,
    api_secret=settings.cloudinary_api_secret,
)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


async def upload_images(file_paths):
    uploaded_images = []
    for file_path in file_paths:
        try:
            file_content = await file_path.read()

            # SECURITY: Validate content-type before uploading.
            # Extension alone can be spoofed (e.g. malware.jpg).
            ct = getattr(file_path, "content_type", "") or ""
            if ct and ct not in ALLOWED_IMAGE_TYPES:
                logger.warning("Rejected upload with content-type: %s", ct)
                uploaded_images.append({"url": "", "public_id": "rejected_type"})
                continue

            if len(file_content) > MAX_IMAGE_SIZE_BYTES:
                logger.warning("Rejected oversized upload: %d bytes", len(file_content))
                uploaded_images.append({"url": "", "public_id": "rejected_size"})
                continue

            response = cloudinary.uploader.upload(
                file_content,
                folder="littleloot/products",
                resource_type="image",
                allowed_formats=["jpg", "jpeg", "png", "webp", "gif"],
            )
            uploaded_images.append({
                "url":       response["secure_url"],
                "public_id": response["public_id"],
            })
            logger.info("Image uploaded: %s", response["public_id"])
        except Exception:
            logger.error("Image upload failed", exc_info=True)
            # SECURITY: do not include exception text (internal Cloudinary errors)
            # in the uploaded_images list — it would be returned to the client.
            uploaded_images.append({"url": "", "public_id": "upload_failed"})
    return uploaded_images
