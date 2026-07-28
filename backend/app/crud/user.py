"""CRUD operations for User and Voice Profile models."""

from sqlalchemy.orm import Session
from app.models.user import User, VoiceProfile, VoiceSample, Conversation, Message
from app.schemas.user import UserCreate, VoiceProfileCreate, VoiceProfileUpdate, VoiceSampleCreate
import hashlib
import hmac


def hash_password(password: str) -> str:
    """Simple password hashing using SHA256."""
    return hashlib.sha256(password.encode()).hexdigest()


class UserCRUD:
    """CRUD operations for users."""
    
    @staticmethod
    def create_user(db: Session, user: UserCreate) -> User:
        """Create a new user."""
        password_hash = hash_password(user.password)
        
        db_user = User(
            full_name=user.full_name,
            email=user.email,
            password_hash=password_hash
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    
    @staticmethod
    def get_user_by_email(db: Session, email: str) -> User | None:
        """Get user by email."""
        return db.query(User).filter(User.email == email).first()

    @staticmethod
    def authenticate_user(db: Session, email: str, password: str) -> User | None:
        """Authenticate a user by email and password."""
        user = UserCRUD.get_user_by_email(db, email)
        if not user:
            return None

        password_hash = hash_password(password)
        if not hmac.compare_digest(password_hash, user.password_hash):
            return None

        return user
    
    @staticmethod
    def get_user(db: Session, user_id: int) -> User | None:
        """Get user by ID."""
        return db.query(User).filter(User.user_id == user_id).first()
    
    @staticmethod
    def get_users(db: Session, skip: int = 0, limit: int = 10) -> list[User]:
        """Get all users with pagination."""
        return db.query(User).offset(skip).limit(limit).all()


class VoiceProfileCRUD:
    """CRUD operations for voice profiles."""
    
    @staticmethod
    def create_voice_profile(db: Session, user_id: int, voice_profile: VoiceProfileCreate) -> VoiceProfile:
        """Create a new voice profile."""
        db_voice_profile = VoiceProfile(
            user_id=user_id,
            voice_name=voice_profile.voice_name,
            relationship=voice_profile.relationship,
            language=voice_profile.language,
            accent=voice_profile.accent,
            training_status="pending"
        )
        db.add(db_voice_profile)
        db.commit()
        db.refresh(db_voice_profile)
        return db_voice_profile
    
    @staticmethod
    def get_voice_profile(db: Session, voice_profile_id: int) -> VoiceProfile | None:
        """Get a voice profile by ID."""
        return db.query(VoiceProfile).filter(VoiceProfile.voice_profile_id == voice_profile_id).first()
    
    @staticmethod
    def get_user_voice_profiles(db: Session, user_id: int, skip: int = 0, limit: int = 10) -> list[VoiceProfile]:
        """Get all voice profiles for a user."""
        return db.query(VoiceProfile).filter(VoiceProfile.user_id == user_id).offset(skip).limit(limit).all()
    
    @staticmethod
    def update_voice_profile(db: Session, voice_profile_id: int, voice_profile_update: VoiceProfileUpdate) -> VoiceProfile | None:
        """Update a voice profile."""
        db_voice_profile = db.query(VoiceProfile).filter(VoiceProfile.voice_profile_id == voice_profile_id).first()
        if db_voice_profile:
            update_data = voice_profile_update.dict(exclude_unset=True)
            for field, value in update_data.items():
                setattr(db_voice_profile, field, value)
            db.commit()
            db.refresh(db_voice_profile)
        return db_voice_profile
    
    @staticmethod
    def delete_voice_profile(db: Session, voice_profile_id: int) -> bool:
        """Delete a voice profile."""
        db_voice_profile = db.query(VoiceProfile).filter(VoiceProfile.voice_profile_id == voice_profile_id).first()
        if db_voice_profile:
            db.delete(db_voice_profile)
            db.commit()
            return True
        return False


class VoiceSampleCRUD:
    """CRUD operations for voice samples."""
    
    @staticmethod
    def create_voice_sample(db: Session, voice_profile_id: int, sample: VoiceSampleCreate) -> VoiceSample:
        """Create a new voice sample."""
        db_sample = VoiceSample(
            voice_profile_id=voice_profile_id,
            file_path=sample.file_path,
            file_name=sample.file_name,
            duration_seconds=sample.duration_seconds,
            file_size_mb=sample.file_size_mb
        )
        db.add(db_sample)
        db.commit()
        db.refresh(db_sample)
        return db_sample
    
    @staticmethod
    def get_voice_samples(db: Session, voice_profile_id: int, skip: int = 0, limit: int = 50) -> list[VoiceSample]:
        """Get all samples for a voice profile."""
        return db.query(VoiceSample).filter(VoiceSample.voice_profile_id == voice_profile_id).offset(skip).limit(limit).all()
    
    @staticmethod
    def delete_voice_sample(db: Session, sample_id: int) -> bool:
        """Delete a voice sample."""
        db_sample = db.query(VoiceSample).filter(VoiceSample.sample_id == sample_id).first()
        if db_sample:
            db.delete(db_sample)
            db.commit()
            return True
        return False


class ConversationCRUD:
    """CRUD operations for conversations."""
    
    @staticmethod
    def create_conversation(
        db: Session,
        user_id: int,
        title: str = "New Chat"
    ) -> Conversation:
        """Create a conversation belonging to a user."""
        db_conversation = Conversation(
            user_id=user_id,
            title=title
        )
        db.add(db_conversation)
        db.commit()
        db.refresh(db_conversation)
        return db_conversation
    
    @staticmethod
    def get_conversation(
        db: Session,
        conversation_id: int
    ) -> Conversation | None:
        """Get a conversation by ID."""
        return (
            db.query(Conversation)
            .filter(Conversation.conversation_id == conversation_id)
            .first()
        )

    @staticmethod
    def get_user_conversation(
        db: Session,
        conversation_id: int,
        user_id: int
    ) -> Conversation | None:
        """Get a conversation only when it belongs to the user."""
        return (
            db.query(Conversation)
            .filter(
                Conversation.conversation_id == conversation_id,
                Conversation.user_id == user_id
            )
            .first()
        )
    
    @staticmethod
    def get_user_conversations(
        db: Session,
        user_id: int
    ) -> list[Conversation]:
        """Get a user's conversations ordered by recent activity."""
        return (
            db.query(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .all()
        )

    @staticmethod
    def update_conversation_title(
        db: Session,
        conversation: Conversation,
        title: str
    ) -> Conversation:
        """Update a conversation title."""
        conversation.title = title
        db.commit()
        db.refresh(conversation)
        return conversation

    @staticmethod
    def delete_conversation(
        db: Session,
        conversation: Conversation
    ) -> None:
        """Delete a conversation and its related messages."""
        db.delete(conversation)
        db.commit()


class MessageCRUD:
    """CRUD operations for messages."""
    
    @staticmethod
    def create_message(db: Session, conversation_id: int, sender: str, message_text: str, audio_path: str = None) -> Message:
        """Create a new message."""
        db_message = Message(
            conversation_id=conversation_id,
            sender=sender,
            message_text=message_text,
            audio_path=audio_path
        )
        db.add(db_message)
        db.commit()
        db.refresh(db_message)
        return db_message
    
    @staticmethod
    def get_conversation_messages(db: Session, conversation_id: int, skip: int = 0, limit: int = 50) -> list[Message]:
        """Get all messages in a conversation."""
        return db.query(Message).filter(Message.conversation_id == conversation_id).offset(skip).limit(limit).all()
