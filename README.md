# High-Concurrency Wallet & Ledger Engine 🚀

Hệ thống ví điện tử & sổ cái giao dịch chịu tải cao, đảm bảo tính nhất quán dữ liệu tuyệt đối (ACID), chống race condition và tự động đối soát.

## 🛠 Công nghệ sử dụng
- **Backend:** Python (FastAPI, SQLAlchemy)
- **Database:** PostgreSQL (Pessimistic Locking)
- **Caching & Locking:** Redis, Redlock (Distributed Lock Manager)
- **Containerization:** Docker, Docker Compose

## 📌 Tính năng chính
- 💸 **Chuyển tiền tức thời:** Xử lý race condition bằng Redlock & Pessimistic Locking (`SELECT ... FOR UPDATE`).
- 🔒 **Idempotency:** Ngăn chặn giao dịch trùng lặp khi người dùng click nhiều lần.
- 🔄 **Hoàn tiền (Refund):** Thu hồi và hoàn tiền an toàn.
- 📊 **Đối soát tự động (Reconciliation):** So sánh số dư thực tế trên ví với tổng nhật ký Sổ cái (Ledger Entries).
- ⚡ **Redis Cache:** Tăng tốc độ đọc truy vấn số dư.

## 🚀 Hướng dẫn khởi chạy

### Yêu cầu tiên quyết
- Cài đặt **Docker Desktop**

### Các bước cài đặt
1. Clone dự án về máy:
   ```bash
   git clone [https://github.com/username/wallet-service.git](https://github.com/username/wallet-service.git)
   cd wallet-service