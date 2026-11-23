"""MANDILAS ONLINE - Flask platform (shop, accounts, orders, admin, automations).
Run:  python app.py      (then open http://127.0.0.1:5000)
"""
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import smtplib
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

import emailkit as ek
from flask import (Flask, g, jsonify, render_template, request, send_from_directory, session)

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "mandilas.db"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
LAGOS = timezone(timedelta(hours=1))
LOW_STOCK = int(os.environ.get("LOW_STOCK", "3"))
PREVIEW_DIR = os.path.join(BASE_DIR, "email_previews")

SIZES = [str(n) for n in range(38, 47)]
CATEGORIES = ["Sneakers", "New Swagger", "Limited Editions", "Collaborations",
              "Corporate Shoes", "Boots", "Dainty Feet (Ladies Ballet)", "Slides & Sandals"]
STATUSES = ["paid", "processing", "dispatched", "out_for_delivery", "delivered", "cancelled", "refunded"]
IMAGE_EXT, VIDEO_EXT = {"jpg", "jpeg", "png", "webp"}, {"mp4", "webm"}

app = Flask(__name__, static_folder="assets", static_url_path="/assets")
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
    MAX_CONTENT_LENGTH=80 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


# ================================================================ database
SCHEMA = """
CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL,
  category TEXT NOT NULL, colour TEXT DEFAULT '', img TEXT NOT NULL, video TEXT DEFAULT '',
  sizes TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT UNIQUE NOT NULL,
  name TEXT, email TEXT, phone TEXT, address TEXT, city TEXT, items TEXT, total REAL, created_at REAL);
CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
  name TEXT DEFAULT '', phone TEXT DEFAULT '', address TEXT DEFAULT '', city TEXT DEFAULT '',
  marketing INTEGER DEFAULT 1, unsub_token TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS otps (email TEXT PRIMARY KEY, code_hash TEXT, expires REAL, attempts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS wishlist (customer_id INTEGER, product_id TEXT, PRIMARY KEY (customer_id, product_id));
CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, email TEXT,
  order_ref TEXT DEFAULT '', type TEXT, message TEXT, status TEXT DEFAULT 'open', reply TEXT DEFAULT '', created_at REAL);
CREATE TABLE IF NOT EXISTS discounts (code TEXT PRIMARY KEY, kind TEXT, value REAL, min_total REAL DEFAULT 0,
  usage_limit INTEGER DEFAULT 0, used INTEGER DEFAULT 0, expires TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS abandoned_carts (email TEXT PRIMARY KEY, name TEXT, items TEXT, updated_at REAL,
  reminded INTEGER DEFAULT 0, recovered INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, body TEXT,
  sent_count INTEGER DEFAULT 0, created_at REAL);
CREATE TABLE IF NOT EXISTS subscribers (email TEXT PRIMARY KEY, unsub_token TEXT, active INTEGER DEFAULT 1, created_at REAL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""
ORDER_COLUMNS = [("customer_id", "INTEGER"), ("subtotal", "REAL"), ("discount", "REAL DEFAULT 0"),
                 ("discount_code", "TEXT DEFAULT ''"), ("status", "TEXT DEFAULT 'paid'"),
                 ("history", "TEXT DEFAULT '[]'"), ("rider_name", "TEXT DEFAULT ''"),
                 ("rider_phone", "TEXT DEFAULT ''"), ("eta", "TEXT DEFAULT ''"),
                 ("tracking_note", "TEXT DEFAULT ''"), ("refund_status", "TEXT DEFAULT ''"),
                 ("refund_amount", "REAL DEFAULT 0")]


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    have = [r[1] for r in db.execute("PRAGMA table_info(orders)")]
    for name, ddl in ORDER_COLUMNS:          # upgrade an older database in place
        if name not in have:
            db.execute("ALTER TABLE orders ADD COLUMN %s %s" % (name, ddl))
    if "thumb" not in [r[1] for r in db.execute("PRAGMA table_info(products)")]:
        db.execute("ALTER TABLE products ADD COLUMN thumb TEXT DEFAULT ''")
    db.commit()
    db.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ================================================================ helpers
_hits = {}


def rate_ok(key, limit, window):
    now = time.time()
    recent = [t for t in _hits.get(key, []) if now - t < window]
    if len(recent) >= limit:
        _hits[key] = recent
        return False
    recent.append(now)
    _hits[key] = recent
    return True


def client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()


def err(message, code=400):
    return jsonify(error=message), code


def naira(n):
    return "NGN {:,.0f}".format(n or 0)


def site_url():
    return os.environ.get("SITE_URL", "http://127.0.0.1:5000").rstrip("/")


def lagos_now():
    return datetime.now(LAGOS).strftime("%d %b %Y, %H:%M")


def valid_email(e):
    return isinstance(e, str) and "@" in e and "." in e.split("@")[-1] and len(e) <= 120 and " " not in e


def owner_email():
    return os.environ.get("ORDER_EMAIL") or os.environ.get("SMTP_USER") or "mandilasonline@gmail.com"


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return err("Please log in as admin.", 401)
        return fn(*a, **kw)
    return wrapper


def customer_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        cid = session.get("cid")
        row = get_db().execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone() if cid else None
        if not row:
            return err("Please log in.", 401)
        g.customer = row
        return fn(*a, **kw)
    return wrapper


def clean_sizes(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        data = {}
    out = {}
    for s in SIZES:
        try:
            out[s] = max(0, int(data.get(s, 0)))
        except (ValueError, TypeError):
            out[s] = 0
    return out


def save_upload(file, allowed):
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        raise ValueError("File type not allowed: ." + ext)
    name = uuid.uuid4().hex + "." + ext
    file.save(os.path.join(UPLOAD_DIR, name))
    return name


def delete_upload(name):
    if name:
        try:
            os.remove(os.path.join(UPLOAD_DIR, os.path.basename(name)))
        except OSError:
            pass


def product_to_dict(r):
    return {"id": r["id"], "name": r["name"], "price": r["price"], "category": r["category"],
            "colour": r["colour"], "img": "/uploads/" + r["img"],
            "thumb": "/uploads/" + (r["thumb"] or r["img"]),
            "video": ("/uploads/" + r["video"]) if r["video"] else "", "sizes": json.loads(r["sizes"])}


def order_to_dict(r):
    return {"reference": r["reference"], "name": r["name"], "email": r["email"], "phone": r["phone"],
            "address": r["address"], "city": r["city"], "items": json.loads(r["items"] or "[]"),
            "subtotal": r["subtotal"] if r["subtotal"] is not None else r["total"],
            "discount": r["discount"] or 0, "discount_code": r["discount_code"] or "", "total": r["total"],
            "status": r["status"] or "paid", "history": json.loads(r["history"] or "[]"),
            "rider_name": r["rider_name"] or "", "rider_phone": r["rider_phone"] or "", "eta": r["eta"] or "",
            "tracking_note": r["tracking_note"] or "", "refund_status": r["refund_status"] or "",
            "refund_amount": r["refund_amount"] or 0, "created_at": r["created_at"]}


def customer_to_dict(r):
    return {"id": r["id"], "email": r["email"], "name": r["name"], "phone": r["phone"],
            "address": r["address"], "city": r["city"], "marketing": bool(r["marketing"])}


# ================================================================ images
def optimize_image(file):
    """Make every upload web-ready: right way up, no metadata, WebP, max 1600px + a 640px thumbnail."""
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in IMAGE_EXT:
        raise ValueError("Photo must be JPG, PNG or WebP.")
    try:
        img = ImageOps.exif_transpose(Image.open(file.stream))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")
    except Exception:
        raise ValueError("That photo couldn't be read.")
    base = uuid.uuid4().hex
    big, small = img.copy(), img.copy()
    big.thumbnail((1600, 1600))
    small.thumbnail((640, 640))
    big.save(os.path.join(UPLOAD_DIR, base + ".webp"), "WEBP", quality=82, method=4)
    small.save(os.path.join(UPLOAD_DIR, base + "_sm.webp"), "WEBP", quality=78, method=4)
    return base + ".webp", base + "_sm.webp"


def email_images(db, ids):
    """Shoe photos for emails: ({product_id: cid}, [(cid, jpeg_bytes, 'jpeg')]). Embedded, so they always show."""
    cids, imgs = {}, []
    for i, pid in enumerate(dict.fromkeys(ids)):
        row = db.execute("SELECT img, thumb FROM products WHERE id=?", (pid,)).fetchone()
        if not row:
            continue
        try:
            im = Image.open(os.path.join(UPLOAD_DIR, os.path.basename(row["thumb"] or row["img"]))).convert("RGB")
            im.thumbnail((560, 560))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=84)
        except Exception:
            continue
        cids[pid] = "p%d" % i
        imgs.append(("p%d" % i, buf.getvalue(), "jpeg"))
    return cids, imgs


# ================================================================ email
def _logo_bytes():
    try:
        with open(os.path.join(BASE_DIR, "assets", "logo-white.png"), "rb") as f:
            return f.read()
    except OSError:
        return b""


def save_preview(subject, html, images):
    """No Gmail set up yet? Save the email as a web page so you can see exactly how it looks."""
    import base64
    for cid, data, sub_t in images:
        html = html.replace("cid:%s" % cid, "data:image/%s;base64,%s" % (sub_t, base64.b64encode(data).decode()))
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    name = "%d-%s.html" % (time.time() * 1000, "".join(c if c.isalnum() else "-" for c in subject)[:40])
    with open(os.path.join(PREVIEW_DIR, name), "w", encoding="utf-8") as f:
        f.write(html)
    print("[email preview saved: email_previews/%s]" % name)


def send_email(to, subject, body, reply_to=None, html=None, images=None):
    """Background thread: the website never waits on Gmail. `html` makes it a branded graphic email."""
    all_images = ([("logo", _logo_bytes(), "png")] + list(images or [])) if html else []

    def worker():
        user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
        if not user or not password:
            print("\n[EMAIL NOT SENT - SMTP not set up] To: %s\nSubject: %s\n%s\n" % (to, subject, body))
            if html:
                save_preview(subject, html, all_images)
            return
        msg = EmailMessage()
        msg["From"] = "MANDILAS ONLINE <%s>" % user
        msg["To"], msg["Subject"] = to, subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
            part = msg.get_payload()[1]
            for cid, data, sub_t in all_images:
                part.add_related(data, "image", sub_t, cid="<%s>" % cid)
        try:
            with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
                s.starttls()
                s.login(user, password)
                s.send_message(msg)
        except Exception as exc:
            print("EMAIL ERROR:", exc)
    threading.Thread(target=worker, daemon=True).start()


def items_text(items):
    return "\n".join("  - %s (size %s) x%d  %s" % (i["name"], i["size"], i["qty"], naira(i["price"] * i["qty"])) for i in items)


def track_link(o):
    return "%s/track?ref=%s&email=%s" % (site_url(), o["reference"], o["email"])


STATUS_MAIL = {
    "processing": ("We're preparing your order", "Good news - we're packing your order now."),
    "dispatched": ("Your order has been dispatched", "Your order has left us and is on its way."),
    "out_for_delivery": ("Your order is out for delivery", "Your rider is on the way to you now."),
    "delivered": ("Your order has been delivered", "Your order has been marked as delivered. Enjoy your new pair!"),
    "cancelled": ("Your order was cancelled", "Your order has been cancelled. Reply to this email if you have questions."),
}


def email_status(o, db=None):
    if o["status"] not in STATUS_MAIL:
        return
    subject, line = STATUS_MAIL[o["status"]]
    extra = ""
    if o["rider_name"] or o["rider_phone"]:
        extra += "\nDelivery rider: %s %s" % (o["rider_name"], o["rider_phone"])
    if o["eta"]:
        extra += "\nExpected: %s" % o["eta"]
    if o["tracking_note"]:
        extra += "\nNote: %s" % o["tracking_note"]
    cids, imgs = email_images(db, [i["id"] for i in o["items"]]) if db is not None else ({}, [])
    send_email(o["email"], "%s - %s" % (subject, o["reference"]),
               "Hi %s,\n\n%s\n\nOrder: %s%s\n\nTrack it any time:\n%s\n\nThe House of Feet"
               % (o["name"], line, o["reference"], extra, track_link(o)),
               html=ek.status_email(o, subject, line, track_link(o), cids, o["items"]), images=imgs)


# ================================================================ pricing + discounts
def price_items(db, items):
    if not isinstance(items, list) or not items:
        raise ValueError("Your cart is empty.")
    lines, subtotal = [], 0.0
    for it in items[:30]:
        row = db.execute("SELECT * FROM products WHERE id=?", (str(it.get("id")),)).fetchone()
        size, qty = str(it.get("size")), it.get("qty")
        if not row or size not in SIZES or not isinstance(qty, int) or not 1 <= qty <= 20:
            raise ValueError("Invalid item in cart.")
        lines.append({"id": row["id"], "name": row["name"], "size": size, "qty": qty, "price": row["price"]})
        subtotal += row["price"] * qty
    return lines, subtotal


def apply_discount(db, code, subtotal):
    code = (code or "").strip().upper()
    if not code:
        return 0.0, None
    d = db.execute("SELECT * FROM discounts WHERE code=?", (code,)).fetchone()
    if not d or not d["active"]:
        raise ValueError("That discount code isn't valid.")
    if d["expires"] and d["expires"] < datetime.now(LAGOS).strftime("%Y-%m-%d"):
        raise ValueError("That discount code has expired.")
    if d["usage_limit"] and d["used"] >= d["usage_limit"]:
        raise ValueError("That discount code has been fully used.")
    if subtotal < (d["min_total"] or 0):
        raise ValueError("Spend at least %s to use this code." % naira(d["min_total"]))
    amount = round(subtotal * d["value"] / 100) if d["kind"] == "percent" else min(d["value"], subtotal)
    amount = min(amount, subtotal - 100)   # never let an order drop to zero
    return float(max(0, amount)), d


# ================================================================ order creation
def finalize_order(db, ref, cust, items, code, paid_kobo, currency):
    """Shared by the browser callback AND the Paystack webhook. Returns (reference, duplicate)."""
    if db.execute("SELECT 1 FROM orders WHERE reference=?", (ref,)).fetchone():
        return ref, True
    lines, subtotal = price_items(db, items)
    discount, drow = apply_discount(db, code, subtotal)
    total = subtotal - discount
    if int(paid_kobo) != int(round(total * 100)) or currency != "NGN":
        raise ValueError("Payment amount does not match the order.")
    history = json.dumps([{"status": "paid", "at": lagos_now(), "note": "Payment verified"}])
    cur = db.execute("SELECT id FROM customers WHERE email=?", (cust["email"].lower(),)).fetchone()
    try:
        db.execute("""INSERT INTO orders (reference,name,email,phone,address,city,items,subtotal,discount,discount_code,
                      total,status,history,customer_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (ref, cust["name"], cust["email"], cust["phone"], cust["address"], cust["city"], json.dumps(lines),
                    subtotal, discount, drow["code"] if drow else "", total, "paid", history,
                    cur["id"] if cur else None, time.time()))
    except sqlite3.IntegrityError:           # the other path got there first
        db.rollback()
        return ref, True
    short, alerts = False, []
    for ln in lines:
        row = db.execute("SELECT sizes FROM products WHERE id=?", (ln["id"],)).fetchone()
        sizes = json.loads(row["sizes"])
        before = sizes.get(ln["size"], 0)
        short = short or before < ln["qty"]
        sizes[ln["size"]] = max(0, before - ln["qty"])
        if before > LOW_STOCK >= sizes[ln["size"]]:            # just crossed the low-stock line
            alerts.append({"name": ln["name"], "size": ln["size"], "left": sizes[ln["size"]]})
        db.execute("UPDATE products SET sizes=? WHERE id=?", (json.dumps(sizes), ln["id"]))
    if drow:
        db.execute("UPDATE discounts SET used=used+1 WHERE code=?", (drow["code"],))
    db.execute("UPDATE abandoned_carts SET recovered=1 WHERE email=?", (cust["email"].lower(),))
    db.commit()

    disc_line = ("\nDiscount (%s): -%s" % (drow["code"], naira(discount))) if drow else ""
    o = {"reference": ref, "email": cust["email"], "name": cust["name"], "phone": cust["phone"], "address": cust["address"],
         "city": cust["city"], "subtotal": subtotal, "discount": discount, "discount_code": drow["code"] if drow else "", "total": total}
    cids, imgs = email_images(db, [l["id"] for l in lines])
    send_email(cust["email"], "Your MANDILAS ONLINE order " + ref,
               "Hi %s,\n\nThank you! Your payment was received.\n\nOrder reference: %s\n%s%s\n\nTotal paid: %s\n\n"
               "Delivery to:\n  %s\n  %s\n  Phone: %s\n\nTrack your order any time:\n%s\n\nThe House of Feet"
               % (cust["name"], ref, items_text(lines), disc_line, naira(total), cust["address"], cust["city"],
                  cust["phone"], track_link(o)),
               html=ek.order_confirmation(o, lines, cids, drow, track_link(o)), images=imgs)
    send_email(owner_email(), "NEW ORDER %s - %s" % (ref, naira(total)),
               "NEW PAID ORDER%s\n\nReference: %s\nTotal: %s%s\n\nCUSTOMER\n  Name: %s\n  Email: %s\n  Phone: %s\n\n"
               "DELIVERY ADDRESS\n  %s\n  %s\n\nITEMS\n%s\n\nManage it: %s/admin"
               % ("  (CHECK STOCK: a size ran short)" if short else "", ref, naira(total), disc_line, cust["name"],
                  cust["email"], cust["phone"], cust["address"], cust["city"], items_text(lines), site_url()),
               reply_to=cust["email"], html=ek.owner_order(o, lines, cids, short, site_url() + "/admin"), images=imgs)
    if alerts:
        send_email(owner_email(), "LOW STOCK ALERT", "Running low:\n" + "\n".join("  - %s (size %s): %d left" % (a["name"], a["size"], a["left"]) for a in alerts),
                   html=ek.lowstock_email(alerts, site_url() + "/admin"))
    return ref, False


