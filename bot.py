import os
import logging
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MORALIS_KEY = os.environ.get("MORALIS_KEY")
HELIUS_KEY = os.environ.get("HELIUS_KEY")

CHAINS = {
    "bsc": {"name": "BSC", "emoji": "🟡", "moralis_chain": "0x38"},
    "base": {"name": "Base", "emoji": "🔵", "moralis_chain": "0x2105"},
    "arbitrum": {"name": "Arbitrum", "emoji": "🔷", "moralis_chain": "0xa4b1"},
}

# Хранилище адресов (в памяти, сбрасывается при рестарте)
user_wallets = {}  # {user_id: {"evm": [...], "solana": [...]}}

def get_user_wallets(user_id):
    if user_id not in user_wallets:
        user_wallets[user_id] = {"evm": [], "solana": []}
    return user_wallets[user_id]

def is_solana_address(address):
    return len(address) >= 32 and len(address) <= 44 and not address.startswith("0x")

def is_evm_address(address):
    return address.startswith("0x") and len(address) == 42

async def get_token_price_usd(token_address: str, chain: str) -> float:
    """Получить цену токена в USD через Moralis"""
    try:
        url = f"https://deep-index.moralis.io/api/v2.2/erc20/{token_address}/price"
        headers = {"X-API-Key": MORALIS_KEY}
        params = {"chain": CHAINS[chain]["moralis_chain"]}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get("usdPrice", 0))
    except:
        pass
    return 0.0

