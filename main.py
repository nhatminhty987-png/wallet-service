import os
import json
from decimal import Decimal
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Numeric, ForeignKey, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import redis
from redlock import Redlock

# Dynamic Environment Configurations
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://wallet_user:wallet_password@db:5432/wallet_db")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Database setup
engine = create_engine(DATABASE_URL, pool_size=50, max_overflow=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Redis & Redlock setup
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
dlm = Redlock([{"host": "redis", "port": 6379, "db": 0}] if os.getenv("DATABASE_URL") else [{"host": "localhost", "port": 6379, "db": 0}])

# Models
class WalletModel(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, nullable=False)
    balance = Column(Numeric(18, 4), nullable=False)
    currency = Column(String(10), default="VND")

class LedgerEntryModel(Base):
    __tablename__ = "ledger_entries"
    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String(64), nullable=False, index=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    amount = Column(Numeric(18, 4), nullable=False)
    balance_after = Column(Numeric(18, 4), nullable=False)
    type = Column(String(20), nullable=False) # TRANSFER_OUT, TRANSFER_IN, REFUND_OUT, REFUND_IN

Base.metadata.create_all(bind=engine)

app = FastAPI(title="High-Concurrency Wallet & Ledger Engine")
from i18n import LanguageMiddleware
app.add_middleware(LanguageMiddleware)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# DTO Schemas
class TransferRequest(BaseModel):
    idempotency_key: str
    sender_wallet_id: int
    receiver_wallet_id: int
    amount: Decimal

class RefundRequest(BaseModel):
    idempotency_key: str
    original_transaction_id: str
    reason: Optional[str] = "Khách hàng yêu cầu hoàn tiền"

# --- CACHING LAYER ---
def get_cached_balance(wallet_id: int, db: Session) -> float:
    cache_key = f"wallet:balance:{wallet_id}"
    cached_val = redis_client.get(cache_key)
    if cached_val is not None:
        return float(cached_val)

    wallet = db.query(WalletModel).filter(WalletModel.id == wallet_id).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Không tìm thấy ví")
    
    redis_client.setex(cache_key, 60, str(wallet.balance)) # Cache TTL 60 seconds
    return float(wallet.balance)

def invalidate_wallet_cache(wallet_id: int):
    redis_client.delete(f"wallet:balance:{wallet_id}")

# --- API ENDPOINTS ---

@app.get("/api/v1/wallets/{wallet_id}/balance")
def read_balance(wallet_id: int, db: Session = Depends(get_db)):
    balance = get_cached_balance(wallet_id, db)
    return {"wallet_id": wallet_id, "balance": balance}

@app.post("/api/v1/transfer")
def transfer_funds(req: TransferRequest, db: Session = Depends(get_db)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền giao dịch phải lớn hơn 0")
    
    # 1. Distributed Locking với Redlock
    lock_key = f"lock:transfer:{min(req.sender_wallet_id, req.receiver_wallet_id)}:{max(req.sender_wallet_id, req.receiver_wallet_id)}"
    lock = dlm.lock(lock_key, 3000) # Lock in 3 seconds
    
    if not lock:
        raise HTTPException(status_code=429, detail="Hệ thống đang bận xử lý giao dịch cho ví này, vui lòng thử lại.")

    try:
        # Kiểm tra Idempotency
        existing_tx = db.query(LedgerEntryModel).filter(LedgerEntryModel.transaction_id == req.idempotency_key).first()
        if existing_tx:
            return {"status": "SUCCESS", "message": "Giao dịch đã được xử lý trước đó (Idempotent)"}

        # Pessimistic Locking trong DB
        first_id, second_id = sorted([req.sender_wallet_id, req.receiver_wallet_id])
        wallets = db.query(WalletModel).filter(WalletModel.id.in_([first_id, second_id])).with_for_update().all()
        wallet_map = {w.id: w for w in wallets}

        sender = wallet_map.get(req.sender_wallet_id)
        receiver = wallet_map.get(req.receiver_wallet_id)

        if not sender or not receiver:
            raise HTTPException(status_code=404, detail="Ví không hợp lệ")

        if sender.balance < req.amount:
            raise HTTPException(status_code=400, detail="Số dư không đủ")

        sender.balance -= req.amount
        receiver.balance += req.amount

        db.add_all([
            LedgerEntryModel(transaction_id=req.idempotency_key, wallet_id=sender.id, amount=-req.amount, balance_after=sender.balance, type="TRANSFER_OUT"),
            LedgerEntryModel(transaction_id=req.idempotency_key, wallet_id=receiver.id, amount=req.amount, balance_after=receiver.balance, type="TRANSFER_IN")
        ])
        db.commit()

        # Invalidate Cache
        invalidate_wallet_cache(sender.id)
        invalidate_wallet_cache(receiver.id)

        return {"status": "SUCCESS", "transaction_id": req.idempotency_key}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        dlm.unlock(lock)

# --- TÍNH NĂNG HOÀN TIỀN (REFUND) ---
@app.post("/api/v1/refund")
def refund_transaction(req: RefundRequest, db: Session = Depends(get_db)):
    lock_key = f"lock:refund:{req.original_transaction_id}"
    lock = dlm.lock(lock_key, 5000)
    
    if not lock:
        raise HTTPException(status_code=429, detail="Yêu cầu hoàn tiền đang được xử lý")

    try:
        # Tìm giao dịch gốc
        orig_entries = db.query(LedgerEntryModel).filter(LedgerEntryModel.transaction_id == req.original_transaction_id).all()
        if not orig_entries:
            raise HTTPException(status_code=404, detail="Không tìm thấy giao dịch gốc")

        # Kiểm tra xem đã refund chưa
        refund_exists = db.query(LedgerEntryModel).filter(LedgerEntryModel.transaction_id == req.idempotency_key).first()
        if refund_exists:
            return {"status": "SUCCESS", "message": "Giao dịch hoàn tiền đã được xử lý trước đó"}

        out_entry = next((e for e in orig_entries if e.amount < 0), None)
        in_entry = next((e for e in orig_entries if e.amount > 0), None)

        orig_sender_id = out_entry.wallet_id # Người nhận lại tiền
        orig_receiver_id = in_entry.wallet_id # Người bị thu hồi tiền
        refund_amount = abs(out_entry.amount)

        # Lock ví & đảo ngược dòng tiền
        first_id, second_id = sorted([orig_sender_id, orig_receiver_id])
        wallets = db.query(WalletModel).filter(WalletModel.id.in_([first_id, second_id])).with_for_update().all()
        wallet_map = {w.id: w for w in wallets}

        orig_sender = wallet_map[orig_sender_id]
        orig_receiver = wallet_map[orig_receiver_id]

        if orig_receiver.balance < refund_amount:
            raise HTTPException(status_code=400, detail="Ví nhận cũ không đủ số dư để thực hiện thu hồi hoàn tiền")

        orig_receiver.balance -= refund_amount
        orig_sender.balance += refund_amount

        db.add_all([
            LedgerEntryModel(transaction_id=req.idempotency_key, wallet_id=orig_receiver.id, amount=-refund_amount, balance_after=orig_receiver.balance, type="REFUND_OUT"),
            LedgerEntryModel(transaction_id=req.idempotency_key, wallet_id=orig_sender.id, amount=refund_amount, balance_after=orig_sender.balance, type="REFUND_IN")
        ])
        db.commit()

        invalidate_wallet_cache(orig_sender.id)
        invalidate_wallet_cache(orig_receiver.id)

        return {"status": "SUCCESS", "refund_tx_id": req.idempotency_key, "amount": refund_amount}

    finally:
        dlm.unlock(lock)

# --- TÍNH NĂNG ĐỐI SOÁT TỰ ĐỘNG (AUTOMATED RECONCILIATION) ---
@app.get("/api/v1/reconciliation/check/{wallet_id}")
def reconcile_wallet(wallet_id: int, db: Session = Depends(get_db)):
    """
    Đối soát tính nhất quán giữa Số dư hiện tại trên Ví (Wallet Table) 
    và Tổng lịch sử biến động trong Sổ cái (Ledger Table).
    """
    wallet = db.query(WalletModel).filter(WalletModel.id == wallet_id).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Không tìm thấy ví")

    # Tính tổng tất cả biến động ghi trong Ledger
    ledger_sum = db.query(func.sum(LedgerEntryModel.amount)).filter(LedgerEntryModel.wallet_id == wallet_id).scalar() or Decimal('0.0000')

    is_matched = (wallet.balance == ledger_sum)

    return {
        "wallet_id": wallet_id,
        "current_balance": float(wallet.balance),
        "ledger_calculated_balance": float(ledger_sum),
        "discrepancy": float(wallet.balance - ledger_sum),
        "is_consistent": is_matched,
        "status": "HEALTHY" if is_matched else "DISCREPANCY_DETECTED"
    }
# API tự động tạo dữ liệu ví mẫu để test
@app.post("/api/v1/seed")
def seed_wallets(db: Session = Depends(get_db)):
    # Tạo ví 1 với 1,000,000 VND và ví 2 với 100,000 VND nếu chưa tồn tại
    w1 = db.query(WalletModel).filter(WalletModel.id == 1).first()
    if not w1:
        w1 = WalletModel(id=1, user_id=101, balance=Decimal("1000000.0000"), currency="VND")
        db.add(w1)
        
    w2 = db.query(WalletModel).filter(WalletModel.id == 2).first()
    if not w2:
        w2 = WalletModel(id=2, user_id=102, balance=Decimal("100000.0000"), currency="VND")
        db.add(w2)

    db.commit()
    invalidate_wallet_cache(1)
    invalidate_wallet_cache(2)
    return {"status": "SUCCESS", "message": "Đã tạo xong dữ liệu ví mẫu (Ví 1: 1,000,000 VND, Ví 2: 100,000 VND)"}