from datetime import datetime
from typing import List
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from fastapi import HTTPException, status

from app.users.models import Users, UserAddress
from app.users.schemas import (
    UserCreate, UserResponse, AddressCreate, AddressResponse,
    ProfileUpdate, GoogleLoginRequest,
)

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_hashed_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, hashed_pass: str) -> bool:
    return password_context.verify(password, hashed_pass)


def handle_google_login(db: Session, google_data: GoogleLoginRequest) -> Users:
    """Handle Google login - create or update user."""
    try:
        # Check if user exists by Google ID
        existing_user = db.query(Users).filter(Users.google_id == google_data.google_id).first()

        if existing_user:
            existing_user.name          = google_data.name
            existing_user.email         = google_data.email
            existing_user.profile_image = google_data.image
            existing_user.confirmed     = True
            # SECURITY: Do not persist short-lived OAuth tokens in the DB.
            # If the database is compromised, stored tokens become a second
            # credential that can be replayed against Google APIs.
            # We only store the stable google_id for identity linkage.
            db.commit()
            db.refresh(existing_user)
            return existing_user

        # Check if user exists by email (registered with email, now using Google)
        existing_user_by_email = db.query(Users).filter(Users.email == google_data.email).first()

        if existing_user_by_email:
            existing_user_by_email.google_id    = google_data.google_id
            existing_user_by_email.profile_image = google_data.image
            existing_user_by_email.confirmed     = True
            db.commit()
            db.refresh(existing_user_by_email)
            return existing_user_by_email

        # Create new user
        new_user = Users(
            email=google_data.email,
            name=google_data.name,
            google_id=google_data.google_id,
            profile_image=google_data.image,
            confirmed=True,
            role=5,
            hashed_password=None,
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return new_user

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google login failed"
        )


def create_user(db: Session, user: UserCreate) -> Users:
    """Create a new user (uses hashed_password — the actual column on Users)."""
    try:
        hashed_password = get_hashed_password(user.password)
        db_user = Users(
            email=user.email,
            name=user.name,
            phone=user.phone,
            hashed_password=hashed_password,
            confirmed=True,
            role=5,
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    except Exception as e:
        db.rollback()
        raise e


def get_user_by_email(db: Session, email: str) -> Users:
    """Get user by email."""
    return db.query(Users).filter(Users.email == email).first()


def update_user_profile(db: Session, user: Users, profile: ProfileUpdate) -> Users:
    """Update user profile."""
    try:
        for key, value in profile.model_dump(exclude_unset=True).items():
            setattr(user, key, value)
        if hasattr(user, "updated_at"):
            user.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(user)
        return user
    except Exception as e:
        db.rollback()
        raise e


def create_address(db: Session, address: AddressCreate, user) -> UserAddress:
    """Create a new address for user (user = dict from JWTBearer)."""
    try:
        if not user or 'id' not in user:
            raise Exception("Invalid token")

        user_id = user['id']

        if address.is_default:
            db.query(UserAddress).filter(
                UserAddress.user_id == user_id,
                UserAddress.is_default == True,  # noqa: E712
            ).update({"is_default": False})

        db_address = UserAddress(
            user_id=user_id,
            **address.model_dump()
        )
        db.add(db_address)
        db.commit()
        db.refresh(db_address)
        return db_address
    except Exception as e:
        db.rollback()
        raise e


def get_user_addresses(db: Session, user) -> List[UserAddress]:
    """
    Get all addresses for the user.

    `user` is the dict produced by JWTBearer (works for both cookie and
    Authorization-header auth). Previously this took a raw token and read it
    from the cookie only, which broke for all NextAuth (header-auth) users.
    """
    try:
        if not user or 'id' not in user:
            raise Exception("Invalid token")
        return db.query(UserAddress).filter(UserAddress.user_id == user['id']).all()
    except Exception as e:
        raise e


def get_address(db: Session, address_id: str, user) -> UserAddress:
    """Get a specific address for the user."""
    try:
        if not user or 'id' not in user:
            raise Exception("Invalid token")

        user_id = user['id']

        return db.query(UserAddress).filter(
            UserAddress.id == address_id,
            UserAddress.user_id == user_id
        ).first()
    except Exception as e:
        raise e


def update_address(db: Session, address_id: str, address: AddressCreate, user) -> UserAddress:
    """Update a user address."""
    try:
        if not user or 'id' not in user:
            raise Exception("Invalid token")

        user_id = user['id']

        db_address = get_address(db, address_id, user)
        if not db_address:
            return None

        if address.is_default:
            db.query(UserAddress).filter(
                UserAddress.user_id == user_id,
                UserAddress.is_default == True,  # noqa: E712
                UserAddress.id != address_id
            ).update({"is_default": False})

        for key, value in address.model_dump().items():
            setattr(db_address, key, value)

        db.commit()
        db.refresh(db_address)
        return db_address
    except Exception as e:
        db.rollback()
        raise e


def delete_address(db: Session, address_id: str, user) -> bool:
    """Delete a user address."""
    try:
        if not user or 'id' not in user:
            raise Exception("Invalid token")

        db_address = get_address(db, address_id, user)
        if not db_address:
            return False

        db.delete(db_address)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        raise e