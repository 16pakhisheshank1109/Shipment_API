import os
from hashlib import sha256
from datetime import datetime, timezone, timedelta
from typing import Any
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlmodel import Session, select

from database.models import RevokedToken, User
from database.sessions import get_session

# load values from .env file
load_dotenv()

# JWT configuration (defaults provided so app runs even if .env is missing)
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-jwt-key-replace-in-production-123456")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# scheme to look for "Bearer <token>" in the Authorization header
oauth_scheme = HTTPBearer()


# generates a jwt token that expires in 30 minutes
def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# alias so both generate_token and create_access_token work
generate_token = create_access_token


# decodes and checks if the token is valid and not expired
def verify_token(token: str) -> int:
    payload = _decode_token(token)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user id",
        )
    try:
        return int(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: invalid user id",
        ) from exc


def _decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def revoke_access_token(token: str, session: Session) -> None:
    payload = _decode_token(token)
    verify_token(token)

    expiration = payload.get("exp")
    if not isinstance(expiration, (int, float)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing expiration",
        )

    expires_at = datetime.fromtimestamp(expiration, tz=timezone.utc)
    token_hash = _token_hash(token)
    if session.get(RevokedToken, token_hash) is not None:
        return

    now = datetime.now(timezone.utc)
    expired_tokens = session.exec(
        select(RevokedToken).where(RevokedToken.expires_at <= now)
    ).all()
    for expired_token in expired_tokens:
        session.delete(expired_token)

    session.add(RevokedToken(token_hash=token_hash, expires_at=expires_at))
    session.commit()


# dependency to protect routes: gets the user associated with the bearer token
def get_user(
    credentials: HTTPAuthorizationCredentials = Depends(oauth_scheme),
    session: Session = Depends(get_session),
) -> User:
    token = credentials.credentials
    user_id = verify_token(token)
    if session.get(RevokedToken, _token_hash(token)) is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user = session.get(User, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user