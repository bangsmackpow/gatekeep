import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from authlib.integrations.starlette_client import OAuth
from src.config import settings
from src.models.database import async_session_factory
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter()

oauth = OAuth()

if settings.ENTRA_CLIENT_ID:
    oauth.register(
        name="entra",
        client_id=settings.ENTRA_CLIENT_ID,
        client_secret=settings.ENTRA_CLIENT_SECRET,
        server_metadata_url=f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/v2.0/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
        redirect_uri=settings.ENTRA_REDIRECT_URI,
    )


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict


class UserInfo(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str


@router.get("/login")
async def login(request: Request):
    if not settings.ENTRA_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Entra ID not configured. Set ENTRA_CLIENT_ID.")
    redirect_uri = settings.ENTRA_REDIRECT_URI
    return await oauth.entra.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def auth_callback(request: Request):
    token = await oauth.entra.authorize_access_token(request)
    user_info = token.get("userinfo", {})

    email = user_info.get("email", "")
    display_name = user_info.get("name", email)
    entra_oid = user_info.get("sub", "")

    if not email:
        raise HTTPException(status_code=400, detail="Email not provided by Entra ID")

    user_id = await _sync_user(entra_oid, email, display_name)

    from jose import jwt
    access_token = jwt.encode(
        {
            "sub": str(user_id),
            "email": email,
            "exp": datetime.now(timezone.utc) + timedelta(seconds=settings.JWT_EXPIRATION),
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )

    response = RedirectResponse(url="/")
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=settings.JWT_EXPIRATION,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/")
    response.delete_cookie(key="access_token")
    return response


@router.get("/me", response_model=UserInfo)
async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from jose import jwt, JWTError
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        email = payload.get("email")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT id, email, display_name, role FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        return UserInfo(
            id=row[0],
            email=row[1],
            display_name=row[2],
            role=row[3],
        )


async def _sync_user(entra_oid: str, email: str, display_name: str) -> uuid.UUID:
    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT id FROM users WHERE entra_oid = :oid"),
            {"oid": entra_oid},
        )
        existing = result.fetchone()

        if existing:
            await session.execute(
                text("UPDATE users SET email = :email, display_name = :name, last_login = NOW() WHERE entra_oid = :oid"),
                {"email": email, "name": display_name, "oid": entra_oid},
            )
            await session.commit()
            return existing[0]

        user_id = uuid.uuid4()
        await session.execute(
            text("""
                INSERT INTO users (id, entra_oid, email, display_name, role)
                VALUES (:id, :oid, :email, :name, 'paralegal')
            """),
            {"id": user_id, "oid": entra_oid, "email": email, "name": display_name},
        )
        await session.commit()
        return user_id