def read_customer_fields(d):
    cust = {k: str(d.get(k, "")).strip()[:200] for k in ("name", "email", "phone", "address", "city")}
    if not all(cust.values()) or not valid_email(cust["email"]):
        raise ValueError("Please fill in your name, email, phone and delivery address.")
    return cust


def paystack(method, path, **kw):
    secret = os.environ.get("PAYSTACK_SECRET_KEY")
    if not secret:
        raise RuntimeError("Payments are not configured on the server yet.")
    return requests.request(method, "https://api.paystack.co" + path,
                            headers={"Authorization": "Bearer " + secret}, timeout=25, **kw)


# ================================================================ pages
@app.route("/")
def home():
    return render_template("index.html", site_url=site_url())


@app.route("/product/<pid>")
def product_page(pid):
    r = get_db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not r:
        return render_template("product.html", site_url=site_url(), p=None), 404
    p_ = product_to_dict(r)
    left = sum(p_["sizes"].values())
    desc = "%s%s - %s. Sizes EU 38-46. Pay securely online and track your delivery. MANDILAS ONLINE, the House of Feet." % (
        p_["name"], (" in " + p_["colour"]) if p_["colour"] else "", naira(p_["price"]))
    ld = {"@context": "https://schema.org", "@type": "Product", "name": p_["name"], "sku": p_["id"], "description": desc,
          "image": ["%s/share/%s.jpg" % (site_url(), p_["id"])], "category": p_["category"],
          "brand": {"@type": "Brand", "name": "MANDILAS ONLINE"},
          "offers": {"@type": "Offer", "priceCurrency": "NGN", "price": p_["price"], "url": "%s/product/%s" % (site_url(), p_["id"]),
                     "availability": "https://schema.org/InStock" if left else "https://schema.org/OutOfStock"}}
    return render_template("product.html", site_url=site_url(), p=p_, desc=desc, jsonld=json.dumps(ld).replace("</", "<\\/"))


