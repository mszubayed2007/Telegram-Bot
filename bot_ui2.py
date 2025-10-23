# bot_ui.py
# Telegram bot with SQLite persistence for balance, referrals, verify, and wallet.

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import Forbidden, BadRequest

# ========= ENV & LOG =========
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN") or "8441553260:AAGj5WrkESVXmgzHdHdW5Oms_kN67i6hnuE"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========= SETTINGS =========
CHANNEL_USERNAME = "@gmailcreators01"
CHANNELS = [CHANNEL_USERNAME]

LINKS = {
    "A": ("👉 Click Here 1", "https://www.effectivegatecpm.com/yv4n27irvw?key=f4f4ca520e242b41029f8e397fe59d19"),
    "B": ("👉 Click Here 2", "https://www.effectivegatecpm.com/s0krpg9b?key=4e464b46b58006e33871139397b970d9"),
}

MIN_WITHDRAW_BALANCE = 800     # উত্তোলনের ন্যূনতম ব্যালেন্স
REFER_BONUS = 50               # প্রতি বৈধ রেফারে বোনাস

# ========= DB =========
DB_PATH = os.getenv("DB_PATH") or "bot.db"

def db_connect():
    # নতুন কানেকশন খুলে কাজ শেষ হলে বন্ধ করছি; ছোট অপারেশনে এটা সেফ ও সহজ
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with closing(db_connect()) as conn, conn, closing(conn.cursor()) as cur:
        # কিছু সেফটি PRAGMA
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id        INTEGER PRIMARY KEY,
            balance        REAL    NOT NULL DEFAULT 0,
            ref_by         INTEGER,
            verified       INTEGER NOT NULL DEFAULT 0,  -- 0/1
            rewarded       INTEGER NOT NULL DEFAULT 0,  -- রেফার বোনাস পেয়েছে? (referred user perspective)
            referrals      INTEGER NOT NULL DEFAULT 0,  -- কতজন verify করিয়েছে
            ref_earned     INTEGER NOT NULL DEFAULT 0,  -- রেফার থেকে মোট আয়
            wallet_type    TEXT,
            wallet_number  TEXT,
            created_at     TEXT,
            updated_at     TEXT
        )
        """)
        conn.commit()

def now_iso():
    return datetime.utcnow().isoformat()

def ensure_user(uid: int):
    with closing(db_connect()) as conn, conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT 1 FROM users WHERE user_id=?", (uid,))
        if cur.fetchone() is None:
            cur.execute("""
                INSERT INTO users (user_id, balance, verified, rewarded, referrals, ref_earned, created_at, updated_at)
                VALUES (?, 0, 0, 0, 0, 0, ?, ?)
            """, (uid, now_iso(), now_iso()))
            conn.commit()

def set_ref_by_if_empty(uid: int, ref_id: int):
    if uid == ref_id:
        return
    with closing(db_connect()) as conn, conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT ref_by FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        if row and row["ref_by"] is None:
            cur.execute(
                "UPDATE users SET ref_by=?, updated_at=? WHERE user_id=?",
                (ref_id, now_iso(), uid)
            )
            conn.commit()

def mark_verified_and_reward(uid: int) -> int | None:
    """
    ইউজারকে verified করে। যদি প্রথমবার verify হয় এবং valid ref_by থাকে ও এখনো rewarded না হয়ে থাকে,
    তাহলে রেফারারকে REFER_BONUS ক্রেডিট করে। সফল হলে referrer_id রিটার্ন করে, নইলে None।
    """
    with closing(db_connect()) as conn, conn, closing(conn.cursor()) as cur:
        # ইউজারের বর্তমান স্টেট
        cur.execute("SELECT verified, rewarded, ref_by FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        if row is None:
            return None

        verified, rewarded, ref_by = int(row["verified"]), int(row["rewarded"]), row["ref_by"]

        # verified মার্ক করি (idempotent)
        if verified == 0:
            cur.execute(
                "UPDATE users SET verified=1, updated_at=? WHERE user_id=?",
                (now_iso(), uid)
            )

        # রেফার বোনাস দেওয়ার শর্ত
        if ref_by and rewarded == 0:
            # রেফারারকে টাকা দিন
            cur.execute("SELECT balance, referrals, ref_earned FROM users WHERE user_id=?", (ref_by,))
            ref = cur.fetchone()
            if ref:
                new_balance = (ref["balance"] or 0) + REFER_BONUS
                new_referrals = (ref["referrals"] or 0) + 1
                new_ref_earned = (ref["ref_earned"] or 0) + REFER_BONUS

                cur.execute("""
                    UPDATE users
                    SET balance=?, referrals=?, ref_earned=?, updated_at=?
                    WHERE user_id=?
                """, (new_balance, new_referrals, new_ref_earned, now_iso(), ref_by))

                # referred user-এ rewarded=True
                cur.execute(
                    "UPDATE users SET rewarded=1, updated_at=? WHERE user_id=?",
                    (now_iso(), uid)
                )
                conn.commit()
                return ref_by

        conn.commit()
        return None

def get_balance(uid: int) -> float:
    with closing(db_connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        return float(row["balance"]) if row else 0.0

def get_stats(uid: int) -> tuple[int, int]:
    with closing(db_connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT referrals, ref_earned FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        if row:
            return int(row["referrals"]), int(row["ref_earned"])
        return 0, 0

def set_wallet(uid: int, wtype: str, wnum: str):
    with closing(db_connect()) as conn, conn, closing(conn.cursor()) as cur:
        cur.execute("""
            UPDATE users SET wallet_type=?, wallet_number=?, updated_at=?
            WHERE user_id=?
        """, (wtype, wnum, now_iso(), uid))
        if cur.rowcount == 0:
            # edge: user yet not ensured
            cur.execute("""
                INSERT INTO users (user_id, wallet_type, wallet_number, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (uid, wtype, wnum, now_iso(), now_iso()))
        conn.commit()

