"""Separate owner preview and active-viewer delivery. Never disclose storage URLs."""

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.legacy import Legacy
from app.models.visual_companion import VisualCompanion, VisualCompanionVersion
from app.schemas.visual_companion import Activation, Toggle, VersionCreate
from app.services.authorization import legacy_role, require_legacy, require_persona_legacy
from app.services.media_storage import StorageError, get_source_storage
from app.services.visual_companions import VisualCompanionService, manifest, read_scope
from app.services.visual_storage import MAX_ASSET_BYTES, VisualStorage

PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
            except RequestValidationError:
                # Validation details may contain crop/source input, arbitrary
                # extra fields, NaN or decoder context. Never echo private data.
                return JSONResponse(status_code=422, headers=PRIVATE_HEADERS, content={
                    "detail": {"code": "visual_request_invalid", "message": "Invalid Visual Presence request."}})
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), **PRIVATE_HEADERS}
                raise
            except Exception:
                # Database/adapter failures outside the known domain errors
                # must not expose exception text or drop private cache headers.
                return JSONResponse(status_code=503, headers=PRIVATE_HEADERS, content={
                    "detail": {"code": "visual_unavailable", "message": "Visual Presence is temporarily unavailable."}})
            response.headers.update(PRIVATE_HEADERS)
            return response
        return handle


router = APIRouter(prefix="/legacies/{legacy_id}/visual-companion", tags=["Visual Presence"], route_class=PrivateRoute)


def enabled():
    if not get_settings().visual_presence_enabled:
        raise HTTPException(404, detail="Visual Presence is unavailable.")


def preparation_service():
    from app.services.visual_local_provider import configured_provider
    try:
        provider = configured_provider()
        return VisualCompanionService(provider_name=provider.provider_name, model_digest=provider.model_digest)
    except Exception:
        raise HTTPException(503, detail={"code": "visual_provider_unavailable", "message": "Visual preparation is not available."}) from None


def profile_json(profile):
    if profile is None:
        return {"revision": 0, "enabled": False, "current_version_id": None, "desired_version_id": None, "deleted": False}
    return {"id": profile.id, "revision": profile.revision, "enabled": profile.enabled,
        "current_version_id": profile.current_version_id, "desired_version_id": profile.desired_version_id,
        "deleted": profile.deleted_at is not None}


def version_json(version):
    return {"id": version.id, "version_number": version.version_number, "state": version.state,
        "source_id": version.source_id, "crop": version.crop_json, "recipe": version.recipe_version,
        "failure_code": version.failure_code, "bundle_digest": version.bundle_digest,
        "confirmed_at": version.confirmed_at, "approved_at": version.approved_at}


