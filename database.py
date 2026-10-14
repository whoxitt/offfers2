import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Dict


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def init_schema(self):
        with self.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS offer_business_connections (
                connection_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_enabled INTEGER DEFAULT 1,
                is_blocked INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS offer_access_users (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS offer_blocked_users (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS offer_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS offer_user_profiles (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                url TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS gift_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER NOT NULL,
                seller_username TEXT,
                buyer_id INTEGER NOT NULL,
                buyer_username TEXT,
                buyer_url TEXT,
                gift_name TEXT NOT NULL,
                gift_number INTEGER NOT NULL,
                gift_url TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                locale TEXT NOT NULL DEFAULT 'ru',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                accepted_at TIMESTAMP,
                transfer_confirmed INTEGER DEFAULT 0,
                transfer_confirmed_at TIMESTAMP,
                message_chat_id INTEGER,
                message_id INTEGER,
                business_connection_id TEXT,
                transfer_url TEXT
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memo TEXT NOT NULL UNIQUE,
                amount REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_offers_status ON gift_offers(status);
            CREATE INDEX IF NOT EXISTS idx_offers_seller ON gift_offers(seller_id);
            CREATE INDEX IF NOT EXISTS idx_offers_buyer ON gift_offers(buyer_id);
            CREATE INDEX IF NOT EXISTS idx_payments_memo ON payments(memo);
            CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
            """)

    def upsert_business_connection(self, connection_id: str, user_id: int, username: Optional[str],
                                   first_name: Optional[str], last_name: Optional[str], enabled: bool):
        with self.connect() as con:
            con.execute("""INSERT INTO offer_business_connections
                (connection_id,user_id,username,first_name,last_name,is_enabled,is_blocked,updated_at)
                VALUES (?,?,?,?,?,?,COALESCE((SELECT is_blocked FROM offer_business_connections WHERE connection_id=?),0),CURRENT_TIMESTAMP)
                ON CONFLICT(connection_id) DO UPDATE SET user_id=excluded.user_id,
                username=excluded.username,first_name=excluded.first_name,last_name=excluded.last_name,
                is_enabled=excluded.is_enabled,updated_at=CURRENT_TIMESTAMP""",
                        (connection_id, user_id, username, first_name, last_name, int(enabled), connection_id))

    def get_business_connection(self, connection_id: str):
        with self.connect() as con:
            return con.execute("SELECT * FROM offer_business_connections WHERE connection_id=?", (connection_id,)).fetchone()

    def get_connection_for_user(self, user_id: int):
        with self.connect() as con:
            return con.execute("""SELECT * FROM offer_business_connections
                WHERE user_id=? AND is_enabled=1 ORDER BY updated_at DESC LIMIT 1""", (user_id,)).fetchone()

    def set_connection_blocked(self, connection_id: str, blocked: bool):
        with self.connect() as con:
            con.execute("UPDATE offer_business_connections SET is_blocked=?, updated_at=CURRENT_TIMESTAMP WHERE connection_id=?", (int(blocked), connection_id))

    def add_access(self, user_id: int):
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO offer_access_users(user_id) VALUES(?)", (user_id,))

    def remove_access(self, user_id: int):
        with self.connect() as con:
            con.execute("DELETE FROM offer_access_users WHERE user_id=?", (user_id,))

    def unblock_offer_user(self, user_id: int):
        with self.connect() as con:
            con.execute("DELETE FROM offer_blocked_users WHERE user_id=?", (user_id,))

    def block_offer_user(self, user_id: int):
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO offer_blocked_users(user_id) VALUES(?)", (user_id,))

    def is_blocked(self, user_id: int) -> bool:
        with self.connect() as con:
            return con.execute("SELECT 1 FROM offer_blocked_users WHERE user_id=?", (user_id,)).fetchone() is not None

    def has_access(self, user_id: int, owner_id: int, mode: str) -> bool:
        if self.is_blocked(user_id):
            return False
        if user_id == owner_id or mode == "all":
            return True
        with self.connect() as con:
            return con.execute("SELECT 1 FROM offer_access_users WHERE user_id=?", (user_id,)).fetchone() is not None

    def set_profile(self, user_id: int, name: Optional[str], url: Optional[str]):
        with self.connect() as con:
            con.execute("""INSERT INTO offer_user_profiles(user_id,name,url,updated_at)
                VALUES(?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,url=excluded.url,updated_at=CURRENT_TIMESTAMP""",
                        (user_id, name, url))

    def get_profile(self, user_id: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM offer_user_profiles WHERE user_id=?", (user_id,)).fetchone()

    def create_offer(self, data: Dict[str, Any]) -> int:
        with self.connect() as con:
            cur = con.execute("""INSERT INTO gift_offers
                (seller_id,seller_username,buyer_id,buyer_username,buyer_url,gift_name,gift_number,gift_url,amount,currency,locale,status,created_at,expires_at,message_chat_id,message_id,business_connection_id,transfer_url)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", tuple(data[k] for k in (
                    'seller_id','seller_username','buyer_id','buyer_username','buyer_url','gift_name','gift_number','gift_url','amount','currency','locale','status','created_at','expires_at','message_chat_id','message_id','business_connection_id','transfer_url')))
            return int(cur.lastrowid)

    def set_offer_message(self, offer_id: int, chat_id: int, message_id: int):
        with self.connect() as con:
            con.execute("UPDATE gift_offers SET message_chat_id=?, message_id=? WHERE id=?", (chat_id, message_id, offer_id))

    def delete_offer(self, offer_id: int):
        with self.connect() as con:
            con.execute("DELETE FROM gift_offers WHERE id=?", (offer_id,))

    def get_offer(self, offer_id: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM gift_offers WHERE id=?", (offer_id,)).fetchone()

    def update_offer_status(self, offer_id: int, status: str, transfer_url: Optional[str] = None):
        with self.connect() as con:
            if status == "accepted":
                con.execute("UPDATE gift_offers SET status=?, transfer_url=COALESCE(?,transfer_url), accepted_at=CURRENT_TIMESTAMP WHERE id=?", (status, transfer_url, offer_id))
            else:
                con.execute("UPDATE gift_offers SET status=?, transfer_url=COALESCE(?,transfer_url) WHERE id=?", (status, transfer_url, offer_id))

    def confirm_transfer(self, offer_id: int):
        with self.connect() as con:
            con.execute("UPDATE gift_offers SET transfer_confirmed=1, transfer_confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (offer_id,))

    def list_connections(self):
        with self.connect() as con:
            return con.execute("SELECT * FROM offer_business_connections ORDER BY updated_at DESC").fetchall()

    def stats(self):
        with self.connect() as con:
            rows = con.execute("SELECT status, COUNT(*) c FROM gift_offers GROUP BY status").fetchall()
            return {r['status']: r['c'] for r in rows}

    def create_payment(self, user_id: int, memo: str, amount: float) -> int:
        with self.connect() as con:
            cur = con.execute("""INSERT INTO payments(user_id, memo, amount, status, created_at)
                VALUES(?,?,?,'pending',CURRENT_TIMESTAMP)""", (user_id, memo, amount))
            return int(cur.lastrowid)

    def get_payment_by_memo(self, memo: str):
        with self.connect() as con:
            return con.execute("SELECT * FROM payments WHERE memo=?", (memo,)).fetchone()

    def get_pending_payment(self, user_id: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM payments WHERE user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (user_id,)).fetchone()

    def mark_payment_paid(self, payment_id: int):
        with self.connect() as con:
            con.execute("UPDATE payments SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE id=?", (payment_id,))