def get_wallet(uid: int) -> tuple[str | None, str | None]:
    with closing(db_connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT wallet_type, wallet_number FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        if row:
            return row["wallet_type"], row["wallet_number"]
        return None, None

# ========= UI HELPERS =========
def build_inline_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📢 জয়েন অফিশিয়াল চ্যানেল", url="https://t.me/gmailcreators01")],
        [InlineKeyboardButton(LINKS["A"][0], callback_data="link:A")],
        [InlineKeyboardButton(LINKS["B"][0], callback_data="link:B")],
        [InlineKeyboardButton("✅ Verify", callback_data="verify")],
    ]
    return InlineKeyboardMarkup(rows)

def build_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("🧮 My Balance"), KeyboardButton("🌿 Refer & Earn")],
            [KeyboardButton("🏧 Withdraw"),   KeyboardButton("⚠️ Rules")],
            [KeyboardButton("💼 Set Wallet"), KeyboardButton("🏆 Stats")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=True,
    )

def wallet_provider_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Bkash", callback_data="wallet:bkash")],
        [InlineKeyboardButton("📦 Nagad", callback_data="wallet:nagad")],
        [InlineKeyboardButton("↩️ Back", callback_data="wallet:back")],
    ])

def withdraw_request_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Withdraw Request", callback_data="withdraw:request")]
    ])

async def my_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    me = await context.bot.get_me()
    return me.username

# ========= COMMANDS =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    uid = user.id
    ensure_user(uid)

    # Parse /start <ref_id>
    ref_id = None
    if context.args:
        try:
            ref_id = int(context.args[0])
        except (ValueError, TypeError):
            ref_id = None

    if ref_id and ref_id != uid:
        set_ref_by_if_empty(uid, ref_id)

    # UI flags (per session)
    context.user_data.setdefault("clickedA", False)
    context.user_data.setdefault("clickedB", False)
    context.user_data.setdefault("awaiting_wallet_number", False)

    text = (
        "🎉 *Free Refer Free Earn bot* এ আপনাকে স্বাগতম!\n\n"
        f"💵 Join+Verify হলে রেফারার পাবেন {REFER_BONUS} টাকা বোনাস।\n"
        f"👥 প্রতি রেফারে {REFER_BONUS} টাকা (রেফার ইউজার Verify করলে)।\n"
        f"🏧 {MIN_WITHDRAW_BALANCE} টাকা হলে নগদ/বিকাশে Withdraw করতে পারবেন।\n\n"
        "➡️ প্রথমে চ্যানেলে Join করুন, তারপর দুইটি বাটন একবার করে চাপুন, এরপর ✅ Verify।"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=build_inline_menu(), parse_mode="Markdown")
        await update.message.reply_text("মেইন মেনু থেকে একটি অপশন বাছাই করুন:", reply_markup=build_reply_kb())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "কমান্ডসমূহ:\n/start - বট চালু\n/help - সহায়তা\nমেনু থেকে অপশন সিলেক্ট করুন।"
    )

