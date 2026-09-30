import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .database import get_db
from .models import User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _kind, salt, expected = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def make_token(user: User) -> str:
    if len(config.JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET 未初始化，请先执行 python -m app.seed")
    return jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc) +
                       timedelta(hours=config.TOKEN_HOURS)}, config.JWT_SECRET, algorithm="HS256")


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    if not credentials:
        raise HTTPException(401, "请先登录")
    try:
        data = jwt.decode(credentials.credentials, config.JWT_SECRET, algorithms=["HS256"])
        user = db.scalar(select(User).where(User.id == int(data["sub"])))
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "登录已失效，请重新登录") from None
    if user is None:
        raise HTTPException(401, "账号不存在")
    if user.status != "active":
        raise HTTPException(403, "账号待系统管理员启用")
    return user


def require(user: User, *roles):
    if user.role not in roles:
        raise HTTPException(403, "当前角色无权执行此操作")
