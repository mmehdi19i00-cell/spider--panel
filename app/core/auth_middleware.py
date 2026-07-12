"""Auth middleware: session-based authentication with HttpOnly cookies."""
from __future__ import annotations

from typing import Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app.core.security import decode_access_token, get_current_admin
from app.database import get_sessionmaker


# Routes that don't require authentication
PUBLIC_PATHS = {
    "/login",
    "/api/auth/token",
    "/api/auth/login",
    "/api/healthz",
    "/sub",
    "/static",
    "/assets",
    "/musics",
    "/favicon.ico",
}


def is_public_path(path: str) -> bool:
    """Check if a path should be accessible without authentication."""
    for public in PUBLIC_PATHS:
        if path.startswith(public):
            return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to check authentication via HttpOnly cookie or Bearer token."""
    
    async def dispatch(self, request: Request, call_next):
        # Skip authentication for public paths
        if is_public_path(request.url.path):
            return await call_next(request)
        
        # Try to get token from HttpOnly cookie first
        token = request.cookies.get("spider_token")
        
        # Fallback to Authorization header for API clients
        if not token:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header[7:]
        
        if not token:
            # Redirect to login for browser requests
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(url="/login", status_code=302)
            # Return 401 for API requests
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"}
            )
        
        # Validate token
        payload = decode_access_token(token)
        if not payload or "sub" not in payload:
            # Clear invalid cookie
            response = RedirectResponse(url="/login", status_code=302) if "text/html" in request.headers.get("accept", "") else JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"}
            )
            response.delete_cookie("spider_token", httponly=True, secure=True, samesite="lax")
            return response
        
        # Attach user info to request state
        request.state.user = payload["sub"]
        request.state.token = token
        
        return await call_next(request)


def set_auth_cookie(response: Response, token: str, expires_minutes: int = 1440) -> None:
    """Set HttpOnly secure cookie with the auth token."""
    response.set_cookie(
        key="spider_token",
        value=token,
        max_age=expires_minutes * 60,
        httponly=True,
        secure=True,  # Only over HTTPS (Railway provides HTTPS)
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Clear the auth cookie on logout."""
    response.delete_cookie(
        key="spider_token",
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )