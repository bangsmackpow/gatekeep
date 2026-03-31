import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from src.models.database import async_session_factory
from src.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

AUDITABLE_PATHS = {
    "/api/upload": "upload",
    "/api/documents": "view",
    "/api/search": "search",
    "/api/export": "export",
    "/api/documents/delete": "delete",
}


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        method = request.method

        action = self._determine_action(path, method)

        response = await call_next(request)

        if action:
            await self._log_audit(request, action, response.status_code)

        return response

    def _determine_action(self, path: str, method: str) -> str | None:
        for prefix, action in AUDITABLE_PATHS.items():
            if path.startswith(prefix):
                if method == "GET" and prefix == "/api/documents":
                    return "view"
                if method == "POST" and prefix == "/api/upload":
                    return "upload"
                if prefix == "/api/search":
                    return "search"
                return action
        return None

    async def _log_audit(self, request: Request, action: str, status_code: int):
        try:
            user_id = getattr(request.state, "user_id", None)
            user_email = getattr(request.state, "user_email", None)

            async with async_session_factory() as session:
                log_entry = AuditLog(
                    event_id=uuid.uuid4(),
                    action=action,
                    resource_type="http_request",
                    resource_id=None,
                    user_id=user_id,
                    user_email=user_email,
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    details={
                        "method": request.method,
                        "path": request.url.path,
                        "query": str(request.query_params) if request.query_params else None,
                        "status_code": status_code,
                    },
                )
                session.add(log_entry)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