async def get_evm_token_balance(wallet: str, token_address: str, chain: str) -> dict:
    """Получить баланс ERC20 токена на EVM кошельке"""
    try:
        url = f"https://deep-index.moralis.io/api/v2.2/{wallet}/erc20"
        headers = {"X-API-Key": MORALIS_KEY}
        params = {
            "chain": CHAINS[chain]["moralis_chain"],
            "token_addresses[]": token_address
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        token = data[0]
                        decimals = int(token.get("decimals", 18))
                        raw_balance = int(token.get("balance", 0))
                        balance = raw_balance / (10 ** decimals)
                        return {
                            "balance": balance,
                            "symbol": token.get("symbol", "???"),
                            "name": token.get("name", "Unknown")
                        }
    except Exception as e:
        logger.error(f"EVM balance error: {e}")
    return {"balance": 0.0, "symbol": "???", "name": "Unknown"}

async def get_solana_token_balance(wallet: str, token_mint: str) -> dict:
    """Получить баланс SPL токена на Solana кошельке"""
    try:
        url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                wallet,
                {"mint": token_mint},
                {"encoding": "jsonParsed"}
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    if accounts:
                        info = accounts[0]["account"]["data"]["parsed"]["info"]
                        amount = float(info["tokenAmount"]["uiAmount"] or 0)
                        return {"balance": amount, "symbol": "SPL", "name": "Solana Token"}
    except Exception as e:
        logger.error(f"Solana balance error: {e}")
    return {"balance": 0.0, "symbol": "SPL", "name": "Unknown"}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *OKX Boost Tracker*\n\n"
        "Бот для массовой проверки балансов токенов по всем твоим кошелькам.\n\n"
        "*Команды:*\n"
        "➕ /addwallet `адрес` — добавить кошелёк\n"
        "📋 /wallets — список всех кошельков\n"
        "🗑 /clearwallets — очистить все кошельки\n"
        "🔍 /check `адрес_токена` `сеть` — проверить балансы\n\n"
        "*Сети:* `bsc` `base` `arbitrum` `solana`\n\n"
        "*Пример:*\n"
        "`/check 0xabc...123 bsc`\n"
        "`/check So1ana...mint solana`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def addwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = get_user_wallets(user_id)

    if not context.args:
        await update.message.reply_text("❌ Укажи адрес кошелька.\nПример: `/addwallet 0x123...abc`", parse_mode="Markdown")
        return

    address = context.args[0].strip()

    if is_evm_address(address):
        if address.lower() in [w.lower() for w in wallets["evm"]]:
            await update.message.reply_text("⚠️ Этот EVM кошелёк уже добавлен.")
            return
        wallets["evm"].append(address)
        total = len(wallets["evm"]) + len(wallets["solana"])
        await update.message.reply_text(f"✅ EVM кошелёк добавлен!\n📊 Всего кошельков: {total}")

    elif is_solana_address(address):
        if address in wallets["solana"]:
            await update.message.reply_text("⚠️ Этот Solana кошелёк уже добавлен.")
            return
        wallets["solana"].append(address)
        total = len(wallets["evm"]) + len(wallets["solana"])
        await update.message.reply_text(f"✅ Solana кошелёк добавлен!\n📊 Всего кошельков: {total}")

    else:
        await update.message.reply_text("❌ Адрес не распознан. Проверь правильность.")

async def wallets_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = get_user_wallets(user_id)

    evm = wallets["evm"]
    sol = wallets["solana"]

    if not evm and not sol:
        await update.message.reply_text("📭 Кошельков нет. Добавь через /addwallet")
        return

    text = "📋 *Твои кошельки:*\n\n"
    if evm:
        text += f"*EVM ({len(evm)} шт.):*\n"
        for i, w in enumerate(evm, 1):
            text += f"`{i}. {w[:8]}...{w[-6:]}`\n"
    if sol:
        text += f"\n*Solana ({len(sol)} шт.):*\n"
        for i, w in enumerate(sol, 1):
            text += f"`{i}. {w[:8]}...{w[-6:]}`\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def clearwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_wallets[user_id] = {"evm": [], "solana": []}
    await update.message.reply_text("🗑 Все кошельки удалены.")

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = get_user_wallets(user_id)

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Укажи адрес токена и сеть.\n"
            "Пример: `/check 0xabc...123 bsc`\n"
            "Сети: `bsc` `base` `arbitrum` `solana`",
            parse_mode="Markdown"
        )
        return

    token_address = context.args[0].strip()
    chain = context.args[1].strip().lower()

    if chain not in CHAINS and chain != "solana":
        await update.message.reply_text("❌ Неизвестная сеть. Используй: `bsc` `base` `arbitrum` `solana`", parse_mode="Markdown")
        return

    evm_wallets = wallets["evm"]
    sol_wallets = wallets["solana"]

    if chain == "solana" and not sol_wallets:
        await update.message.reply_text("📭 Нет Solana кошельков. Добавь через /addwallet")
        return

    if chain in CHAINS and not evm_wallets:
        await update.message.reply_text("📭 Нет EVM кошельков. Добавь через /addwallet")
        return

    msg = await update.message.reply_text("⏳ Проверяю балансы по всем кошелькам...")

    results = []
    token_symbol = "???"
    total_balance = 0.0

    if chain in CHAINS:
        # Получаем цену токена
        price_usd = await get_token_price_usd(token_address, chain)
        chain_info = CHAINS[chain]

        tasks = [get_evm_token_balance(w, token_address, chain) for w in evm_wallets]
        balances = await asyncio.gather(*tasks)

        for wallet, bal_data in zip(evm_wallets, balances):
            if bal_data["balance"] > 0:
                token_symbol = bal_data["symbol"]
                total_balance += bal_data["balance"]
                usd_value = bal_data["balance"] * price_usd
                results.append({
                    "wallet": wallet,
                    "balance": bal_data["balance"],
                    "usd": usd_value,
                    "symbol": bal_data["symbol"]
                })

        # Формируем ответ
        chain_emoji = chain_info["emoji"]
        chain_name = chain_info["name"]
        text = f"{chain_emoji} *{chain_name} — {token_symbol}*\n"
        text += f"`{token_address[:10]}...{token_address[-8:]}`\n"
        if price_usd > 0:
            text += f"💲 Цена: ${price_usd:.6f}\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n"

        if not results:
            text += "😶 Баланс 0 на всех кошельках\n"
        else:
            for r in results:
                w = r["wallet"]
                short = f"{w[:6]}...{w[-4:]}"
                text += f"💼 `{short}` → *{r['balance']:.4f}* {r['symbol']}"
                if r["usd"] > 0:
                    text += f" (≈${r['usd']:.2f})"
                text += "\n"

        text += "━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📦 *Итого:* {total_balance:.4f} {token_symbol}\n"
        if price_usd > 0:
            text += f"💵 *В USD:* ≈${total_balance * price_usd:.2f}\n"

        text += f"\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"

    elif chain == "solana":
        tasks = [get_solana_token_balance(w, token_address) for w in sol_wallets]
        balances = await asyncio.gather(*tasks)

        text = f"◎ *Solana — SPL Token*\n"
        text += f"`{token_address[:10]}...{token_address[-8:]}`\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n"

        for wallet, bal_data in zip(sol_wallets, balances):
            if bal_data["balance"] > 0:
                total_balance += bal_data["balance"]
                w = wallet
                short = f"{w[:6]}...{w[-4:]}"
                text += f"💼 `{short}` → *{bal_data['balance']:.4f}* SPL\n"

        if total_balance == 0:
            text += "😶 Баланс 0 на всех кошельках\n"

        text += "━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📦 *Итого:* {total_balance:.4f}\n"
        text += f"\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"

    # Кнопка для копирования в Excel
    keyboard = [[InlineKeyboardButton("📊 Строка для Excel", callback_data=f"excel_{chain}_{token_address}_{total_balance:.4f}_{token_symbol}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await msg.edit_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def excel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_", 4)
    if len(parts) < 5:
        return

    _, chain, token_addr, total, symbol = parts
    date_str = datetime.now().strftime("%d.%m.%Y")

    text = (
        f"📊 *Строка для Excel:*\n\n"
        f"`{date_str}\t{symbol}\t{total}\t\t\t\t`\n\n"
        f"Скопируй и вставь в таблицу.\n"
        f"Заполни: цену, сумму USDT, биржу, примечание."
    )
    await query.message.reply_text(text, parse_mode="Markdown")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addwallet", addwallet))
    app.add_handler(CommandHandler("wallets", wallets_list))
    app.add_handler(CommandHandler("clearwallets", clearwallets))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CallbackQueryHandler(excel_callback, pattern="^excel_"))
    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
