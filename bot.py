import asyncio
import json
import logging
import os
import re
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from fastapi import FastAPI
from numbers_parser import Document
from openpyxl import load_workbook
from rapidfuzz import fuzz

# Назви листів, які варто ігнорувати автоматично — типові "службові"
# сторінки, які деякі конвертери .numbers → .xlsx додають самі
# (звіт про конвертацію тощо). Якщо після завантаження побачиш в
# /categories зайву назву, яку тут нема — просто допиши сюди.
JUNK_SHEET_KEYWORDS = [
    "summary",
    "conversion",
    "readme",
    "report",
    "info",
    "звіт",
    "підсумок",
    "конвертац",
]

# ──────────────────────────────────────────────────────────────
# Налаштування
# ──────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shop-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Не задано змінну середовища BOT_TOKEN. "
        "Візьми токен у @BotFather і додай його в Render → Environment."
    )

DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "products.json"

MAX_FILE_SIZE_MB = 20
MAX_RESULTS = 25
FUZZY_THRESHOLD = 75  # 0-100: наскільки схожим має бути слово, щоб зарахувати збіг

UPDATE_BUTTON_TEXT = "🔄 Оновити базу товарів"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=UPDATE_BUTTON_TEXT)]],
    resize_keyboard=True,
)

# Проста in-memory база, що синхронізується з DB_FILE на диску.
# Диск на Render переживає рестарти процесу (сон/пробудження),
# але стирається при редеплої — це нормально для рідких оновлень коду.
DB = {"categories": [], "products": []}

# Тимчасово тримає щойно розібраний файл до підтвердження користувачем
PENDING = {}


def load_db():
    if DB_FILE.exists():
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                DB["categories"] = data.get("categories", [])
                DB["products"] = data.get("products", [])
                log.info(f"Завантажено {len(DB['products'])} товарів з диску.")
        except Exception as e:
            log.warning(f"Не вдалося завантажити {DB_FILE}: {e}")


def save_db():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────
# Утиліти очищення значень
# ──────────────────────────────────────────────────────────────


def clean_sku(val):
    if val is None:
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def clean_price(val):
    if val is None or val == "":
        return 0.0
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return 0.0


def clean_count(val):
    if val is None or val == "":
        return 0
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


# ──────────────────────────────────────────────────────────────
# Парсинг .numbers (без фото — навмисно не звертаємось до cell.image,
# щоб не провокувати зайве споживання пам'яті на декодування картинок)
# ──────────────────────────────────────────────────────────────


def parse_numbers_file(path: str):
    doc = Document(path)
    all_products = []
    categories = []

    for sheet in doc.sheets:
        sheet_name = sheet.name
        categories.append(sheet_name)

        for table in sheet.tables:
            num_rows = table.num_rows
            num_cols = table.num_cols

            for row_idx in range(1, num_rows):
                row_values = [
                    table.cell(row_idx, col_idx).value
                    if table.cell(row_idx, col_idx)
                    else None
                    for col_idx in range(num_cols)
                ]

                if not any(row_values):
                    continue

                raw_sku = row_values[0] if len(row_values) > 0 else ""
                raw_title = row_values[1] if len(row_values) > 1 else ""

                sku = clean_sku(raw_sku)
                title = str(raw_title).strip() if raw_title is not None else ""

                if not title or title.lower() in [
                    "назва товару",
                    "артикул",
                    "назва",
                    "none",
                ]:
                    continue

                raw_count = row_values[-2] if len(row_values) >= 2 else 0
                raw_price = row_values[-1] if len(row_values) >= 1 else 0

                all_products.append(
                    {
                        "id": f"{sheet_name}_{row_idx}",
                        "category": sheet_name,
                        "sku": sku,
                        "title": title,
                        "count": clean_count(raw_count),
                        "price": clean_price(raw_price),
                    }
                )

    del doc
    return categories, all_products


def is_junk_sheet(name: str) -> bool:
    n = name.strip().lower()
    return any(keyword in n for keyword in JUNK_SHEET_KEYWORDS)