# ========= LINK CLICK TRACKING =========
async def link_clicked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    which = q.data.split(":")[1]  # "A"/"B"
    title, url = LINKS[which]
    context.user_data[f"clicked{which}"] = True
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔓 Open Link", url=url)]])
    await q.message.reply_text(f"{title} চাপা হয়েছে ✅\nএখন Open করে দেখুন:", reply_markup=kb)

# ========= VERIFY =========
async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    uid = user.id
    ensure_user(uid)

    not_joined = []
    for ch in CHANNELS:
        try:
            m = await context.bot.get_chat_member(chat_id=ch, user_id=uid)
            if getattr(m, "status", None) not in ("member", "administrator", "creator"):
                not_joined.append(ch)
        except (Forbidden, BadRequest) as e:
            logger.warning("Cannot check %s: %s", ch, e)
            not_joined.append(ch)

    clickedA = bool(context.user_data.get("clickedA"))
    clickedB = bool(context.user_data.get("clickedB"))

    problems = []
    if not_joined: problems.append("• চ্যানেলে Join করেননি")
    if not clickedA: problems.append("• Click Here 1 একবার চাপেননি")
    if not clickedB: problems.append("• Click Here 2 একবার চাপেননি")

    if problems:
        await q.edit_message_text("❌ Verification ব্যর্থ। সম্পূর্ণ করুন:\n" + "\n".join(problems),
                                  reply_markup=build_inline_menu())
        return

    referrer_id = mark_verified_and_reward(uid)

    # রেফারারকে নোটিফাই (যদি বোনাস গেছে)
    if referrer_id:
        try:
            await context.bot.send_message(
                chat_id=referrer_id,
                text=f"🎉 আপনার রেফার *{user.first_name or uid}* Verify করেছে!\n➕ {REFER_BONUS} ৳ আপনার ব্যালেন্সে যোগ হয়েছে।",
                parse_mode="Markdown"
            )
        except Forbidden:
            pass  # বট ব্লক করা থাকলে ইগনোর

    await q.edit_message_text("✅ Verification successful! সব শর্ত পূরণ হয়েছে।")

# ========= MENU TEXTS =========
async def on_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    ensure_user(uid)
    text = (update.message.text or "").strip().lower()

    if "balance" in text:
        bal = get_balance(uid)
        await update.message.reply_text(f"আপনার ব্যালেন্স: {bal:.2f} ৳")

    elif "refer" in text:
        uname = await my_username(context)
        link = f"https://t.me/{uname}?start={uid}"
        await update.message.reply_text(
            "আপনার রেফার লিংক (রেফার্ড ইউজার Verify করলে আপনি ৫০৳ পাবেন):\n" + link
        )

    elif "withdraw" in text:
        await handle_withdraw_menu(update, context)

    elif "rules" in text:
        await update.message.reply_text("❌ নিয়ম মেনে কাজ করলে এই Bot থেকে আপনি ১০০% পেমেন্ট পাবেন 🚫 এই Bot এ যদি কোন প্রকার ফেক রেফার করেন? তাহলে পেমেন্ট পাবেন না, সুতরাং ফেক রেফার করবেন না।  📝 আর পেমেন্ট নিয়ে কেউ চিন্তা করবেন না। সর্বোচ্চ ২৪ ঘণ্টার ভিতরে পেমেন্ট পেয়ে যাবেন। ♻ আর এখান থেকে শুধু মাএ বিকাশ নগদ এ পেমেন্ট নিতে পারবেন। কেউ যদি ভুল নাম্বার এড করে উইথড্র দেন? তাহলে আপনার টাকা মার যাবে। ঐটা আর ফিরে পাবেন না ❌ss")

    elif "wallet" in text:
        await update.message.reply_text("আপনার পেমেন্ট মেথড সিলেক্ট করুন:", reply_markup=wallet_provider_kb())

    elif "stats" in text:
        refs, earned = get_stats(uid)
        await update.message.reply_text(
            f"আপনার স্ট্যাটস:\nমোট রেফার (Verified): {refs}\nরেফার ইনকাম: {earned} ৳"
        )

    else:
        await update.message.reply_text("একটি বৈধ অপশন বাছাই করুন। /help দেখুন।")




# ========= WALLET FLOW =========
async def wallet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, action = q.data.split(":", 1)
    uid = q.from_user.id
    ensure_user(uid)

    if action == "back":
        await q.edit_message_text("মেথড বাছাই করুন:", reply_markup=wallet_provider_kb())
        return

    if action in ("bkash", "nagad"):
        context.user_data["wallet_type"] = action
        context.user_data["awaiting_wallet_number"] = True
        prompt = "Bkash Account Number: 01********" if action == "bkash" else "Nagad Account Number: 01********"
        await q.message.reply_text(f"দয়া করে আপনার {prompt}")
        return

