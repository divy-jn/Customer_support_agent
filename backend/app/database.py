"""
SQLAlchemy models and async engine setup for PostgreSQL.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, DateTime, ForeignKey, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import settings

# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────
def _utcnow():
    """Timezone-aware UTC now (replaces deprecated datetime.utcnow)."""
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────
#  Async engine (used by FastAPI at runtime)
# ──────────────────────────────────────────────
async_engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

# ──────────────────────────────────────────────
#  Sync engine (used by seed scripts)
# ──────────────────────────────────────────────
sync_engine = create_engine(settings.database_url_sync, echo=False)
SyncSessionLocal = sessionmaker(bind=sync_engine)

Base = declarative_base()


# ──────────────────────────────────────────────
#  ORM Models
# ──────────────────────────────────────────────

class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    age = Column(Integer)
    gender = Column(String(20))
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    orders = relationship("Order", back_populates="customer", lazy="selectin")
    tickets = relationship("Ticket", back_populates="customer", lazy="selectin")
    conversations = relationship("Conversation", back_populates="customer", lazy="selectin")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "age": self.age,
            "gender": self.gender,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    category = Column(String(100))
    price = Column(Numeric(10, 2))
    stock = Column(Integer, default=0)
    description = Column(Text)

    # Relationships
    orders = relationship("Order", back_populates="product", lazy="selectin")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "price": float(self.price) if self.price else None,
            "stock": self.stock,
            "description": self.description,
        }


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    order_date = Column(DateTime(timezone=True))
    status = Column(String(50), default="active")  # active, shipped, delivered, cancelled, refunded
    tracking_number = Column(String(100))

    # Relationships
    customer = relationship("Customer", back_populates="orders")
    product = relationship("Product", back_populates="orders")
    tickets = relationship("Ticket", back_populates="order", lazy="selectin")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "product_id": self.product_id,
            "product_name": self.product.name if self.product else None,
            "order_date": self.order_date.isoformat() if self.order_date else None,
            "status": self.status,
            "tracking_number": self.tracking_number,
        }


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    subject = Column(String(200))
    description = Column(Text)
    type = Column(String(50))          # technical_issue, billing, refund, cancellation, inquiry
    status = Column(String(50))        # open, in_progress, pending_customer, escalated, closed
    priority = Column(String(20))      # low, medium, high, critical
    channel = Column(String(50))       # chat, email, phone, social_media
    assigned_agent = Column(String(100))
    resolution = Column(Text)
    satisfaction_rating = Column(Integer)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    customer = relationship("Customer", back_populates="tickets")
    order = relationship("Order", back_populates="tickets")
    conversations = relationship("Conversation", back_populates="ticket", lazy="selectin")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else None,
            "order_id": self.order_id,
            "subject": self.subject,
            "description": self.description,
            "type": self.type,
            "status": self.status,
            "priority": self.priority,
            "channel": self.channel,
            "assigned_agent": self.assigned_agent,
            "resolution": self.resolution,
            "satisfaction_rating": self.satisfaction_rating,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=True)
    transcript = Column(Text)                        # JSON text of conversation
    escalated = Column(String(5), default="false")   # "true" / "false"
    started_at = Column(DateTime(timezone=True), default=_utcnow)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    ticket = relationship("Ticket", back_populates="conversations")
    customer = relationship("Customer", back_populates="conversations")

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "customer_id": self.customer_id,
            "ticket_id": self.ticket_id,
            "transcript": self.transcript,
            "escalated": self.escalated,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


# ──────────────────────────────────────────────
#  Database Lifecycle Helpers
# ──────────────────────────────────────────────

async def get_async_session() -> AsyncSession:
    """Dependency for FastAPI endpoints."""
    async with AsyncSessionLocal() as session:
        yield session


def create_all_tables_sync():
    """Create all tables using the sync engine (used by seed scripts)."""
    Base.metadata.create_all(bind=sync_engine)


async def create_all_tables_async():
    """Create all tables using the async engine."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
