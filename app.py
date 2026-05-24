from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import yt_dlp
import os
import uuid
import threading
import time
import sqlite3
import hashlib
import random
import string
import requests

app = Flask(__name__)
CORS(app, origins=[
    "https://baixaclip.online",
    "https://www.baixaclip.online",
    "https://gregarious-semolina-35c555.netlify.app"
])

# ── CONFIG ──────────────────────────────────────────────
DOWNLOAD_DIR = "/tmp/snapload"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

ABACATE_API_KEY = "abc_prod_kQSa4yrqLefcYYYTZW0KXyhe"
ABACATE_BASE    = "https://api.abacatepay.com/v2"

PRODUCT_IDS = {
    "daily":   "prod_T6PwMcEsfsdex2rRdSnNrye2",
    "weekly":  "prod_Nz6phqAJ0yY0xTk3BwDRuyaM",
    "monthly": "prod_HXHBqDrQhxdMaGPWQnZTcMum",
}

PLAN_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
PLAN_NAMES = {"daily": "Plano Diário", "weekly": "Plano Semanal", "monthly": "Plano Mensal"}

DB_PATH = "/tmp/baixaclip.db"

# ── DATABASE ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            nick TEXT PRIMARY KEY,
            pass_hash TEXT NOT NULL,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checkouts (
            id TEXT PRIMARY KEY,
            nick TEXT NOT NULL,
            plan TEXT NOT NULL,
            abacate_id TEXT,
            abacate_url TEXT,
            pix_code TEXT,
            qr_code_url TEXT,
            status TEXT DEFAULT 'pending',
            created_at INTEGER DEFAULT (strftime('%s','now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codes (
            code TEXT PRIMARY KEY,
            nick TEXT NOT NULL,
            plan TEXT NOT NULL,
            days INTEGER NOT NULL,
            used INTEGER DEFAULT 0,
            expires_at INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ── HELPERS ───────────────────────────────────────────────
def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_code():
    chars = string.ascii_uppercase + string.digits
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    return f"BC-{part1}-{part2}"

def abacate_headers():
    return {
        "Authorization": f"Bearer {ABACATE_API_KEY}",
        "Content-Type": "application/json"
    }

# ── CLEANUP ───────────────────────────────────────────────
def cleanup_old_files():
    while True:
        time.sleep(600)
        now = time.time()
        try:
            for f in os.listdir(DOWNLOAD_DIR):
                fp = os.path.join(DOWNLOAD_DIR, f)
                if os.path.isfile(fp) and now - os.path.getmtime(fp) > 600:
                    os.remove(fp)
        except Exception:
            pass

threading.Thread(target=cleanup_old_files, daemon=True).start()

# ── HEALTH ────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ── VIDEO INFO ────────────────────────────────────────────
@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json()
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL é obrigatória"}), 400

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen = set()

        # MP4 HD
        for f in (info.get("formats") or []):
            if f.get("ext") == "mp4" and f.get("height") and f["height"] >= 720:
                key = "mp4_hd"
                if key not in seen:
                    seen.add(key)
                    formats.append({"format_id": f["format_id"], "ext": "mp4", "label": "MP4 HD"})
                break

        # MP4 SD
        for f in (info.get("formats") or []):
            if f.get("ext") == "mp4" and f.get("height") and f["height"] < 720:
                key = "mp4_sd"
                if key not in seen:
                    seen.add(key)
                    formats.append({"format_id": f["format_id"], "ext": "mp4", "label": "MP4 SD"})
                break

        # Fallback MP4
        if not formats:
            for f in (info.get("formats") or []):
                if f.get("ext") == "mp4":
                    formats.append({"format_id": f["format_id"], "ext": "mp4", "label": "MP4"})
                    break

        # MP3
        formats.append({"format_id": "bestaudio", "ext": "mp3", "label": "MP3 Áudio"})

        return jsonify({
            "title":     info.get("title", "Vídeo"),
            "thumbnail": info.get("thumbnail"),
            "uploader":  info.get("uploader") or info.get("channel"),
            "duration":  info.get("duration"),
            "formats":   formats,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ── DOWNLOAD ──────────────────────────────────────────────
@app.route("/api/download", methods=["POST"])
def download():
    data      = request.get_json()
    url       = data.get("url", "").strip()
    format_id = data.get("format_id", "best")
    ext       = data.get("ext", "mp4")

    if not url:
        return jsonify({"error": "URL é obrigatória"}), 400

    file_id  = str(uuid.uuid4())
    out_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    if ext == "mp3":
        ydl_opts = {
            "quiet": True,
            "format": "bestaudio/best",
            "outtmpl": out_path,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        }
    else:
        ydl_opts = {
            "quiet": True,
            "format": f"{format_id}+bestaudio/best[ext=mp4]/{format_id}/best",
            "outtmpl": out_path,
            "merge_output_format": "mp4",
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info     = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        # Encontra o arquivo gerado
        base = os.path.splitext(filename)[0]
        final = filename
        for candidate in [filename, f"{base}.mp4", f"{base}.mp3", f"{base}.webm"]:
            if os.path.exists(candidate):
                final = candidate
                break

        return send_file(
            final,
            as_attachment=True,
            download_name=f"baixaclip_{file_id}.{ext}",
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ── CREATE CHECKOUT ───────────────────────────────────────
PLAN_AMOUNTS = {"daily": 590, "weekly": 1490, "monthly": 1990}

@app.route("/api/create-checkout", methods=["POST"])
def create_checkout():
    data = request.get_json()
    print("CREATE CHECKOUT REQUEST:", data)

    plan = data.get("plan")
    nick = data.get("nick", "").strip().lower()
    pwd  = data.get("pass", "")

    if plan not in PLAN_AMOUNTS:
        return jsonify({"error": "Plano inválido"}), 400
    if not nick or len(nick) < 3:
        return jsonify({"error": "Nick inválido"}), 400
    if not pwd or len(pwd) < 6:
        return jsonify({"error": "Senha inválida"}), 400

    conn = get_db()

    # Cria ou verifica usuário
    user = conn.execute("SELECT * FROM users WHERE nick = ?", (nick,)).fetchone()
    if user:
        if user["pass_hash"] != hash_pass(pwd):
            conn.close()
            return jsonify({"error": "Nick já existe com outra senha. Tente outro nick."}), 400
    else:
        conn.execute("INSERT INTO users (nick, pass_hash) VALUES (?, ?)", (nick, hash_pass(pwd)))
        conn.commit()

    # Cria cobrança Pix na AbacatePay
    checkout_id = str(uuid.uuid4())
    payload = {
        "amount": PLAN_AMOUNTS[plan],
        "externalId": checkout_id,
        "description": f"BaixaClip - {PLAN_NAMES[plan]} - @{nick}",
        "customer": {
            "name": nick,
            "email": f"{nick}@baixaclip.online"
        }
    }

    print("ABACATE PAYLOAD:", payload)

    try:
        resp = requests.post(
            f"{ABACATE_BASE}/pixQrCode/create",
            json=payload,
            headers=abacate_headers(),
            timeout=15
        )
        print("ABACATE STATUS:", resp.status_code)
        resp_data = resp.json()
        print("ABACATE RESPONSE:", resp_data)

        if not resp_data.get("success"):
            # Fallback: tenta billing
            resp2 = requests.post(
                f"{ABACATE_BASE}/billing/create",
                json={
                    "frequency": "ONE_TIME",
                    "methods": ["PIX"],
                    "products": [{"externalId": plan, "name": PLAN_NAMES[plan], "quantity": 1, "price": PLAN_AMOUNTS[plan]}],
                    "externalId": checkout_id,
                    "customer": {"name": nick, "email": f"{nick}@baixaclip.online", "cellphone": "11999999999", "taxId": {"type": "CPF", "number": "00000000000"}}
                },
                headers=abacate_headers(),
                timeout=15
            )
            print("BILLING STATUS:", resp2.status_code)
            resp_data = resp2.json()
            print("BILLING RESPONSE:", resp_data)

        if not resp_data.get("success"):
            conn.close()
            return jsonify({"error": resp_data.get("error", "Erro na AbacatePay")}), 400

        d = resp_data["data"]

        # Tenta extrair pix_code e qr_code_url de vários campos possíveis
        pix_code  = (d.get("pixCode") or d.get("pix_code") or d.get("emv") or
                     d.get("brCode") or d.get("code") or "")
        qr_url    = (d.get("qrCodeUrl") or d.get("qr_code_url") or d.get("qrCode") or
                     d.get("qrcode") or d.get("qr_image") or "")
        abacate_id = d.get("id") or checkout_id
        pay_url    = d.get("url") or d.get("checkoutUrl") or ""

        # Gera QR via API pública se não tiver
        if pix_code and not qr_url:
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={requests.utils.quote(pix_code)}"

        conn.execute("""
            INSERT INTO checkouts (id, nick, plan, abacate_id, abacate_url, pix_code, qr_code_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (checkout_id, nick, plan, abacate_id, pay_url, pix_code, qr_url))
        conn.commit()
        conn.close()

        return jsonify({
            "checkout_id": checkout_id,
            "payment_url": pay_url,
            "pix_code":    pix_code,
            "qr_code_url": qr_url,
        })

    except Exception as e:
        print("EXCEPTION:", str(e))
        conn.close()
        return jsonify({"error": str(e)}), 500

# ── CHECK PAYMENT ─────────────────────────────────────────
@app.route("/api/check-payment", methods=["POST"])
def check_payment():
    data        = request.get_json()
    checkout_id = data.get("checkout_id")
    nick        = data.get("nick", "").strip().lower()

    if not checkout_id:
        return jsonify({"paid": False}), 400

    conn = get_db()
    checkout = conn.execute(
        "SELECT * FROM checkouts WHERE id = ? AND nick = ?", (checkout_id, nick)
    ).fetchone()

    if not checkout:
        conn.close()
        return jsonify({"paid": False})

    # Já foi pago e código gerado
    if checkout["status"] == "paid":
        code_row = conn.execute(
            "SELECT code FROM codes WHERE nick = ? AND plan = ? ORDER BY created_at DESC LIMIT 1",
            (nick, checkout["plan"])
        ).fetchone()
        conn.close()
        return jsonify({"paid": True, "code": code_row["code"] if code_row else None})

    # Consulta status na AbacatePay
    try:
        resp = requests.get(
            f"{ABACATE_BASE}/checkouts/{checkout['abacate_id']}",
            headers=abacate_headers(),
            timeout=10
        )
        resp_data = resp.json()
        status = resp_data.get("data", {}).get("status", "")

        if status == "PAID":
            # Gera código único
            code = generate_code()
            days = PLAN_DAYS[checkout["plan"]]
            expires = int(time.time()) + days * 86400

            conn.execute("""
                INSERT INTO codes (code, nick, plan, days, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (code, nick, checkout["plan"], days, expires))

            conn.execute(
                "UPDATE checkouts SET status = 'paid' WHERE id = ?", (checkout_id,)
            )
            conn.commit()
            conn.close()
            return jsonify({"paid": True, "code": code})

    except Exception:
        pass

    conn.close()
    return jsonify({"paid": False})

# ── WEBHOOK AbacatePay ────────────────────────────────────
@app.route("/webhook/abacatepay", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    external_id = data.get("externalId") or data.get("external_id")
    status      = data.get("status") or data.get("event")

    if not external_id:
        return jsonify({"ok": True})

    if status in ("PAID", "payment.confirmed", "COMPLETED"):
        conn = get_db()
        checkout = conn.execute(
            "SELECT * FROM checkouts WHERE id = ? AND status != 'paid'", (external_id,)
        ).fetchone()

        if checkout:
            code    = generate_code()
            days    = PLAN_DAYS.get(checkout["plan"], 1)
            expires = int(time.time()) + days * 86400

            conn.execute("""
                INSERT OR IGNORE INTO codes (code, nick, plan, days, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (code, checkout["nick"], checkout["plan"], days, expires))

            conn.execute(
                "UPDATE checkouts SET status = 'paid' WHERE id = ?", (external_id,)
            )
            conn.commit()
        conn.close()

    return jsonify({"ok": True})

# ── VALIDATE CODE ─────────────────────────────────────────
@app.route("/api/validate-code", methods=["POST"])
def validate_code():
    data = request.get_json()
    code = data.get("code", "").strip().upper()

    if not code:
        return jsonify({"valid": False})

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM codes WHERE code = ? AND used = 0 AND expires_at > ?",
        (code, int(time.time()))
    ).fetchone()

    if row:
        conn.execute("UPDATE codes SET used = 1 WHERE code = ?", (code,))
        conn.commit()
        conn.close()
        return jsonify({"valid": True, "days": row["days"], "plan": row["plan"]})

    conn.close()
    return jsonify({"valid": False})

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