# capture wallet number — কেবল যখন আমরা নম্বর চাইছি
async def capture_wallet_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get("awaiting_wallet_number"):
        return await on_menu_text(update, context)

    uid = update.effective_user.id
    ensure_user(uid)

    number = (update.message.text or "").strip()
    provider = context.user_data.get("wallet_type")

    if not (number.isdigit() and len(number) == 11 and number.startswith("01")):
        await update.message.reply_text("ভুল নম্বর! ১১ ডিজিটের 01******** ফরম্যাটে দিন।")
        return

    set_wallet(uid, provider, number)
    context.user_data["awaiting_wallet_number"] = False

    nice_name = "Bkash" if provider == "bkash" else "Nagad"
    await update.message.reply_text(
        f"✅ আপনার নাম্বারটি (𝐖𝐢𝐭𝐡𝐝𝐫𝐚𝐰) অপশনে সেভ করা হয়েছে।\n"
        f"Method: {nice_name}\nAccount: {number}\n\n"
        f"আপনার ব্যালেন্সে যদি {MIN_WITHDRAW_BALANCE} টাকা থাকে তাহলে আপনি টাকা উত্তোলন করতে পারবেন ✅"
    )

# ========= WITHDRAW FLOW =========
async def handle_withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    ensure_user(uid)
    bal = get_balance(uid)
    wtype, wnum = get_wallet(uid)

    if not (wtype and wnum):
        await update.message.reply_text("আগে 💼 Set Wallet থেকে Bkash/Nagad সিলেক্ট করে নম্বর সেভ করুন।")
        return

    nice_name = "Bkash" if wtype == "bkash" else "Nagad"
    if bal >= MIN_WITHDRAW_BALANCE:
        await update.message.reply_text(
            f"আপনার সেভ করা অ্যাকাউন্ট:\n"
            f"Method: {nice_name}\nAccount: {wnum}\n\n"
            f"ব্যালেন্স: {bal:.2f} ৳\n"
            f"{MIN_WITHDRAW_BALANCE} ৳ বা তার বেশি হলে Withdraw করতে পারবেন।",
            reply_markup=withdraw_request_kb()
        )
    else:
        await update.message.reply_text(
            f"আপনার সেভ করা অ্যাকাউন্ট:\n"
            f"Method: {nice_name}\nAccount: {wnum}\n\n"
            f"বর্তমান ব্যালেন্স: {bal:.2f} ৳ — কমপক্ষে {MIN_WITHDRAW_BALANCE} ৳ হলে Withdraw করতে পারবেন।"
        )

async def withdraw_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid)
    bal = get_balance(uid)

    if bal < MIN_WITHDRAW_BALANCE:
        await q.message.reply_text(f"ব্যালেন্স যথেষ্ট নয়। কমপক্ষে {MIN_WITHDRAW_BALANCE} ৳ প্রয়োজন।")
        return

    # TODO: রিকোয়েস্ট DB-তে আলাদা টেবিলে সেভ করুন (withdraw_requests)
    await q.message.reply_text(
        "♻ Sᴛᴀᴛᴜs :- Pᴇɴᴅɪɴɢ ⏳\n"
        "⏰ Pᴀʏᴍᴇɴᴛ Tɪᴍᴇ :- 24hours"
    )

# ========= FALLBACK & ERRORS =========
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        if context.user_data.get("awaiting_wallet_number"):
            await update.message.reply_text("দয়া করে সঠিক ১১ ডিজিটের মোবাইল নম্বর দিন (01********)।")
            return
        await update.message.reply_text("আমি আপনার মেসেজটা বুঝিনি। /help দেখুন বা মেনু থেকে বাছাই করুন।")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)

# ========= APP =========
def main() -> None:
    # DB প্রস্তুত
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CallbackQueryHandler(link_clicked, pattern=r"^link:(A|B)$"))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^verify$"))
    app.add_handler(CallbackQueryHandler(wallet_callback, pattern=r"^wallet:(bkash|nagad|back)$"))

    # wallet number input (only 11-digit, private chat)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND & filters.Regex(r"^01\d{9}$"),
        capture_wallet_number
    ))

    # Menu actions
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        on_menu_text
    ))

    app.add_handler(CallbackQueryHandler(withdraw_request_callback, pattern=r"^withdraw:request$"))

    app.add_handler(MessageHandler(filters.ALL, fallback))
    app.add_error_handler(error_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
# To run: python bot_ui.py
