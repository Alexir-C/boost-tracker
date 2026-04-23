import os
import json
import logging
import aiohttp
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MORALIS_KEY = os.environ.get("MORALIS_KEY")
HELIUS_KEY = os.environ.get("HELIUS_KEY")

WALLETS_FILE = "wallets.json"

CHAINS = {
    "bsc":      {"name": "BSC",      "emoji": "🟡", "moralis_chain": "0x38"},
    "base":     {"name": "Base",     "emoji": "🔵", "moralis_chain": "0x2105"},
    "arbitrum": {"name": "Arbitrum", "emoji": "🔷", "moralis_chain": "0xa4b1"},
    "eth":      {"name": "Ethereum", "emoji": "⚪", "moralis_chain": "0x1"},
}

STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "FDUSD", "USDE", "TUSD"}

# Контрактные адреса USDT/USDC на разных сетях
STABLE_CONTRACTS = {
    # BSC
    "0x55d398326f99059ff775485246999027b3197955": ("USDT", "bsc"),
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", "bsc"),
    # Base
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", "base"),
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": ("USDT", "base"),
    # Arbitrum
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("USDT", "arbitrum"),
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", "arbitrum"),
    # Ethereum
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", "eth"),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", "eth"),
}

def load_wallets() -> dict:
    if os.path.exists(WALLETS_FILE):
        try:
            with open(WALLETS_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_wallets(data: dict):
    with open(WALLETS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_user_wallets(user_id: int) -> dict:
    data = load_wallets()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"evm": [], "solana": []}
        save_wallets(data)
    return data[uid]

def update_user_wallets(user_id: int, wallets: dict):
    data = load_wallets()
    data[str(user_id)] = wallets
    save_wallets(data)

def is_evm_address(address: str) -> bool:
    return address.startswith("0x") and len(address) == 42

def is_solana_address(address: str) -> bool:
    return len(address) >= 32 and len(address) <= 44 and not address.startswith("0x")

async def get_wallet_token_transfers(wallet: str, chain_id: str, hours: int) -> list:
    """Получить все ERC20 transfers через Moralis v2"""
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        url = f"https://deep-index.moralis.io/api/v2.2/{wallet}/erc20/transfers"
        headers = {"X-API-Key": MORALIS_KEY}
        params = {
            "chain": chain_id,
            "limit": 100,
            "order": "DESC",
            "from_date": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", [])
                else:
                    text = await resp.text()
                    logger.error(f"API error {resp.status}: {text[:200]}")
    except Exception as e:
        logger.error(f"Transfer fetch error {wallet} {chain_id}: {e}")
    return []

def find_stables_received(transfers: list, wallet: str, hours: int) -> list:
    """Находим входящие стейблкоины по контрактным адресам"""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    results = []
    seen = set()

    for tx in transfers:
        try:
            to_addr = (tx.get("to_address") or "").lower()
            if to_addr != wallet.lower():
                continue

            contract = (tx.get("address") or "").lower()
            symbol_upper = (tx.get("token_symbol") or "").upper()

            # Проверяем по символу ИЛИ по контракту
            is_stable = symbol_upper in STABLECOINS or contract in STABLE_CONTRACTS

            if not is_stable:
                continue

            # Определяем символ
            if symbol_upper in STABLECOINS:
                symbol = symbol_upper
            else:
                symbol = STABLE_CONTRACTS.get(contract, ("STABLE", ""))[0]

            block_time = tx.get("block_timestamp", "")
            if block_time:
                tx_time = datetime.fromisoformat(block_time.replace("Z", "+00:00"))
                if tx_time < since:
                    continue

            tx_hash = tx.get("transaction_hash", "")
            if tx_hash in seen:
                continue
            seen.add(tx_hash)

            decimals = int(tx.get("token_decimals") or 6)
            raw_value = int(tx.get("value") or 0)
            amount = raw_value / (10 ** decimals)

            if amount < 0.01:
                continue

            results.append({
                "tx_hash": tx_hash,
                "time": block_time,
                "symbol": symbol,
                "amount": amount,
                "wallet": wallet,
