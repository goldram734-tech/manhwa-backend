"""
AI Manhwa Translator - Backend (Python Flask)
Deploy: Render.com (bepul)
"""

import os
import io
import base64
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image, ImageDraw, ImageFont
import fitz  # PyMuPDF

app = Flask(__name__)
CORS(app)  # GitHub Pages frontendidan so'rov qabul qilish

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
OCR_SPACE_API_KEY = os.environ.get("OCR_SPACE_API_KEY", "K81851527588957")  # bepul key
MAX_PAGES = 20  # Bir so'rovda max sahifalar


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def pdf_to_images(pdf_bytes: bytes, dpi: int = 150) -> list[dict]:
    """PDF → PNG images (base64) using PyMuPDF"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        if i >= MAX_PAGES:
            break
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        img_bytes = pix.tobytes("png")
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        pages.append({
            "index": i,
            "base64": b64,
            "width": pix.width,
            "height": pix.height,
        })
    doc.close()
    return pages


def ocr_image(base64_img: str) -> dict:
    """
    OCR.space API — bepul, manga uchun yaxshi
    Returns: { "text": str, "lines": [...] }
    """
    payload = {
        "base64Image": f"data:image/png;base64,{base64_img}",
        "language": "kor",          # Koreys
        "isOverlayRequired": True,  # Bounding box kerak
        "detectOrientation": True,
        "scale": True,
        "OCREngine": 2,             # Engine 2 — manga/komiks uchun yaxshiroq
        "filetype": "PNG",
    }
    headers = {"apikey": OCR_SPACE_API_KEY}
    resp = requests.post(
        "https://api.ocr.space/parse/image",
        data=payload,
        headers=headers,
        timeout=30
    )
    data = resp.json()

    if data.get("IsErroredOnProcessing"):
        return {"text": "", "lines": []}

    result = data.get("ParsedResults", [{}])[0]
    full_text = result.get("ParsedText", "").strip()

    # TextOverlay bilan line boxes
    lines = []
    overlay = result.get("TextOverlay", {})
    for line in overlay.get("Lines", []):
        words = line.get("Words", [])
        if not words:
            continue
        line_text = " ".join(w.get("WordText", "") for w in words)
        # Bounding box: min/max koordinatalar
        lefts  = [w.get("Left", 0) for w in words]
        tops   = [w.get("Top", 0) for w in words]
        rights = [w.get("Left", 0) + w.get("Width", 0) for w in words]
        bots   = [w.get("Top", 0) + w.get("Height", 0) for w in words]
        lines.append({
            "text": line_text,
            "x": min(lefts),
            "y": min(tops),
            "w": max(rights) - min(lefts),
            "h": max(bots) - min(tops),
        })

    return {"text": full_text, "lines": lines}


def draw_translated_image(base64_img: str, translations: list[dict]) -> str:
    """
    Har bir OCR box ustiga:
    1. Oq to'rtburchak (original matnni berkitish)
    2. O'zbek matni yozish
    Returns: base64 PNG
    """
    img_bytes = base64.b64decode(base64_img)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Font — Unicode (O'zbek) uchun
    font_size = 14
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
        font_bold = font

    for item in translations:
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        uz_text = item.get("translated", "")
        if not uz_text:
            continue

        # 1. Oq box (inpainting o'rniga)
        padding = 3
        draw.rectangle(
            [x - padding, y - padding, x + w + padding, y + h + padding],
            fill="white",
            outline="#cccccc",
            width=1,
        )

        # 2. Matn yozish (word wrap)
        wrapped = wrap_text(uz_text, font, w + padding * 2)
        text_y = y
        for line in wrapped:
            if text_y + font_size > y + h + padding * 2 + 5:
                break
            draw.text((x, text_y), line, fill="#1a1a1a", font=font)
            text_y += font_size + 2

    # PNG → base64
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def wrap_text(text: str, font, max_width: int) -> list[str]:
    """Matnni berilgan kenglikka moslab qatorlarga bo'lish"""
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        try:
            bbox = font.getbbox(test)
            w = bbox[2] - bbox[0]
        except Exception:
            w = len(test) * 7  # fallback
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines if lines else [text[:30]]


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "AI Manhwa Translator"})


@app.route("/convert-pdf", methods=["POST"])
def convert_pdf():
    """
    POST /convert-pdf
    Body: multipart/form-data { file: PDF }
    Returns: { pages: [ { index, base64, width, height } ] }
    """
    if "file" not in request.files:
        return jsonify({"error": "PDF fayl yuborilmadi"}), 400

    pdf_file = request.files["file"]
    if not pdf_file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Faqat PDF format qabul qilinadi"}), 400

    pdf_bytes = pdf_file.read()
    if len(pdf_bytes) > 50 * 1024 * 1024:  # 50 MB limit
        return jsonify({"error": "PDF hajmi 50MB dan oshmasligi kerak"}), 400

    try:
        pages = pdf_to_images(pdf_bytes)
        return jsonify({"pages": pages, "total": len(pages)})
    except Exception as e:
        return jsonify({"error": f"PDF konvertatsiya xatosi: {str(e)}"}), 500


@app.route("/ocr", methods=["POST"])
def ocr():
    """
    POST /ocr
    Body: JSON { base64: "..." }
    Returns: { text: str, lines: [...] }
    """
    data = request.get_json()
    if not data or "base64" not in data:
        return jsonify({"error": "base64 rasm yuborilmadi"}), 400

    try:
        result = ocr_image(data["base64"])
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"OCR xatosi: {str(e)}"}), 500


@app.route("/typeset", methods=["POST"])
def typeset():
    """
    POST /typeset
    Body: JSON { base64: "...", translations: [ {x,y,w,h,translated} ] }
    Returns: { base64: "..." }
    """
    data = request.get_json()
    if not data or "base64" not in data:
        return jsonify({"error": "Rasm yuborilmadi"}), 400

    translations = data.get("translations", [])
    try:
        result_b64 = draw_translated_image(data["base64"], translations)
        return jsonify({"base64": result_b64})
    except Exception as e:
        return jsonify({"error": f"Typesetting xatosi: {str(e)}"}), 500


# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
