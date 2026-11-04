"""Проверка платежей TON через TonAPI."""
import asyncio
import aiohttp
import logging
from typing import Optional

log = logging.getLogger("payment")


class TonkeeperAPI:
    """API для проверки транзакций TON через tonapi.io"""
    
    def __init__(self, wallet_address: str):
        self.wallet = wallet_address
        self.base_url = "https://tonapi.io/v2"
        
    async def check_transaction(self, memo: str, expected_amount: float) -> bool:
        """Проверить есть ли транзакция с данным memo и суммой"""
        url = f"{self.base_url}/blockchain/accounts/{self.wallet}/transactions"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params={"limit": 100}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        transactions = data.get("transactions", [])
                        
                        for tx in transactions:
                            # Проверяем входящие транзакции
                            in_msg = tx.get("in_msg", {})
                            if not in_msg:
                                continue
                                
                            # Проверяем memo (comment)
                            message = in_msg.get("message", "")
                            if message != memo:
                                continue
                            
                            # Проверяем сумму (в нанограммах, 1 TON = 10^9 nanoTON)
                            value = int(in_msg.get("value", 0))
                            amount_ton = value / 1_000_000_000
                            
                            if amount_ton >= expected_amount:
                                log.info(f"✅ Transaction found: memo={memo}, amount={amount_ton} TON")
                                return True
                        
                        log.info(f"❌ Transaction not found: memo={memo}")
                        return False
                    else:
                        log.error(f"TonAPI error: {resp.status} - {await resp.text()}")
        except Exception as e:
            log.exception(f"TonAPI exception: {e}")
        return False
    
    async def wait_for_transaction(self, memo: str, expected_amount: float, timeout: int = 600) -> bool:
        """Ждать транзакцию с таймаутом (по умолчанию 10 минут)"""
        start_time = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if await self.check_transaction(memo, expected_amount):
                return True
            await asyncio.sleep(10)  # Проверяем каждые 10 секунд
        
        return False
