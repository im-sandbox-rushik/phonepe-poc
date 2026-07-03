"""Validation API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from config import load_config
from services.validation_service import ValidationService

logger = logging.getLogger(__name__)
router = APIRouter()


class ValidateRequest(BaseModel):
    repository: Optional[str] = None  # If None, validate all


@router.post("")
async def trigger_validation(request: ValidateRequest = None):
    """
    POST /validate
    Trigger validation for repositories.
    """
    config = load_config()
    validation_service = ValidationService(config)

    if request and request.repository:
        result = await validation_service.validate_single(request.repository)
        return result
    else:
        results = await validation_service.validate_all()
        return {"results": results}
