"""Strict incoming TON transaction verification using the official TonAPI v2."""
import logging
from typing import Optional
import aiohttp

log = logging.getLogger("payment")
NANO = 1_000_000_000


class TonAPIError(RuntimeError):
    pass


class TonkeeperAPI:
    def __init__(self, wallet_address: str, api_key: str = ""):
        self.wallet = wallet_address.strip()
        self.api_key = api_key.strip()
        self.base_url = "https://tonapi.io/v2"

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _comment(in_msg: dict) -> str:
        decoded = in_msg.get("decoded_body") or {}
        for value in (
            decoded.get("text"), decoded.get("comment"),
            in_msg.get("message"), in_msg.get("comment"),
        ):
            if value is not None:
                return str(value).strip()
        return ""

    @staticmethod
    def _hash(tx: dict) -> str:
        txid = tx.get("transaction_id") or {}
        return str(txid.get("hash") or tx.get("hash") or "").strip()

    @staticmethod
    def _same_address(a: str, b: str) -> bool:
        # TonAPI may represent an address in raw or user-friendly form.
        # Never compare usernames or transaction comments as an address substitute.
        return str(a or "").strip() == str(b or "").strip()

    async def find_payment(self, memo: str, expected_amount: float) -> Optional[str]:
        if not self.wallet or not memo or expected_amount <= 0:
            raise TonAPIError("Missing wallet, payment memo, or amount")
        url = f"{self.base_url}/blockchain/accounts/{self.wallet}/transactions"
        params = {"limit": 100}
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        raise TonAPIError(f"TonAPI returned HTTP {resp.status}")
                    payload = await resp.json()
        except TonAPIError:
            raise
        except Exception as exc:
            raise TonAPIError("TonAPI request failed") from exc

        txs = payload.get("transactions")
        if not isinstance(txs, list):
            raise TonAPIError("TonAPI response has no transaction list")
        required_nano = int(round(float(expected_amount) * NANO))
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            # Only finalized successful transactions with a complete inbound message.
            if tx.get("success") is not True or tx.get("aborted") is True:
                continue
            msg = tx.get("in_msg")
            if not isinstance(msg, dict) or not msg:
                continue
            destination = (msg.get("destination") or {})
            destination_address = destination.get("address") if isinstance(destination, dict) else destination
            if not self._same_address(destination_address, self.wallet):
                continue
            source = msg.get("source")
            source_address = source.get("address") if isinstance(source, dict) else source
            if not source_address:
                continue
            try:
                value = int(msg.get("value"))
            except (TypeError, ValueError):
                continue
            if value < required_nano:
                continue
            if self._comment(msg) != memo:
                continue
            tx_hash = self._hash(tx)
            if not tx_hash:
                continue
            return tx_hash
        return None

    async def check_transaction(self, memo: str, expected_amount: float) -> bool:
        return bool(await self.find_payment(memo, expected_amount))
