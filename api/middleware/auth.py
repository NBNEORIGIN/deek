import os
from fastapi import HTTPException, Header
from typing import Optional


def verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> bool:
    expected = os.getenv('CLAW_API_KEY', '')
    if not expected:
        return True  # No key configured — dev mode, open access
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True
