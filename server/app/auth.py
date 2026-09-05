"""Google sign-in and small signed sessions without an auth framework."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import Header, HTTPException, Request, Response
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

COOKIE_NAME = "clipstack_session"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").encode()
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_token(user: dict[str, Any], lifetime_seconds: int) -> str:
    if len(SESSION_SECRET) < 32:
        raise HTTPException(status_code=503, detail="SESSION_SECRET must be at least 32 characters.")
    payload = {
        "sub": user["sub"],
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "picture": user.get("picture", ""),
        "exp": int(time.time()) + lifetime_seconds,
    }
    encoded = _encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _encode(hmac.new(SESSION_SECRET, encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def read_token(token: str) -> dict[str, Any] | None:
    if len(SESSION_SECRET) < 32:
        return None
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected = _encode(hmac.new(SESSION_SECRET, encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied_signature, expected):
            return None
        payload = json.loads(_decode(encoded))
        if not payload.get("sub") or int(payload.get("exp", 0)) <= time.time():
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def current_user(
    request: Request,
    x_clip_token: str | None = Header(default=None, alias="X-Clip-Token"),
) -> dict[str, Any]:
    user = read_token(request.cookies.get(COOKIE_NAME, "")) or read_token(x_clip_token or "")
    if not user:
        raise HTTPException(status_code=401, detail="Sign in with Google.")
    return user


def verify_google(credential: str) -> dict[str, Any]:
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="GOOGLE_CLIENT_ID is not configured.")
    try:
        payload = id_token.verify_oauth2_token(credential, GoogleRequest(), GOOGLE_CLIENT_ID)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Google sign-in could not be verified.") from exc
    if not payload.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google email is not verified.")
    return {
        "sub": payload["sub"],
        "email": payload.get("email", ""),
        "name": payload.get("name", ""),
        "picture": payload.get("picture", ""),
    }


def set_session(response: Response, user: dict[str, Any]) -> None:
    response.set_cookie(
        COOKIE_NAME,
        create_token(user, 60 * 60 * 24 * 7),
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=COOKIE_SECURE, samesite="lax")