@router.get("/capabilities")
def capabilities(legacy_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    db.expire_all()
    legacy = db.get(Legacy, legacy_id)
    role = legacy_role(db, user.id, legacy) if legacy else None
    if role is None:
        require_persona_legacy(db, user.id, legacy_id)
    can_prepare = False
    if role == 'owner' and get_settings().visual_presence_enabled and legacy.setup_status == 'active':
        try:
            preparation_service()
            can_prepare = True
        except HTTPException:
            pass
    return {"enabled": get_settings().visual_presence_enabled and legacy.setup_status in {"active", "collecting_identity"},
        "owner_managed": True, "can_manage": role == "owner", "can_prepare": can_prepare,
        "recipe": "portrait_2d_v1", "max_photo_bytes": 20 * 1024 * 1024,
        "max_pixels": 24_000_000, "min_crop_edge": 128, "confirmation_copy_version": "l19-likeness-v1"}


@router.get("", dependencies=[Depends(enabled)])
def get_profile(legacy_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    db.expire_all()
    require_legacy(db, user.id, legacy_id, owner_only=True)
    return profile_json(db.scalar(select(VisualCompanion).where(VisualCompanion.legacy_id == legacy_id)))


@router.post("/versions", status_code=202, dependencies=[Depends(enabled)])
def create_version(legacy_id: int, payload: VersionCreate, user=Depends(get_current_user), db=Depends(get_db), service=Depends(preparation_service)):
    if not get_settings().visual_preparation_enabled:
        raise HTTPException(503, detail="Visual preparation is unavailable.")
    version = service.admit(db, user.id, legacy_id, payload)
    db.commit()
    return version_json(version)


@router.get("/versions/{version_id}", dependencies=[Depends(enabled)])
def get_version(legacy_id: int, version_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    db.expire_all()
    require_legacy(db, user.id, legacy_id, owner_only=True)
    version = db.scalar(select(VisualCompanionVersion).where(VisualCompanionVersion.legacy_id == legacy_id, VisualCompanionVersion.id == version_id))
    if version is None:
        raise HTTPException(404, detail="Visual version not found.")
    return version_json(version)


@router.get("/versions/{version_id}/manifest", dependencies=[Depends(enabled)])
def preview_manifest(legacy_id: int, version_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    return manifest(db, user.id, legacy_id, version_id=version_id)


@router.get("/active-manifest", dependencies=[Depends(enabled)])
def active_manifest(legacy_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    return manifest(db, user.id, legacy_id, viewer=True)


def content(db, user_id, legacy_id, asset_id, *, viewer=False, version_id=None):
    _, version, assets = read_scope(db, user_id, legacy_id, viewer=viewer, version_id=version_id)
    asset = next((a for a in assets if a.id == asset_id), None)
    if asset is None:
        raise HTTPException(404, detail="Visual asset not found.")
    expected_version = version.id

    def identity(item):
        return (item.object_key, item.object_version, item.byte_size, item.sha256,
                item.mime_type, item.storage_backend, item.encryption_key_id, item.logical_role, item.storage_bucket)

    expected_asset = identity(asset)
    key, object_version, size, checksum, mime, backend, key_id, role, bucket = expected_asset
    allowed_mimes = {"application/json"} if role == "rig" else {"image/png", "image/jpeg", "image/webp"}
    if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES or mime not in allowed_mimes:
        raise HTTPException(404, detail="Visual asset unavailable.")
    # End read transaction before I/O; reauthorize after loading bounded bytes.
    # No SQL lock spans an object store operation.
    db.rollback()
    try:
        storage = VisualStorage(get_source_storage())
        if (storage.backend_name != backend or storage.encryption_key_id != key_id
                or (backend == 's3' and (not bucket or storage.bucket_name != bucket))):
            raise HTTPException(503, detail="Visual storage unavailable.")
        data = storage.read(key, version=object_version)
    except (StorageError, OSError):
        raise HTTPException(503, detail="Visual storage unavailable.") from None
    if len(data) != size or hashlib.sha256(data).hexdigest() != checksum:
        raise HTTPException(503, detail="Visual asset unavailable.")
    _, fresh, fresh_assets = read_scope(db, user_id, legacy_id, viewer=viewer, version_id=version_id)
    fresh_asset = next((a for a in fresh_assets if a.id == asset_id), None)
    if fresh.id != expected_version or fresh_asset is None or identity(fresh_asset) != expected_asset:
        raise HTTPException(404, detail="Visual asset unavailable.")
    return Response(data, media_type=mime, headers={**PRIVATE_HEADERS, "Content-Disposition": "inline; filename=visual-asset"})


@router.get("/versions/{version_id}/assets/{asset_id}/content", dependencies=[Depends(enabled)])
def preview_content(legacy_id: int, version_id: str, asset_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    return content(db, user.id, legacy_id, asset_id, version_id=version_id)


@router.get("/active/assets/{asset_id}/content", dependencies=[Depends(enabled)])
def active_content(legacy_id: int, asset_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    return content(db, user.id, legacy_id, asset_id, viewer=True)


@router.post("/activate", dependencies=[Depends(enabled)])
def activate(legacy_id: int, payload: Activation, user=Depends(get_current_user), db=Depends(get_db)):
    profile = VisualCompanionService().activate(db, user.id, legacy_id, payload)
    db.commit()
    return profile_json(profile)


@router.patch("", dependencies=[Depends(enabled)])
def toggle(legacy_id: int, payload: Toggle, user=Depends(get_current_user), db=Depends(get_db)):
    profile = VisualCompanionService().toggle(db, user.id, legacy_id, payload.enabled, payload.expected_revision)
    db.commit()
    return profile_json(profile)


# Erasure remains available with preparation/presentation disabled.
@router.delete("", status_code=202)
def delete(legacy_id: int, expected_revision: int = Query(ge=1), user=Depends(get_current_user), db=Depends(get_db)):
    profile = VisualCompanionService().delete(db, user.id, legacy_id, expected_revision)
    db.commit()
    return profile_json(profile)
