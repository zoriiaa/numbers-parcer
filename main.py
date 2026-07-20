import os
import io
import re
import gc
import zipfile
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from numbers_parser import Document

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "./static/images"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

DB = {"categories": [], "products": []}


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


def process_and_save_image(img_bytes, filename_prefix):
    """Стискає та збереже картинку по 1 шт"""
    if not img_bytes or len(img_bytes) < 300:
        return None

    if HAS_PIL:
        try:
            img = Image.open(io.BytesIO(img_bytes))
            if img.width < 15 or img.height < 15:
                return None

            img.thumbnail((300, 300), Image.Resampling.LANCZOS)

            fname = f"{filename_prefix}.jpg"
            path = os.path.join(UPLOAD_DIR, fname)

            if img.mode in ("RGBA", "P", "LA"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                background.save(path, "JPEG", quality=70, optimize=True)
            else:
                img.convert("RGB").save(path, "JPEG", quality=70, optimize=True)

            return f"/static/images/{fname}"
        except Exception:
            pass

    try:
        fname = f"{filename_prefix}.jpg"
        path = os.path.join(UPLOAD_DIR, fname)
        with open(path, "wb") as f:
            f.write(img_bytes)
        return f"/static/images/{fname}"
    except Exception:
        return None


def get_zip_image_names(temp_filename):
    """Отримує список шляхів файлів без завантаження їхніх байтів у RAM"""
    image_names = []
    try:
        with zipfile.ZipFile(temp_filename, 'r') as z:
            for file_info in z.infolist():
                if file_info.filename.startswith('Data/') and file_info.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff')):
                    if file_info.file_size > 500:
                        image_names.append(file_info.filename)
    except Exception as e:
        print(f"⚠️ Помилка списку ZIP: {e}")
    return image_names


@app.post("/api/upload")
async def upload_numbers(request: Request, filename: str = "data.numbers"):
    print(f"\n==========================================")
    print(f"ПОЧАТОК ЗАВАНТАЖЕННЯ: '{filename}'")

    file_bytes = await request.body()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Файл порожній або не переданий")

    temp_filename = f"temp_{re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)}"
    if not temp_filename.endswith(".numbers"):
        temp_filename += ".numbers"

    with open(temp_filename, "wb") as buffer:
        buffer.write(file_bytes)

    zip_img_names = get_zip_image_names(temp_filename)
    print(f"📦 Знайдено у ZIP: {len(zip_img_names)} назв картинок.")

    doc = None
    try:
        doc = Document(temp_filename)
        all_products = []
        categories = []
        global_img_counter = 0

        # Читаємо байти по 1 файлу в момент створення товару
        with zipfile.ZipFile(temp_filename, 'r') as z_file:
            for sheet in doc.sheets:
                sheet_name = sheet.name
                categories.append(sheet_name)

                for table in sheet.tables:
                    num_rows = table.num_rows
                    num_cols = table.num_cols

                    for row_idx in range(1, num_rows):
                        row_cells = [table.cell(row_idx, col_idx) for col_idx in range(num_cols)]
                        row_values = [c.value if c else None for c in row_cells]

                        if not any(row_values):
                            continue

                        raw_sku = row_values[0] if len(row_values) > 0 else ""
                        raw_title = row_values[1] if len(row_values) > 1 else ""

                        sku = clean_sku(raw_sku)
                        title = str(raw_title).strip() if raw_title is not None else ""

                        if not title or title.lower() in ["назва товару", "артикул", "назва", "none"]:
                            continue

                        cell_img_url = None

                        # 1. Пошук у комірці
                        for col_i, cell in enumerate(row_cells):
                            if cell and hasattr(cell, "image") and cell.image is not None:
                                try:
                                    img_obj = cell.image
                                    img_data = getattr(img_obj, "data", None) or getattr(img_obj, "filename_data", None)
                                    if img_data:
                                        prefix = f"img_{row_idx}_{col_i}"
                                        cell_img_url = process_and_save_image(img_data, prefix)
                                        if cell_img_url:
                                            break
                                except Exception:
                                    pass

                        # 2. Якщо комірка порожня, читаємо 1 картинку з ZIP
                        if not cell_img_url and global_img_counter < len(zip_img_names):
                            try:
                                img_name = zip_img_names[global_img_counter]
                                single_img_bytes = z_file.read(img_name)
                                prefix = f"img_z_{global_img_counter}"
                                cell_img_url = process_and_save_image(single_img_bytes, prefix)
                                if cell_img_url:
                                    global_img_counter += 1
                            except Exception:
                                pass

                        raw_count = row_values[-2] if len(row_values) >= 2 else 0
                        raw_price = row_values[-1] if len(row_values) >= 1 else 0

                        all_products.append({
                            "id": f"{sheet_name}_{row_idx}",
                            "category": sheet_name,
                            "sku": sku,
                            "title": title,
                            "image": cell_img_url,
                            "count": clean_count(raw_count),
                            "price": clean_price(raw_price)
                        })

        DB["categories"] = categories
        DB["products"] = all_products

        count_with_img = sum(1 for p in all_products if p["image"])
        print(f"\n✅ УСПІХ: Збережено {len(all_products)} товарів та {count_with_img} картинок!\n")

        return {"status": "ok", "total_products": len(all_products)}

    except Exception as e:
        print(f"❌ ПОМИЛКА: {e}")
        raise HTTPException(status_code=500, detail=f"Помилка обробки: {str(e)}")

    finally:
        if doc is not None:
            del doc
        gc.collect()

        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except Exception:
                pass


@app.get("/api/products")
async def get_products():
    return DB