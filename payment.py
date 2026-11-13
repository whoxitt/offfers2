"""Проверка входящих платежей TON через TonAPI."""
import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

log = logging.getLogger("payment")


class TonkeeperAPI:
    """Проверяет входящие переводы на кошелёк магазина по сумме и memo."""

    def __init__(self, wallet_address: str, api_key: str = ""):
        self.wallet = wallet_address.strip()
        self.base_url = "https://tonapi.io/v2"
        self.api_key = api_key.strip()

    @staticmethod
    def _message_text(in_msg: dict[str, Any]) -> str:
        """TonAPI в разных версиях отдаёт комментарий в разных полях."""
        candidates = [
            in_msg.get("message"),
            in_msg.get("comment"),
        ]
        decoded = in_msg.get("decoded_body") or in_msg.get("decodedBody") or {}
        if isinstance(decoded, dict):
            candidates.extend([decoded.get("text"), decoded.get("comment")])
        msg_data = in_msg.get("msg_data") or {}
        if isinstance(msg_data, dict):
            candidates.extend([msg_data.get("text"), msg_data.get("comment")])
            decoded_data = msg_data.get("decoded") or {}
            if isinstance(decoded_data, dict):
                candidates.extend([decoded_data.get("text"), decoded_data.get("comment")])
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    async def check_transaction(self, memo: str, expected_amount: float) -> bool:
        """Проверяет свежую историю кошелька. Ошибки API не маскируются под отсутствие оплаты."""
        url = f"{self.base_url}/blockchain/accounts/{self.wallet}/transactions"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = aiohttp.ClientTimeout(total=12, connect=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, params={"limit": 100}, headers=headers) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:500]
                        log.error("TonAPI HTTP %s: %s", resp.status, body)
                        raise RuntimeError(f"TonAPI вернул HTTP {resp.status}")
                    data = await resp.json(content_type=None)

            transactions = data.get("transactions", []) if isinstance(data, dict) else []
            expected_memo = str(memo).strip()
            expected = Decimal(str(expected_amount))
            for tx in transactions:
                in_msg = tx.get("in_msg") or {}
                if not isinstance(in_msg, dict) or not in_msg:
                    continue
                actual_memo = self._message_text(in_msg)
                if actual_memo != expected_memo:
                    continue
                raw_value = in_msg.get("value", 0)
                try:
                    actual_amount = Decimal(str(raw_value)) / Decimal("1000000000")
                except (InvalidOperation, TypeError, ValueError):
                    log.warning("Invalid TON value in transaction: %r", raw_value)
                    continue
                if actual_amount >= expected:
                    log.info("TON payment found: memo=%s amount=%s TON", expected_memo, actual_amount)
                    return True
                log.warning("Memo matched but amount is too small: %s < %s TON", actual_amount, expected)
            log.info("TON payment not found: memo=%s", expected_memo)
            return False
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError):
            log.exception("TonAPI payment check failed")
            raise

    async def wait_for_transaction(self, memo: str, expected_amount: float, timeout: int = 600) -> bool:
        """Ожидание оплаты с повторной проверкой."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        while loop.time() - start < timeout:
            if await self.check_transaction(memo, expected_amount):
                return True
            await asyncio.sleep(3)
        return False