@app.get("/share/<pid>.jpg")
def share_image(pid):
    """A 1200x630 picture for WhatsApp / Facebook / X link previews."""
    r = get_db().execute("SELECT img FROM products WHERE id=?", (pid,)).fetchone()
    if not r:
        return "", 404
    folder = os.path.join(UPLOAD_DIR, "share")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "%s.jpg" % os.path.basename(pid))
    if not os.path.exists(out):
        im = Image.open(os.path.join(UPLOAD_DIR, os.path.basename(r["img"]))).convert("RGB")
        im.thumbnail((1100, 570))
        canvas = Image.new("RGB", (1200, 630), (11, 11, 10))
        canvas.paste(im, ((1200 - im.width) // 2, (630 - im.height) // 2))
        canvas.save(out, "JPEG", quality=85)
    return send_from_directory(folder, os.path.basename(out), max_age=86400)


@app.get("/robots.txt")
def robots():
    return ("User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /account\nDisallow: /api/\nDisallow: /track\n\nSitemap: %s/sitemap.xml\n" % site_url(),
            200, {"Content-Type": "text/plain"})


@app.get("/sitemap.xml")
def sitemap():
    urls = ["<url><loc>%s/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>" % site_url()]
    for r in get_db().execute("SELECT id, created_at FROM products ORDER BY created_at DESC"):
        urls.append("<url><loc>%s/product/%s</loc><lastmod>%s</lastmod><priority>0.8</priority></url>" % (
            site_url(), r["id"], datetime.fromtimestamp(r["created_at"], LAGOS).strftime("%Y-%m-%d")))
    return ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>' % "".join(urls),
            200, {"Content-Type": "application/xml"})


@app.route("/account")
def account_page():
    return render_template("account.html")


@app.route("/track")
def track_page():
    return render_template("track.html")


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


@app.route("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename, max_age=86400)


@app.get("/unsubscribe/<token>")
def unsubscribe(token):
    db = get_db()
    db.execute("UPDATE customers SET marketing=0 WHERE unsub_token=?", (token,))
    db.execute("UPDATE subscribers SET active=0 WHERE unsub_token=?", (token,))
    db.commit()
    return "<body style='font-family:sans-serif;padding:40px'><h2>You're unsubscribed.</h2><p>You won't get news or offers from MANDILAS ONLINE any more. Order emails will still reach you.</p></body>"


# ================================================================ public API: catalog, quote, cart
@app.get("/api/products")
def list_products():
    rows = get_db().execute("SELECT * FROM products ORDER BY created_at ASC").fetchall()
    return jsonify([product_to_dict(r) for r in rows])


@app.get("/api/products/<pid>")
def one_product(pid):
    r = get_db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_to_dict(r)) if r else err("Product not found.", 404)


@app.get("/api/products/<pid>/recommendations")
def recommendations(pid):
    """Same category + similar price + 'people who bought this also bought'."""
    db = get_db()
    base = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not base:
        return jsonify([])
    together = {}
    for o in db.execute("SELECT items FROM orders ORDER BY created_at DESC LIMIT 500"):
        ids = {i["id"] for i in json.loads(o["items"] or "[]")}
        if pid in ids:
            for other in ids - {pid}:
                together[other] = together.get(other, 0) + 1
    scored = []
    for r in db.execute("SELECT * FROM products WHERE id<>?", (pid,)):
        if sum(json.loads(r["sizes"]).values()) <= 0:
            continue
        score = (3 if r["category"] == base["category"] else 0) + (1 if abs(r["price"] - base["price"]) <= 0.35 * base["price"] else 0) + 5 * together.get(r["id"], 0)
        scored.append((score, r["created_at"], r))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return jsonify([product_to_dict(r) for _, _, r in scored[:4]])


@app.post("/api/quote")
def quote():
    d = request.get_json(silent=True) or {}
    db = get_db()
    try:
        lines, subtotal = price_items(db, d.get("items"))
        discount, drow = apply_discount(db, d.get("code"), subtotal)
    except ValueError as exc:
        return err(str(exc))
    return jsonify(subtotal=subtotal, discount=discount, total=subtotal - discount, code=drow["code"] if drow else "")


@app.post("/api/cart/sync")
def cart_sync():
    d = request.get_json(silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    if not valid_email(email) or not isinstance(d.get("items"), list) or not rate_ok("sync:" + client_ip(), 40, 3600):
        return jsonify(ok=True)
    db = get_db()
    db.execute("""INSERT INTO abandoned_carts (email,name,items,updated_at,reminded,recovered) VALUES (?,?,?,?,0,0)
                  ON CONFLICT(email) DO UPDATE SET name=excluded.name, items=excluded.items,
                  updated_at=excluded.updated_at, reminded=0, recovered=0""",
               (email, str(d.get("name", ""))[:80], json.dumps(d["items"][:30]), time.time()))
    db.commit()
    return jsonify(ok=True)


@app.post("/api/orders")
def create_order():
    d = request.get_json(silent=True) or {}
    ref = str(d.get("reference", "")).strip()
    try:
        cust = read_customer_fields(d)
        if not ref:
            raise ValueError("Missing payment reference.")
        r = paystack("GET", "/transaction/verify/" + ref)
        pay = (r.json() or {}).get("data") or {}
        if not r.ok or pay.get("status") != "success":
            return err("Payment not verified.", 402)
        _, dup = finalize_order(get_db(), ref, cust, d.get("items"), d.get("code"), pay.get("amount", 0), pay.get("currency"))
    except RuntimeError as exc:
        return err(str(exc), 500)
    except requests.RequestException:
        return err("Could not reach Paystack to verify the payment.", 502)
    except ValueError as exc:
        return err(str(exc))
    return jsonify(ok=True, duplicate=dup)


def webhook_signature_ok(raw, headers):
    """Accept Paystack's newer Paystack-Signature (signed with the whsec_ secret) or the older
    X-Paystack-Signature (signed with the secret key). Both are HMAC-SHA512 over the RAW body."""
    new_header, whsec = headers.get("Paystack-Signature", ""), os.environ.get("PAYSTACK_WEBHOOK_SECRET", "")
    if new_header and whsec:
        parts = [p.split("=", 1) for p in new_header.split(",") if "=" in p]
        stamp = next((v for k, v in parts if k.strip() == "t"), "")
        if not stamp.isdigit() or abs(time.time() - int(stamp)) > 300:     # reject old (replayed) deliveries
            return False
        good = hmac.new(whsec.encode(), stamp.encode() + b"." + raw, hashlib.sha512).hexdigest()
        return any(hmac.compare_digest(v.split(":")[-1].strip(), good) for k, v in parts if k.strip() == "v1")
    secret = os.environ.get("PAYSTACK_SECRET_KEY", "")
    if not secret:
        return False
    good = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(headers.get("X-Paystack-Signature", ""), good)


@app.post("/api/paystack/webhook")
def paystack_webhook():
    """Paystack calls this itself, so an order is saved even if the customer closes the tab after paying."""
    if not webhook_signature_ok(request.get_data(), request.headers):
        return "", 401
    event = request.get_json(silent=True) or {}
    if event.get("event") == "charge.success":
        data = event.get("data") or {}
        order = (data.get("metadata") or {}).get("order") or {}
        try:
            finalize_order(get_db(), data.get("reference", ""), read_customer_fields(order), order.get("items"),
                           order.get("code"), data.get("amount", 0), data.get("currency"))
        except Exception as exc:
            print("WEBHOOK ORDER NOT CREATED:", exc)
    return "", 200


@app.get("/api/track")
def track():
    ref, email = request.args.get("reference", "").strip(), request.args.get("email", "").strip().lower()
    if not rate_ok("track:" + client_ip(), 30, 600):
        return err("Too many lookups. Try again shortly.", 429)
    r = get_db().execute("SELECT * FROM orders WHERE reference=? AND lower(email)=?", (ref, email)).fetchone()
    return jsonify(order_to_dict(r)) if r else err("We couldn't find an order with that reference and email.", 404)


# ================================================================ customer: email OTP login
def hash_code(email, code):
    return hmac.new(app.config["SECRET_KEY"].encode(), (email + code).encode(), hashlib.sha256).hexdigest()


@app.post("/api/auth/request-otp")
def request_otp():
    email = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower()
    if not valid_email(email):
        return err("Enter a valid email address.")
    if not rate_ok("otp:" + email, 3, 600) or not rate_ok("otpip:" + client_ip(), 12, 3600):
        return err("Too many codes requested. Please wait a few minutes.", 429)
    code = "%06d" % secrets.randbelow(1000000)
    db = get_db()
    db.execute("INSERT OR REPLACE INTO otps (email,code_hash,expires,attempts) VALUES (?,?,?,0)",
               (email, hash_code(email, code), time.time() + 600))
    db.commit()
    send_email(email, "Your MANDILAS ONLINE login code",
               "Your login code is %s\n\nIt expires in 10 minutes. If you didn't ask for it, ignore this email." % code,
               html=ek.otp_email(code))
    return jsonify(ok=True)


@app.post("/api/auth/verify-otp")
def verify_otp():
    d = request.get_json(silent=True) or {}
    email, code = str(d.get("email", "")).strip().lower(), str(d.get("code", "")).strip()
    db = get_db()
    row = db.execute("SELECT * FROM otps WHERE email=?", (email,)).fetchone()
    if not row or row["expires"] < time.time():
        return err("That code has expired. Request a new one.", 400)
    if row["attempts"] >= 5:
        db.execute("DELETE FROM otps WHERE email=?", (email,))
        db.commit()
        return err("Too many wrong tries. Request a new code.", 429)
    if not hmac.compare_digest(row["code_hash"], hash_code(email, code)):
        db.execute("UPDATE otps SET attempts=attempts+1 WHERE email=?", (email,))
        db.commit()
        return err("Wrong code.", 400)
    db.execute("DELETE FROM otps WHERE email=?", (email,))
    cust = db.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
    if not cust:
        name = str(d.get("name", "")).strip()[:80] or email.split("@")[0]
        db.execute("INSERT INTO customers (email,name,unsub_token,created_at) VALUES (?,?,?,?)",
                   (email, name, secrets.token_urlsafe(16), time.time()))
        cust = db.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
        send_email(email, "Welcome to MANDILAS ONLINE",
                   "Hi %s,\n\nYour account is ready. Browse the catalog, save favourites and track your orders:\n%s\n\nThe House of Feet" % (name, site_url()),
                   html=ek.welcome_email(name))
    db.commit()
    session["cid"] = cust["id"]
    session.permanent = True
    return jsonify(ok=True, customer=customer_to_dict(cust))


@app.post("/api/auth/logout")
def customer_logout():
    session.pop("cid", None)
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    cid = session.get("cid")
    r = get_db().execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone() if cid else None
    return jsonify(customer_to_dict(r) if r else None)


@app.put("/api/me")
@customer_required
def update_me():
    d = request.get_json(silent=True) or {}
    db = get_db()
    db.execute("UPDATE customers SET name=?, phone=?, address=?, city=?, marketing=? WHERE id=?",
               (str(d.get("name", ""))[:80], str(d.get("phone", ""))[:30], str(d.get("address", ""))[:200],
                str(d.get("city", ""))[:80], 1 if d.get("marketing", True) else 0, g.customer["id"]))
    db.commit()
    return jsonify(ok=True)


@app.get("/api/me/orders")
@customer_required
def my_orders():
    rows = get_db().execute("SELECT * FROM orders WHERE lower(email)=? ORDER BY created_at DESC", (g.customer["email"],)).fetchall()
    return jsonify([order_to_dict(r) for r in rows])


@app.get("/api/me/wishlist")
@customer_required
def my_wishlist():
    rows = get_db().execute("""SELECT p.* FROM wishlist w JOIN products p ON p.id=w.product_id
                               WHERE w.customer_id=?""", (g.customer["id"],)).fetchall()
    return jsonify([product_to_dict(r) for r in rows])


@app.post("/api/me/wishlist/<pid>")
@customer_required
def toggle_wishlist(pid):
    db = get_db()
    if not db.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
        return err("Product not found.", 404)
    had = db.execute("DELETE FROM wishlist WHERE customer_id=? AND product_id=?", (g.customer["id"], pid)).rowcount
    if not had:
        db.execute("INSERT INTO wishlist (customer_id,product_id) VALUES (?,?)", (g.customer["id"], pid))
    db.commit()
    return jsonify(ok=True, saved=not had)


@app.get("/api/me/wishlist/ids")
def wishlist_ids():
    cid = session.get("cid")
    rows = get_db().execute("SELECT product_id FROM wishlist WHERE customer_id=?", (cid,)).fetchall() if cid else []
    return jsonify([r["product_id"] for r in rows])


@app.get("/api/me/tickets")
@customer_required
def my_tickets():
    rows = get_db().execute("SELECT * FROM tickets WHERE customer_id=? ORDER BY created_at DESC", (g.customer["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/me/tickets")
@customer_required
def new_ticket():
    d = request.get_json(silent=True) or {}
    kind, msg = d.get("type"), str(d.get("message", "")).strip()[:2000]
    ref = str(d.get("order_ref", "")).strip()
    db = get_db()
    if kind not in ("return", "support") or len(msg) < 5:
        return err("Please choose a type and write a short message.")
    if ref and not db.execute("SELECT 1 FROM orders WHERE reference=? AND lower(email)=?", (ref, g.customer["email"])).fetchone():
        return err("That order isn't on your account.")
    if kind == "return" and not ref:
        return err("Choose the order you want to return.")
    db.execute("INSERT INTO tickets (customer_id,email,order_ref,type,message,created_at) VALUES (?,?,?,?,?,?)",
               (g.customer["id"], g.customer["email"], ref, kind, msg, time.time()))
    db.commit()
    send_email(owner_email(), "New %s request from %s" % (kind, g.customer["email"]),
               "Order: %s\n\n%s\n\nReply in the dashboard: %s/admin" % (ref or "-", msg, site_url()), reply_to=g.customer["email"])
    return jsonify(ok=True)


# ================================================================ admin: auth + products
@app.post("/api/admin/login")
def admin_login():
    if not rate_ok("login:" + client_ip(), 8, 900):
        return err("Too many attempts. Try again in 15 minutes.", 429)
    expected = os.environ.get("ADMIN_PASSWORD", "")
    given = str((request.get_json(silent=True) or {}).get("password", ""))
    if not expected:
        return err("ADMIN_PASSWORD is not set in the server .env file.", 500)
    if not hmac.compare_digest(given.encode(), expected.encode()):
        return err("Wrong passcode.", 401)
    session["admin"] = True
    return jsonify(ok=True)


@app.post("/api/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return jsonify(ok=True)


@app.get("/api/admin/me")
def admin_me():
    return jsonify(admin=bool(session.get("admin")))


@app.post("/api/admin/products")
@admin_required
def add_product():
    f = request.form
    name, category = f.get("name", "").strip()[:120], f.get("category", "")
    try:
        price = float(f.get("price", "0"))
    except ValueError:
        price = 0
    image, video = request.files.get("image"), request.files.get("video")
    if not name or price <= 0 or category not in CATEGORIES or not image or not image.filename:
        return err("Name, price, category and an image are required.")
    saved = []
    try:
        img_name, thumb_name = optimize_image(image)
        saved += [img_name, thumb_name]
        vid_name = save_upload(video, VIDEO_EXT) if video and video.filename else ""
    except ValueError as exc:
        for s_ in saved:
            delete_upload(s_)
        return err(str(exc))
    db = get_db()
    pid = "p_" + uuid.uuid4().hex[:10]
    db.execute("INSERT INTO products (id,name,price,category,colour,img,thumb,video,sizes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (pid, name, price, category, f.get("colour", "").strip()[:60], img_name, thumb_name, vid_name,
                json.dumps(clean_sizes(f.get("sizes", "{}"))), time.time()))
    db.commit()
    notified = 0
    if f.get("notify") in ("1", "true", "on"):
        notified = notify_new_arrival(db, pid)
    return jsonify(ok=True, notified=notified)


@app.put("/api/admin/products/<pid>")
@admin_required
def edit_product(pid):
    d = request.get_json(silent=True) or {}
    db = get_db()
    r = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not r:
        return err("Product not found.", 404)
    name = str(d.get("name", r["name"])).strip()[:120]
    category = d.get("category", r["category"])
    try:
        price = float(d.get("price", r["price"]))
    except (TypeError, ValueError):
        price = 0
    if not name or price <= 0 or category not in CATEGORIES:
        return err("Check the name, price and category.")
    db.execute("UPDATE products SET name=?, price=?, category=?, colour=? WHERE id=?",
               (name, price, category, str(d.get("colour", r["colour"]))[:60], pid))
    db.commit()
    return jsonify(ok=True)


@app.put("/api/admin/products/<pid>/stock")
@admin_required
def update_stock(pid):
    sizes = clean_sizes((request.get_json(silent=True) or {}).get("sizes", {}))
    db = get_db()
    cur = db.execute("UPDATE products SET sizes=? WHERE id=?", (json.dumps(sizes), pid))
    db.commit()
    return jsonify(ok=True) if cur.rowcount else err("Product not found.", 404)


@app.delete("/api/admin/products/<pid>")
@admin_required
def delete_product(pid):
    db = get_db()
    row = db.execute("SELECT img, thumb, video FROM products WHERE id=?", (pid,)).fetchone()
    if not row:
        return err("Product not found.", 404)
    db.execute("DELETE FROM products WHERE id=?", (pid,))
    db.execute("DELETE FROM wishlist WHERE product_id=?", (pid,))
    delete_upload("share_%s.jpg" % pid)
    db.commit()
    delete_upload(row["img"])
    delete_upload(row["thumb"])
    delete_upload(row["video"])
    return jsonify(ok=True)


# ================================================================ admin: orders, delivery, refunds
@app.get("/api/admin/orders")
@admin_required
def admin_orders():
    status = request.args.get("status")
    sql, args = "SELECT * FROM orders", ()
    if status in STATUSES:
        sql, args = sql + " WHERE status=?", (status,)
    rows = get_db().execute(sql + " ORDER BY created_at DESC LIMIT 200", args).fetchall()
    return jsonify([order_to_dict(r) for r in rows])


def get_order(db, ref):
    return db.execute("SELECT * FROM orders WHERE reference=?", (ref,)).fetchone()


@app.put("/api/admin/orders/<ref>/status")
@admin_required
def set_status(ref):
    d = request.get_json(silent=True) or {}
    status, note = d.get("status"), str(d.get("note", "")).strip()[:300]
    db = get_db()
    r = get_order(db, ref)
    if not r:
        return err("Order not found.", 404)
    if status not in STATUSES:
        return err("Unknown status.")
    hist = json.loads(r["history"] or "[]")
    hist.append({"status": status, "at": lagos_now(), "note": note})
    db.execute("UPDATE orders SET status=?, history=?, tracking_note=CASE WHEN ?<>'' THEN ? ELSE tracking_note END WHERE reference=?",
               (status, json.dumps(hist), note, note, ref))
    db.commit()
    if status != r["status"]:
        email_status(order_to_dict(get_order(db, ref)), db)   # dispatch / delivery emails go out automatically
    return jsonify(ok=True)


@app.put("/api/admin/orders/<ref>/delivery")
@admin_required
def set_delivery(ref):
    d = request.get_json(silent=True) or {}
    db = get_db()
    if not get_order(db, ref):
        return err("Order not found.", 404)
    db.execute("UPDATE orders SET rider_name=?, rider_phone=?, eta=?, tracking_note=? WHERE reference=?",
               (str(d.get("rider_name", ""))[:80], str(d.get("rider_phone", ""))[:30], str(d.get("eta", ""))[:80],
                str(d.get("tracking_note", ""))[:300], ref))
    db.commit()
    return jsonify(ok=True)


@app.post("/api/admin/orders/<ref>/refund")
@admin_required
def refund_order(ref):
    d = request.get_json(silent=True) or {}
    db = get_db()
    r = get_order(db, ref)
    if not r:
        return err("Order not found.", 404)
    if r["refund_status"] == "refunded":
        return err("This order was already refunded.")
    try:
        amount = float(d.get("amount") or r["total"])
    except ValueError:
        return err("Invalid amount.")
    if not 0 < amount <= r["total"]:
        return err("Refund must be between 1 and the order total.")
    try:
        resp = paystack("POST", "/refund", json={"transaction": ref, "amount": int(round(amount * 100))})
        body = resp.json() or {}
    except RuntimeError as exc:
        return err(str(exc), 500)
    except (requests.RequestException, ValueError):
        return err("Could not reach Paystack.", 502)
    if not resp.ok or not body.get("status"):
        return err("Paystack refused the refund: " + str(body.get("message", "unknown error")), 502)
    full = amount >= r["total"]
    hist = json.loads(r["history"] or "[]")
    hist.append({"status": "refunded" if full else r["status"], "at": lagos_now(), "note": "Refund of %s started" % naira(amount)})
    if d.get("restock"):
        for it in json.loads(r["items"] or "[]"):
            p = db.execute("SELECT sizes FROM products WHERE id=?", (it["id"],)).fetchone()
            if p:
                sizes = json.loads(p["sizes"])
                sizes[it["size"]] = sizes.get(it["size"], 0) + it["qty"]
                db.execute("UPDATE products SET sizes=? WHERE id=?", (json.dumps(sizes), it["id"]))
    db.execute("UPDATE orders SET refund_status=?, refund_amount=?, status=?, history=? WHERE reference=?",
               ("refunded", amount, "refunded" if full else r["status"], json.dumps(hist), ref))
    db.commit()
    send_email(r["email"], "Your refund for order " + ref,
               "Hi %s,\n\nWe've started a refund of %s for order %s. Your bank may take a few days to show it.\n\nThe House of Feet" % (r["name"], naira(amount), ref),
               html=ek.refund_email(r["name"], amount, ref))
    return jsonify(ok=True)


@app.get("/api/admin/tickets")
@admin_required
def admin_tickets():
    return jsonify([dict(r) for r in get_db().execute("SELECT * FROM tickets ORDER BY created_at DESC LIMIT 200")])


@app.put("/api/admin/tickets/<int:tid>")
@admin_required
def update_ticket(tid):
    d = request.get_json(silent=True) or {}
    status, reply = d.get("status"), str(d.get("reply", "")).strip()[:2000]
    if status not in ("open", "in_review", "approved", "rejected", "resolved"):
        return err("Unknown status.")
    db = get_db()
    t = db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t:
        return err("Ticket not found.", 404)
    db.execute("UPDATE tickets SET status=?, reply=? WHERE id=?", (status, reply, tid))
    db.commit()
    if reply and reply != t["reply"]:
        send_email(t["email"], "Update on your %s request" % t["type"],
                   "Hi,\n\nStatus: %s\n\n%s\n\nThe House of Feet" % (status.replace("_", " ").title(), reply),
                   html=ek.ticket_reply_email(t["type"], status.replace("_", " ").title(), reply))
    return jsonify(ok=True)


# ================================================================ admin: customers, discounts, campaigns, stats
@app.get("/api/admin/customers")
@admin_required
def admin_customers():
    db = get_db()
    out = []
    for c in db.execute("SELECT * FROM customers ORDER BY created_at DESC"):
        o = db.execute("SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM orders WHERE lower(email)=? AND status NOT IN ('cancelled','refunded')", (c["email"],)).fetchone()
        out.append(dict(customer_to_dict(c), orders=o["n"], spent=o["s"], joined=c["created_at"]))
    return jsonify(out)


@app.get("/api/admin/discounts")
@admin_required
def list_discounts():
    return jsonify([dict(r) for r in get_db().execute("SELECT * FROM discounts ORDER BY rowid DESC")])


@app.post("/api/admin/discounts")
@admin_required
def add_discount():
    d = request.get_json(silent=True) or {}
    code = str(d.get("code", "")).strip().upper()[:30]
    try:
        value, min_total, limit = float(d.get("value", 0)), float(d.get("min_total") or 0), int(d.get("usage_limit") or 0)
    except ValueError:
        return err("Check the numbers.")
    kind = d.get("kind")
    if not code.isalnum() or kind not in ("percent", "fixed") or value <= 0 or (kind == "percent" and value > 90):
        return err("Use letters/numbers for the code, a type, and a sensible value (percent max 90).")
    db = get_db()
    try:
        db.execute("INSERT INTO discounts (code,kind,value,min_total,usage_limit,expires) VALUES (?,?,?,?,?,?)",
                   (code, kind, value, min_total, limit, str(d.get("expires", ""))[:10]))
    except sqlite3.IntegrityError:
        return err("That code already exists.")
    db.commit()
    return jsonify(ok=True)


@app.put("/api/admin/discounts/<code>")
@admin_required
def toggle_discount(code):
    db = get_db()
    db.execute("UPDATE discounts SET active=1-active WHERE code=?", (code.upper(),))
    db.commit()
    return jsonify(ok=True)


@app.delete("/api/admin/discounts/<code>")
@admin_required
def delete_discount(code):
    db = get_db()
    db.execute("DELETE FROM discounts WHERE code=?", (code.upper(),))
    db.commit()
    return jsonify(ok=True)


@app.get("/api/admin/campaigns")
@admin_required
def list_campaigns():
    return jsonify([dict(r) for r in get_db().execute("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 30")])


def marketing_recipients(db):
    """Customers who allowed news + newsletter subscribers, no duplicates."""
    seen, out = set(), []
    q = ("SELECT email,name,unsub_token FROM customers WHERE marketing=1 UNION ALL "
         "SELECT email,'',unsub_token FROM subscribers WHERE active=1")
    for r in db.execute(q):
        if r["email"] not in seen:
            seen.add(r["email"])
            out.append({"email": r["email"], "name": r["name"], "unsub_token": r["unsub_token"]})
    return out


def blast(people, subject, text_fn, html_fn, images=None):
    def run():
        for person in people:
            send_email(person["email"], subject, text_fn(person), html=html_fn(person), images=images)
            time.sleep(1.5)                 # gentle pace so Gmail doesn't flag it
    threading.Thread(target=run, daemon=True).start()


def unsub_url(person):
    return "%s/unsubscribe/%s" % (site_url(), person["unsub_token"])


def notify_new_arrival(db, pid):
    row = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    people = marketing_recipients(db)
    if not row or not people:
        return 0
    cids, imgs = email_images(db, [pid])
    prod, url = product_to_dict(row), "%s/product/%s" % (site_url(), pid)
    blast(people, "New arrival: %s" % row["name"],
          lambda p_: "New at MANDILAS ONLINE: %s - %s\n%s\n\nUnsubscribe: %s" % (row["name"], naira(row["price"]), url, unsub_url(p_)),
          lambda p_: ek.arrival_email(prod, cids.get(pid), url, unsub_url(p_)), imgs)
    return len(people)


@app.get("/api/admin/audience")
@admin_required
def audience():
    db = get_db()
    return jsonify(customers=db.execute("SELECT COUNT(*) FROM customers WHERE marketing=1").fetchone()[0],
                   subscribers=db.execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0],
                   total=len(marketing_recipients(db)))


@app.post("/api/admin/campaigns")
@admin_required
def send_campaign():
    d = request.get_json(silent=True) or {}
    subject, body = str(d.get("subject", "")).strip()[:150], str(d.get("body", "")).strip()[:5000]
    cta_label, cta_url = str(d.get("cta_label", "")).strip()[:40], str(d.get("cta_url", "")).strip()[:300]
    if cta_url and not cta_url.startswith(("http://", "https://", "/")):
        return err("The button link must start with http:// or https://")
    if cta_url.startswith("/"):
        cta_url = site_url() + cta_url
    if not subject or len(body) < 5:
        return err("Write a subject and a message.")
    db = get_db()
    if d.get("test"):
        send_email(owner_email(), "[TEST] " + subject, body, html=ek.campaign_email(subject, body, cta_label, cta_url, site_url() + "/unsubscribe/test"))
        return jsonify(ok=True, sent=1)
    people = marketing_recipients(db)
    db.execute("INSERT INTO campaigns (subject,body,sent_count,created_at) VALUES (?,?,?,?)", (subject, body, len(people), time.time()))
    db.commit()
    blast(people, subject, lambda p_: "Hi %s,\n\n%s\n\n--\nUnsubscribe: %s" % (p_["name"] or "there", body, unsub_url(p_)),
          lambda p_: ek.campaign_email(subject, body, cta_label, cta_url, unsub_url(p_)))
    return jsonify(ok=True, sent=len(people))


@app.post("/api/subscribe")
def subscribe():
    email = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower()
    if not valid_email(email):
        return err("Enter a valid email address.")
    if not rate_ok("sub:" + client_ip(), 6, 3600):
        return err("Too many tries. Please wait a little.", 429)
    db = get_db()
    row = db.execute("SELECT * FROM subscribers WHERE email=?", (email,)).fetchone()
    if row and row["active"]:
        return jsonify(ok=True)
    token = row["unsub_token"] if row else secrets.token_urlsafe(16)
    db.execute("INSERT OR REPLACE INTO subscribers (email,unsub_token,active,created_at) VALUES (?,?,1,?)", (email, token, time.time()))
    db.commit()
    link = "%s/unsubscribe/%s" % (site_url(), token)
    send_email(email, "You're on the MANDILAS ONLINE list", "Thanks for joining! You'll hear first about new arrivals and offers.\n\nUnsubscribe: " + link,
               html=ek.subscribed_email(link))
    return jsonify(ok=True)


@app.get("/api/admin/stats")
@admin_required
def stats():
    db = get_db()
    live = [r for r in db.execute("SELECT * FROM orders") if (r["status"] or "paid") not in ("cancelled", "refunded")]
    today = datetime.now(LAGOS).date()
    days = {(today - timedelta(days=i)).isoformat(): 0 for i in range(6, -1, -1)}
    top, revenue, today_rev = {}, 0, 0
    for r in live:
        day = datetime.fromtimestamp(r["created_at"], LAGOS).date().isoformat()
        revenue += r["total"]
        if day in days:
            days[day] += r["total"]
        if day == today.isoformat():
            today_rev += r["total"]
        for it in json.loads(r["items"] or "[]"):
            top[it["name"]] = top.get(it["name"], 0) + it["qty"]
    low = []
    for p in db.execute("SELECT * FROM products"):
        s = json.loads(p["sizes"])
        if sum(s.values()) <= 3:
            low.append({"id": p["id"], "name": p["name"], "left": sum(s.values())})
    count = lambda sql: db.execute(sql).fetchone()[0]
    return jsonify(revenue=revenue, today=today_rev, orders=len(live),
                   to_ship=count("SELECT COUNT(*) FROM orders WHERE status IN ('paid','processing')"),
                   customers=count("SELECT COUNT(*) FROM customers"), products=count("SELECT COUNT(*) FROM products"),
                   open_tickets=count("SELECT COUNT(*) FROM tickets WHERE status IN ('open','in_review')"),
                   days=[{"day": k, "total": v} for k, v in days.items()],
                   top=sorted(({"name": k, "qty": v} for k, v in top.items()), key=lambda x: -x["qty"])[:5], low=low)


# ================================================================ analytics + reports
def build_report(db, days):
    now = datetime.now(LAGOS)
    start = (now - timedelta(days=days)).timestamp()
    rows = db.execute("SELECT * FROM orders WHERE created_at>=?", (start,)).fetchall()
    cat_of = {p["id"]: p["category"] for p in db.execute("SELECT id, category FROM products")}
    live = [r for r in rows if (r["status"] or "paid") not in ("cancelled", "refunded")]
    revenue = sum(r["total"] for r in live)
    series, cats, top, buyers = {}, {}, {}, {}
    for r in live:
        d = datetime.fromtimestamp(r["created_at"], LAGOS).date()
        key = d.isoformat() if days <= 31 else (d - timedelta(days=d.weekday())).isoformat()
        s_ = series.setdefault(key, {"label": key[5:], "total": 0, "orders": 0})
        s_["total"] += r["total"]
        s_["orders"] += 1
        buyers[r["email"]] = buyers.get(r["email"], 0) + r["total"]
        for it in json.loads(r["items"] or "[]"):
            c = cat_of.get(it["id"], "Other")
            cats[c] = cats.get(c, 0) + it["price"] * it["qty"]
            t = top.setdefault(it["name"], {"name": it["name"], "qty": 0, "revenue": 0})
            t["qty"] += it["qty"]
            t["revenue"] += it["price"] * it["qty"]
    names = {r["email"]: r["name"] for r in rows}
    return {"days": days, "revenue": revenue, "orders": len(live), "aov": (revenue / len(live)) if live else 0,
            "discounts": sum(r["discount"] or 0 for r in live),
            "refunds": sum(r["refund_amount"] or 0 for r in rows if r["refund_status"] == "refunded"),
            "cancelled": len([r for r in rows if r["status"] == "cancelled"]),
            "new_customers": db.execute("SELECT COUNT(*) FROM customers WHERE created_at>=?", (start,)).fetchone()[0],
            "subscribers": db.execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0],
            "series": [series[k] for k in sorted(series)],
            "by_category": sorted(({"name": k, "total": v} for k, v in cats.items()), key=lambda x: -x["total"]),
            "top": sorted(top.values(), key=lambda x: -x["qty"])[:8],
            "customers": sorted(({"email": k, "name": names.get(k, ""), "spent": v} for k, v in buyers.items()), key=lambda x: -x["spent"])[:8]}


@app.get("/api/admin/report")
@admin_required
def report():
    days = request.args.get("days", "30", type=str)
    days = int(days) if days in ("7", "30", "90", "365") else 30
    return jsonify(build_report(get_db(), days))


def _safe_cell(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in "=+-@" else v            # stops spreadsheet formula tricks


@app.get("/api/admin/report.csv")
@admin_required
def report_csv():
    days = request.args.get("days", "30", type=str)
    days = int(days) if days in ("7", "30", "90", "365") else 30
    rows = get_db().execute("SELECT * FROM orders WHERE created_at>=? ORDER BY created_at DESC", (time.time() - days * 86400,)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["reference", "date", "status", "name", "email", "phone", "address", "city", "items", "subtotal", "discount", "code", "total", "refunded"])
    for r in rows:
        o = order_to_dict(r)
        w.writerow([_safe_cell(x) for x in (o["reference"], datetime.fromtimestamp(o["created_at"], LAGOS).strftime("%Y-%m-%d %H:%M"), o["status"], o["name"], o["email"],
                    o["phone"], o["address"], o["city"], "; ".join("%s (%s) x%d" % (i["name"], i["size"], i["qty"]) for i in o["items"]),
                    o["subtotal"], o["discount"], o["discount_code"], o["total"], o["refund_amount"])])
    return out.getvalue(), 200, {"Content-Type": "text/csv", "Content-Disposition": "attachment; filename=mandilas-orders-%dd.csv" % days}


def weekly_report_job(force=False):
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    week = datetime.now(LAGOS).strftime("%G-W%V")
    row = db.execute("SELECT v FROM meta WHERE k='weekly_sent'").fetchone()
    if row and row["v"] == week and not force:
        db.close()
        return False
    rep_ = build_report(db, 7)
    send_email(owner_email(), "Your MANDILAS ONLINE weekly report",
               "Last 7 days: %s revenue from %d orders (average %s).\nOpen the dashboard for the full report: %s/admin" % (naira(rep_["revenue"]), rep_["orders"], naira(rep_["aov"]), site_url()),
               html=ek.weekly_email(rep_, site_url() + "/admin"))
    db.execute("INSERT OR REPLACE INTO meta (k,v) VALUES ('weekly_sent',?)", (week,))
    db.commit()
    db.close()
    return True


# ================================================================ automation: abandoned carts
def abandoned_cart_job():
    """Emails shoppers who left items behind 1-48 hours ago (once each)."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    now = time.time()
    rows = db.execute("SELECT * FROM abandoned_carts WHERE reminded=0 AND recovered=0 AND updated_at<? AND updated_at>?",
                      (now - 3600, now - 48 * 3600)).fetchall()
    sent = 0
    for r in rows:
        try:
            lines, subtotal = price_items(db, json.loads(r["items"]))
        except ValueError:
            db.execute("UPDATE abandoned_carts SET reminded=1 WHERE email=?", (r["email"],))
            continue
        cids, imgs = email_images(db, [l["id"] for l in lines])
        send_email(r["email"], "You left something behind at MANDILAS ONLINE",
                   "Hi %s,\n\nYour pairs are still waiting:\n%s\n\nTotal: %s\n\nPick up where you left off: %s\n\nSizes can sell out, so don't wait too long.\n\nThe House of Feet"
                   % (r["name"] or "there", items_text(lines), naira(subtotal), site_url()),
                   html=ek.abandoned_email(r["name"], lines, cids, subtotal), images=imgs)
        db.execute("UPDATE abandoned_carts SET reminded=1 WHERE email=?", (r["email"],))
        sent += 1
    db.commit()
    db.close()
    return sent


@app.get("/api/cron/abandoned")
def cron_abandoned():
    key = os.environ.get("CRON_KEY")
    if not key or not hmac.compare_digest(request.args.get("key", ""), key):
        return err("Not allowed.", 403)
    return jsonify(sent=abandoned_cart_job())


@app.get("/api/cron/weekly")
def cron_weekly():
    key = os.environ.get("CRON_KEY")
    if not key or not hmac.compare_digest(request.args.get("key", ""), key):
        return err("Not allowed.", 403)
    return jsonify(sent=weekly_report_job())


def start_scheduler():
    def loop():
        while True:
            time.sleep(15 * 60)
            try:
                abandoned_cart_job()
                now = datetime.now(LAGOS)
                if now.weekday() == 0 and now.hour >= 8:      # Monday morning, once a week
                    weekly_report_job()
            except Exception as exc:
                print("SCHEDULER ERROR:", exc)
    threading.Thread(target=loop, daemon=True).start()


init_db()
if os.environ.get("RUN_SCHEDULER") == "1":
    start_scheduler()

if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":      # only the reloader child, so one scheduler
        start_scheduler()
    app.run(debug=True)