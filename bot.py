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

async def get_erc20_transfers(wallet: str, chain: str, hours: int) -> list:
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        url = f"https://deep-index.moralis.io/api/v2.2/{wallet}/erc20/transfers"
        headers = {"X-API-Key": MORALIS_KEY}
        params = {
            "chain": CHAINS[chain]["moralis_chain"],
            "limit": 100,
            "order": "DESC",
            "from_date": since.strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", [])
    except Exception as e:
        logger.error(f"Transfers error {wallet} {chain}: {e}")
    return []

def parse_swaps_from_transfers(transfers: list, wallet: str, hours: int) -> list:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    swaps = []
    seen_tx = set()

    for tx in transfers:
        try:
            to_addr = tx.get("to_address", "").lower()
            if to_addr != wallet.lower():
                continue

            symbol = tx.get("token_symbol", "").upper()
            if symbol not in STABLECOINS:
                continue

            block_time = tx.get("block_timestamp", "")
            if block_time:
                tx_time = datetime.fromisoformat(block_time.replace("Z", "+00:00"))
                if tx_time < since:
                    continue

            tx_hash = tx.get("transaction_hash", "")
            if tx_hash in seen_tx:
                continue
            seen_tx.add(tx_hash)

            decimals = int(tx.get("token_decimals", 6))
            raw_value = int(tx.get("value", 0))
            amount = raw_value / (10 ** decimals)

            if amount < 0.01:
                continue

            swaps.append({
                "tx_hash": tx_hash,
                "time": block_time,
                "bought_symbol": symbol,
                "bought_amount": amount,
                "wallet": wallet,
            })
        except Exception as e:
            logger.error(f"Parse error: {e}")
            continue

    return swaps

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *OKX Boost Tracker*\n\n"
        "Отслеживает продажи токенов в USDT/USDC по всем кошелькам.\n\n"
        "*Команды:*\n"
        "➕ /addwallet `адрес` — добавить один кошелёк\n"
        "➕➕ /addmany — добавить много кошельков сразу\n"
        "📋 /wallets — список кошельков\n"
        "❌ /removewallet `адрес` — удалить кошелёк\n"
        "🗑 /clearwallets — очистить всё\n"
        "🔍 /scan `часы` — найти свопы в USDT/USDC\n\n"
        "*Примеры:*\n"
        "`/scan 24` — за 24 часа\n"
        "`/scan 168` — за неделю"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def addwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = get_user_wallets(user_id)

    if not context.args:
        await update.message.reply_text(
            "❌ Укажи адрес.\nПример: `/addwallet 0x123...abc`",
            parse_mode="Markdown"
        )
        return

    address = context.args[0].strip()

    if is_evm_address(address):
        if address.lower() in [w.lower() for w in wallets["evm"]]:
            await update.message.reply_text("⚠️ Уже добавлен.")
            return
        wallets["evm"].append(address)
        update_user_wallets(user_id, wallets)
        total = len(wallets["evm"]) + len(wallets["solana"])
        await update.message.reply_text(f"✅ EVM кошелёк добавлен!\n📊 Всего: *{total}*", parse_mode="Markdown")
    elif is_solana_address(address):
        if address in wallets["solana"]:
            await update.message.reply_text("⚠️ Уже добавлен.")
            return
        wallets["solana"].append(address)
        update_user_wallets(user_id, wallets)
        total = len(wallets["evm"]) + len(wallets["solana"])
        await update.message.reply_text(f"✅ Solana кошелёк добавлен!\n📊 Всего: *{total}*", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Адрес не распознан.")

async def addmany(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = get_user_wallets(user_id)

    text = update.message.text
    lines = text.split("\n")[1:]
    addresses = [line.strip() for line in lines if line.strip()]

    if not addresses:
        await update.message.reply_text(
            "❌ Укажи адреса каждый на новой строке:\n\n"
            "`/addmany\n0x111...aaa\n0x222...bbb\n0x333...ccc`",
            parse_mode="Markdown"
        )
        return

    added = 0
    skipped = 0
    for address in addresses:
        if is_evm_address(address):
            if address.lower() not in [w.lower() for w in wallets["evm"]]:
                wallets["evm"].append(address)
                added += 1
            else:
                skipped += 1
        elif is_solana_address(address):
            if address not in wallets["solana"]:
                wallets["solana"].append(address)
                added += 1
            else:
                skipped += 1

    update_user_wallets(user_id, wallets)
    total = len(wallets["evm"]) + len(wallets["solana"])
    await update.message.reply_text(
        f"✅ Добавлено: *{added}*\n"
        f"⚠️ Пропущено (дубли): *{skipped}*\n"
        f"📊 Всего кошельков: *{total}*",
        parse_mode="Markdown"
    )

async def removewallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = get_user_wallets(user_id)

    if not context.args:
        await update.message.reply_text(
            "❌ Укажи адрес.\nПример: `/removewallet 0x123...abc`",
            parse_mode="Markdown"
        )
        return

    address = context.args[0].strip()
    removed = False

    new_evm = [w for w in wallets["evm"] if w.lower() != address.lower()]
    if len(new_evm) < len(wallets["evm"]):
        wallets["evm"] = new_evm
        removed = True

    new_sol = [w for w in wallets["solana"] if w != address]
    if len(new_sol) < len(wallets["solana"]):
        wallets["solana"] = new_sol
        removed = True

    if removed:
        update_user_wallets(user_id, wallets)
        total = len(wallets["evm"]) + len(wallets["solana"])
        await update.message.reply_text(f"✅ Удалён.\n📊 Осталось: *{total}*", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Такой кошелёк не найден.")

async def wallets_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = get_user_wallets(user_id)
    evm = wallets["evm"]
    sol = wallets["solana"]

    if not evm and not sol:
        await update.message.reply_text("📭 Кошельков нет. Добавь через /addwallet или /addmany")
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
    update_user_wallets(user_id, {"evm": [], "solana": []})
    await update.message.reply_text("🗑 Все кошельки удалены.")

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = get_user_wallets(user_id)

    hours = 24
    if context.args:
        try:
            hours = int(context.args[0])
        except:
            pass

    evm_wallets = wallets["evm"]
    if not evm_wallets:
        await update.message.reply_text("📭 Нет EVM кошельков. Добавь через /addmany")
        return

    msg = await update.message.reply_text(
        f"⏳ Сканирую *{len(evm_wallets)}* кошельков за *{hours}ч*...\n"
        f"Подожди до 60 секунд.",
        parse_mode="Markdown"
    )

    all_swaps = []
    total_usdt = 0.0
    total_usdc = 0.0

    for wallet in evm_wallets:
        for chain in CHAINS:
            transfers = await get_erc20_transfers(wallet, chain, hours)
            swaps = parse_swaps_from_transfers(transfers, wallet, hours)
            for s in swaps:
                s["chain"] = chain
                all_swaps.append(s)
                if s["bought_symbol"] == "USDT":
                    total_usdt += s["bought_amount"]
                elif s["bought_symbol"] == "USDC":
                    total_usdc += s["bought_amount"]

    if not all_swaps:
        await msg.edit_text(
            f"😶 За последние {hours}ч свопов в USDT/USDC не найдено.\n"
            f"Проверь кошельки: /wallets"
        )
        return

    all_swaps.sort(key=lambda x: x["time"], reverse=True)

    text = f"📊 *Свопы в USDT/USDC за {hours}ч*\n"
    text += f"🔍 Кошельков: {len(evm_wallets)} | Транзакций: {len(all_swaps)}\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    for s in all_swaps:
        chain_info = CHAINS[s["chain"]]
        w = s["wallet"]
        short_w = f"{w[:6]}...{w[-4:]}"
        try:
            dt = datetime.fromisoformat(s["time"].replace("Z", "+00:00"))
            time_str = dt.strftime("%d.%m %H:%M")
        except:
            time_str = "—"
        text += f"{chain_info['emoji']} `{short_w}` | {time_str}\n"
        text += f"💰 +*{s['bought_amount']:.2f} {s['bought_symbol']}*\n\n"

    text += "━━━━━━━━━━━━━━━━━━━━\n"
    if total_usdt > 0:
        text += f"💚 USDT: *{total_usdt:.2f}*\n"
    if total_usdc > 0:
        text += f"🔵 USDC: *{total_usdc:.2f}*\n"
    text += f"💰 *Всего: {total_usdt + total_usdc:.2f}*\n\n"

    text += "━━━━━━━━━━━━━━━━━━━━\n"
    text += "📋 *Копируй в Excel:*\n"
    for s in all_swaps:
        w = s["wallet"]
        short_w = f"{w[:6]}...{w[-4:]}"
        try:
            dt = datetime.fromisoformat(s["time"].replace("Z", "+00:00"))
            date_str = dt.strftime("%d.%m.%Y")
        except:
            date_str = "—"
        text += f"`{date_str}  {short_w}  {s['bought_amount']:.2f}  {s['bought_symbol']}`\n"

    await msg.edit_text(text, parse_mode="Markdown")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addwallet", addwallet))
    app.add_handler(CommandHandler("addmany", addmany))
    app.add_handler(CommandHandler("removewallet", removewallet))
    app.add_handler(CommandHandler("wallets", wallets_list))
    app.add_handler(CommandHandler("clearwallets", clearwallets))
    app.add_handler(CommandHandler("scan", scan))
    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
