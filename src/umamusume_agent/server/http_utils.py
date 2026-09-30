"""Single-character HTTP error and browser-identity translation."""
import logging
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from openai import APIConnectionError, APITimeoutError, APIStatusError

logger = logging.getLogger(__name__)


def _extract_upstream_error_detail(exc: APIStatusError) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return f"上游模型服务返回 {exc.status_code}"

    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return f"上游模型服务返回 {exc.status_code}: {message.strip()}"
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return f"上游模型服务返回 {exc.status_code}: {detail.strip()}"
    except Exception:
        pass

    return f"上游模型服务返回 {exc.status_code}"


def translate_llm_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, APITimeoutError):
        return HTTPException(status_code=504, detail="上游模型服务超时，请稍后重试。")
    if isinstance(exc, APIConnectionError):
        return HTTPException(status_code=502, detail="无法连接到上游模型服务，请稍后重试。")
    if isinstance(exc, APIStatusError):
        status_code = exc.status_code or 502
        mapped_status = status_code if 400 <= status_code <= 599 else 502
        return HTTPException(status_code=mapped_status, detail=_extract_upstream_error_detail(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=f"对话失败: {str(exc)}")


def normalize_user_uuid(user_uuid: Optional[str]) -> str:
    if not user_uuid:
        return str(uuid4())
    try:
        return str(UUID(str(user_uuid)))
    except ValueError:
        logger.warning("Invalid user_uuid received, generate a new one: %s", user_uuid)
        return str(uuid4())


def require_valid_user_uuid(user_uuid: str) -> str:
    try:
        return str(UUID(str(user_uuid)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid user_uuid: {user_uuid}") from e