def parse_xlsx_file(path: str):
    """Парсинг .xlsx (напр. конвертований з .numbers). Фотки навмисно
    ігноруються — читаємо тільки текстові значення клітинок."""
    wb = load_workbook(path, read_only=True, data_only=True)
    all_products = []
    categories = []

    try:
        for ws in wb.worksheets:
            if is_junk_sheet(ws.title):
                continue

            rows = list(ws.iter_rows(values_only=True))
            if len(rows) < 2:
                continue

            num_cols = max((len(r) for r in rows), default=0)
            if num_cols < 3:
                # Занадто мало колонок для товарної таблиці —
                # найімовірніше службовий лист від конвертера.
                continue

            sheet_name = ws.title
            categories.append(sheet_name)

            for row_idx, row_values in enumerate(rows[1:], start=1):
                if not any(row_values):
                    continue

                raw_sku = row_values[0] if len(row_values) > 0 else ""
                raw_title = row_values[1] if len(row_values) > 1 else ""

                sku = clean_sku(raw_sku)
                title = (
                    str(raw_title).strip() if raw_title is not None else ""
                )

                if not title or title.lower() in [
                    "назва товару",
                    "артикул",
                    "назва",
                    "none",
                ]:
                    continue

                raw_count = row_values[-2] if len(row_values) >= 2 else 0
                raw_price = row_values[-1] if len(row_values) >= 1 else 0

                all_products.append(
                    {
                        "id": f"{sheet_name}_{row_idx}",
                        "category": sheet_name,
                        "sku": sku,
                        "title": title,
                        "count": clean_count(raw_count),
                        "price": clean_price(raw_price),
                    }
                )
    finally:
        wb.close()

    return categories, all_products


def search_products(query: str):
    # Пошук "по словах" з допуском на одруки: кожне слово запиту або
    # точно входить у назву/артикул, або достатньо на нього схоже
    # (rapidfuzz). Короткі слова (≤2 символи) шукаються тільки точним
    # підрядком, щоб не ловити випадковий шум.
    tokens = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    if not tokens:
        return []

    scored = []
    for p in DB["products"]:
        haystack = f"{p['title']} {p['sku']}".lower()
        haystack_words = [w for w in re.split(r"[\s\-_/]+", haystack) if w]

        token_scores = []
        matched_all = True

        for tok in tokens:
            if len(tok) <= 2:
                if not any(tok in w for w in haystack_words):
                    matched_all = False
                    break
                token_scores.append(100)
                continue

            best = 0
            for w in haystack_words:
                if tok in w:
                    best = 100
                    break
                best = max(best, fuzz.ratio(tok, w))

            if best < FUZZY_THRESHOLD:
                matched_all = False
                break
            token_scores.append(best)

        if matched_all:
            avg_score = sum(token_scores) / len(token_scores)
            scored.append((avg_score, p))

    # Найточніші збіги — першими
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored]


def format_product(p: dict) -> str:
    availability = f"{p['count']} шт" if p["count"] > 0 else "немає в наявності"
    price = f"{p['price']:.0f} ₴" if p["price"] else "—"
    return (
        f"📦 <b>{p['title']}</b>\n"
        f"Арт: <code>{p['sku'] or '—'}</code>\n"
        f"Наявність: {availability}\n"
        f"Ціна: {price}\n"
        f"Категорія: {p['category']}"
    )


# ──────────────────────────────────────────────────────────────
# Telegram-бот
# ──────────────────────────────────────────────────────────────

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привіт! 👋\n\n"
        "Надішли мені <b>.numbers</b> або <b>.xlsx</b> файл з базою товарів "
        "— я його розберу.\n"
        "Після цього просто пиши назву або артикул товару в чат, "
        "і я знайду все, що підходить.\n\n"
        "Коли треба оновити базу — тисни кнопку внизу або просто "
        "кидай новий файл, я запитаю підтвердження перед перезаписом.\n\n"
        "Команди:\n"
        "/status — скільки товарів зараз завантажено\n"
        "/categories — список категорій",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    total = len(DB["products"])
    with_stock = sum(1 for p in DB["products"] if p["count"] > 0)
    await message.answer(
        f"У базі зараз: <b>{total}</b> товарів\n"
        f"У наявності: <b>{with_stock}</b>",
        parse_mode="HTML",
    )


@router.message(Command("categories"))
async def cmd_categories(message: Message):
    if not DB["categories"]:
        await message.answer("Категорій ще немає — спочатку завантаж .numbers файл.")
        return
    text = "Категорії:\n" + "\n".join(f"• {c}" for c in DB["categories"])
    await message.answer(text)


@router.message(F.text == UPDATE_BUTTON_TEXT)
async def handle_update_button(message: Message):
    total = len(DB["products"])
    await message.answer(
        f"Зараз у базі {total} товарів.\n"
        f"Надішли новий .numbers файл — я покажу, що зміниться, "
        f"і запитаю підтвердження перед оновленням."
    )


