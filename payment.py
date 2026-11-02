"""Модуль для обработки платежей через CryptoBot и Tonkeeper"""
import asyncio
import aiohttp
import logging
from typing import Optional

log = logging.getLogger("payment")


class CryptoBotAPI:
    """API для работы с CryptoBot"""
    
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://pay.crypt.bot/api"
        
    async def create_invoice(self, amount: float, currency: str = "GRAM", description: str = "Оплата доступа к OfferBot") -> Optional[dict]:
        """Создать счёт для оплаты"""
        url = f"{self.base_url}/createInvoice"
        headers = {"Crypto-Pay-API-Token": self.token}
        
        payload = {
            "asset": currency,
            "amount": str(amount),
            "description": description,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            return data.get("result")
                    log.error(f"CryptoBot API error: {resp.status} - {await resp.text()}")
        except Exception as e:
            log.exception(f"CryptoBot API exception: {e}")
        return None
    
    async def check_invoice(self, invoice_id: int) -> Optional[dict]:
        """Проверить статус счёта"""
        url = f"{self.base_url}/getInvoices"
        headers = {"Crypto-Pay-API-Token": self.token}
        
        payload = {"invoice_ids": str(invoice_id)}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            items = data.get("result", {}).get("items", [])
                            if items:
                                return items[0]
                    log.error(f"CryptoBot check error: {resp.status} - {await resp.text()}")
        except Exception as e:
            log.exception(f"CryptoBot check exception: {e}")
        return None


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
