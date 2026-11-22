"""Media serving and photo upload endpoints.

An article carries at most one photo — an identification aid ("which swab?"), not an
archive. Files live under `media_root/photos/` and are served by the same path-safe
route as any other media, so nothing here needs to know about the inventory schema.
"""
from __future__ import annotations

import mimetypes
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.dependencies import get_client_ip, check_read_auth
from app.utils import safe_path

router = APIRouter()

PHOTO_DIR = "photos"


@router.get("/media/{path:path}")
async def serve_media(
    path: str,
    client_ip: str | None = Depends(get_client_ip),
    _auth: bool = Depends(check_read_auth),
):
    """Serve a media file (article photos) from under media_root."""
    from app.routes.metrics import log_request_async

    await log_request_async("/media", client_ip)

    settings = get_settings()

    if not path:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        target = safe_path(settings.media_root, path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ctype, _ = mimetypes.guess_type(str(target))
    return FileResponse(path=target, media_type=ctype or "application/octet-stream")


@router.post("/api/upload-photo")
async def upload_photo(
    file: UploadFile = File(...),
    client_ip: str | None = Depends(get_client_ip),
):
    """Store an article photo and return its relative path (to save in `inventory.photo`)."""
    from app.routes.metrics import log_request_async

    await log_request_async("/api/upload-photo", client_ip)

    settings = get_settings()

    if not file.filename:
        return {"success": False, "error": "Nom de fichier manquant"}

    ext = Path(file.filename).suffix.lower()
    if ext not in settings.allowed_photo_ext:
        allowed = ", ".join(sorted(settings.allowed_photo_ext))
        return {"success": False, "error": f"Extension non autorisée : {ext} (attendu : {allowed})"}

    contents = await file.read()
    if len(contents) > settings.max_photo_size:
        max_mb = settings.max_photo_size // (1024 * 1024)
        return {"success": False, "error": f"Fichier trop volumineux (max {max_mb} Mo)"}

    folder = settings.media_root / PHOTO_DIR
    folder.mkdir(parents=True, exist_ok=True)

    # Timestamp prefix: keeps names unique without ever overwriting a photo still
    # referenced by another article.
    stem = re.sub(r"[^\w\-]", "_", Path(file.filename).stem)[:40] or "photo"
    final_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{stem}{ext}"
    (folder / final_name).write_bytes(contents)

    rel_path = f"{PHOTO_DIR}/{final_name}"
    print(f"Photo: {rel_path} ({len(contents) // 1024} Ko)")
    return {"success": True, "path": rel_path}