@router.message(F.document)
async def handle_document(message: Message):
    doc = message.document
    filename = doc.file_name or "data.numbers"
    filename_lower = filename.lower()

    if filename_lower.endswith(".numbers"):
        parse_fn = parse_numbers_file
    elif filename_lower.endswith((".xlsx", ".xls")):
        parse_fn = parse_xlsx_file
    else:
        await message.answer("Приймаю файли .numbers або .xlsx 🙂")
        return

    size_mb = (doc.file_size or 0) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        await message.answer(
            f"Файл завеликий ({size_mb:.1f} МБ). "
            f"Максимум {MAX_FILE_SIZE_MB} МБ."
        )
        return

    status_msg = await message.answer("⏳ Завантажую і розбираю файл...")

    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
    temp_path = DATA_DIR / f"temp_{safe_name}"

    try:
        await message.bot.download(doc, destination=str(temp_path))

        # Парсинг синхронний — виносимо в окремий потік, щоб не
        # блокувати event loop і не заважати обробці інших повідомлень.
        categories, products = await asyncio.to_thread(
            parse_fn, str(temp_path)
        )

        old_total = len(DB["products"])
        new_total = len(products)
        new_with_stock = sum(1 for p in products if p["count"] > 0)

        # Не перезаписуємо одразу — чекаємо підтвердження, щоб не
        # втратити базу через випадково не той файл.
        PENDING[message.chat.id] = {
            "categories": categories,
            "products": products,
        }

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Так, оновити", callback_data="confirm_update"
                    ),
                    InlineKeyboardButton(
                        text="❌ Скасувати", callback_data="cancel_update"
                    ),
                ]
            ]
        )

        await status_msg.edit_text(
            f"Файл розібрано.\n\n"
            f"Було: <b>{old_total}</b> товарів\n"
            f"Стане: <b>{new_total}</b> товарів "
            f"(у наявності: {new_with_stock})\n\n"
            f"Оновити базу цими даними?",
            parse_mode="HTML",
            reply_markup=kb,
        )

    except Exception as e:
        log.exception("Помилка обробки .numbers файлу")
        await status_msg.edit_text(f"❌ Помилка обробки файлу: {e}")

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@router.callback_query(F.data == "confirm_update")
async def confirm_update(callback: CallbackQuery):
    pending = PENDING.pop(callback.message.chat.id, None)
    if not pending:
        await callback.answer("Дані застаріли, надішли файл ще раз.", show_alert=True)
        return

    DB["categories"] = pending["categories"]
    DB["products"] = pending["products"]
    save_db()

    total = len(DB["products"])
    with_stock = sum(1 for p in DB["products"] if p["count"] > 0)

    await callback.message.edit_text(
        f"✅ Базу оновлено!\n"
        f"Товарів: <b>{total}</b>\n"
        f"У наявності: <b>{with_stock}</b>\n\n"
        f"Тепер просто пиши назву або артикул для пошуку.",
        parse_mode="HTML",
    )
    await callback.answer()
    log.info(f"База оновлена: {total} товарів.")


@router.callback_query(F.data == "cancel_update")
async def cancel_update(callback: CallbackQuery):
    PENDING.pop(callback.message.chat.id, None)
    await callback.message.edit_text("Скасовано. Стара база лишилась без змін.")
    await callback.answer()


@router.message(F.text)
async def handle_search(message: Message):
    query = message.text.strip()
    if not query or query.startswith("/"):
        return

    if not DB["products"]:
        await message.answer(
            "База ще порожня — спочатку надішли .numbers файл."
        )
        return

    results = search_products(query)

    if not results:
        await message.answer(f"Нічого не знайдено за запитом «{query}» 🤷")
        return

    shown = results[:MAX_RESULTS]
    for p in shown:
        await message.answer(format_product(p), parse_mode="HTML")

    if len(results) > MAX_RESULTS:
        await message.answer(
            f"Показано перші {MAX_RESULTS} з {len(results)} знайдених. "
            f"Уточни запит, щоб звузити пошук."
        )


# ──────────────────────────────────────────────────────────────
# Health-check веб-сервер (потрібен, щоб Render free web service
# розпізнавав сервіс як "живий" — він вимагає слухати $PORT)
# ──────────────────────────────────────────────────────────────

api = FastAPI()


@api.get("/")
def health():
    return {"status": "ok", "products": len(DB["products"])}


async def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(api, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def run_bot():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Бот запускається (polling)...")
    await dp.start_polling(bot)


async def main():
    load_db()
    await asyncio.gather(run_web_server(), run_bot())


if __name__ == "__main__":
    asyncio.run(main())
