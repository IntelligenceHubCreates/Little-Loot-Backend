import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.db import get_db
from app.users.utils import JWTBearer
from app.users.schemas import AddressCreate, AddressResponse
from app.users.services import (
    create_address, get_user_addresses, get_address, update_address, delete_address,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/address", tags=["address"])


@router.post("/addresses")
async def create_user_address(
    address: AddressCreate,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    """Create a new address for the current user."""
    try:
        created = create_address(db, address, user)
        if created:
            return {"message": "Address Created Successfully"}
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Address could not be created")
    except HTTPException:
        raise
    except Exception:
        logger.error("create_user_address failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred")


@router.get("/addresses", response_model=List[AddressResponse])
async def get_addresses(
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    """Get all addresses for the current user."""
    try:
        addresses_object = get_user_addresses(db, user)

        result = []
        for i in addresses_object:
            if isinstance(i.id, UUID):
                i.id = str(i.id)
            if isinstance(i.user_id, UUID):
                i.user_id = str(i.user_id)
            result.append(i)

        return [AddressResponse.from_orm(address) for address in result]
    except Exception:
        logger.error("get_addresses failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred")


@router.get("/addresses/{address_id}", response_model=AddressResponse)
async def get_address_by_id(
    address_id: str,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    """Get a specific address by ID."""
    try:
        address = get_address(db, address_id, user)
        if not address:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")

        if isinstance(address.id, UUID):
            address.id = str(address.id)
        if isinstance(address.user_id, UUID):
            address.user_id = str(address.user_id)

        return address
    except HTTPException:
        raise
    except Exception:
        logger.error("get_address_by_id failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred")


@router.put("/addresses/{address_id}")
async def update_user_address(
    address_id: str,
    address: AddressCreate,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    """Update an existing address."""
    try:
        updated_address = update_address(db, address_id, address, user)
        if not updated_address:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
        return {"message": "Update Successfully"}
    except HTTPException:
        raise
    except Exception:
        logger.error("update_user_address failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred")


@router.delete("/addresses/{address_id}")
async def delete_user_address(
    address_id: str,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    """Delete an address."""
    try:
        success = delete_address(db, address_id, user)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
        return {"message": "Address deleted successfully"}
    except HTTPException:
        raise
    except Exception:
        logger.error("delete_user_address failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred")
