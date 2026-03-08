"""
=============================================================
  अक्षय मल्टी सर्व्हिसेस - WhatsApp Chatbot with PhonePe Payment
  Features:
    - Session-based conversation flow (Redis)
    - Service selection → Document upload → Payment → Confirmation
    - Google Sheets logging (monthly, auto-created, row-updatable)
    - Admin alert on payment completion (client no. + service)
    - Daily 9 PM IST sheet link to admin
    - Full Marathi UI
=============================================================
"""

import os
import time
import hashlib
import json
import base64
import logging
import uuid
from datetime import datetime

import pytz
import requests
from dotenv import load_dotenv
from flask import Flask, request
from apscheduler.schedulers.background import BackgroundScheduler
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
except ImportError:
    gspread = None
    ServiceAccountCredentials = None

try:
    import redis
except ImportError:
    redis = None

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Environment
# ─────────────────────────────────────────────
load_dotenv()

IST = pytz.timezone("Asia/Kolkata")

# Twilio
TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "whatsapp:+14155238886")
ADMIN_NUMBER        = os.getenv("ADMIN_NUMBER", "")          # e.g. whatsapp:+919876543210

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("Twilio client initialised.")
    except Exception:
        logger.exception("Twilio init error")

# PhonePe
PHONEPE_MERCHANT_ID  = os.getenv("PHONEPE_MERCHANT_ID", "")
PHONEPE_SALT_KEY     = os.getenv("PHONEPE_SALT_KEY", "")
PHONEPE_SALT_INDEX   = os.getenv("PHONEPE_SALT_INDEX", "1")
PHONEPE_ENV          = os.getenv("PHONEPE_ENV", "UAT")       # UAT | PROD
PHONEPE_BASE_URL = (
    "https://api-preprod.phonepe.com/apis/pg-sandbox"
    if PHONEPE_ENV == "UAT"
    else "https://api.phonepe.com/apis/hermes"
)
PHONEPE_CALLBACK_BASE = os.getenv("PHONEPE_CALLBACK_BASE", "https://your-domain.com")

# Google Sheets
SHEET_PREFIX       = os.getenv("SHEET_PREFIX", "AMS-Applications")
GOOGLE_CREDS_JSON  = os.getenv("GOOGLE_CREDS_JSON", "")
GOOGLE_CREDS_FILE  = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

sheets_client = None
if gspread:
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        if GOOGLE_CREDS_JSON:
            creds_dict = json.loads(GOOGLE_CREDS_JSON)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            sheets_client = gspread.authorize(creds)
            logger.info("Google Sheets client initialised from GOOGLE_CREDS_JSON.")
        elif GOOGLE_CREDS_FILE and os.path.exists(GOOGLE_CREDS_FILE):
            creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_FILE, scope)
            sheets_client = gspread.authorize(creds)
            logger.info("Google Sheets client initialised from file.")
    except Exception:
        logger.exception("Google Sheets init error")

# Redis
REDIS_URL    = os.getenv("REDIS_URL", "")
redis_client = None
if redis and REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL)
        redis_client.ping()
        logger.info("Redis connected.")
    except Exception:
        logger.exception("Redis connection error")

# ─────────────────────────────────────────────
# Flask App
# ─────────────────────────────────────────────
app = Flask(__name__)

# ─────────────────────────────────────────────
# Constants & Content
# ─────────────────────────────────────────────
SESSION_TIMEOUT = 20 * 60  # 20 minutes inactivity

SERVICES = {
    "1": {
        "name": "डोमासाईल",
        "documents": [
            "आधार कार्ड पुढून (Front)",
            "आधार कार्ड माघून (Back)",
            "शाळा सोडल्याचा दाखला / बोनफाईड",
            "रेशन कार्ड पुढून (Front)",
            "रेशन कार्ड माघून (Back)",
        ],
        "amount_paise": 20000,   # ₹200
    },
    "2": {
        "name": "Nationality Certificate",
        "documents": [
            "आधार कार्ड पुढून (Front)",
            "आधार कार्ड माघून (Back)",
            "शाळा सोडल्याचा दाखला / बोनफाईड",
            "रेशन कार्ड पुढून (Front)",
            "रेशन कार्ड माघून (Back)",
        ],
        "amount_paise": 20000,
    },
    "3": {
        "name": "उत्पन्न दाखला",
        "documents": [
            "तलाठी उत्पन्न दाखला",
            "रेशन कार्ड पुढून (Front)",
            "रेशन कार्ड माघून (Back)",
        ],
        "amount_paise": 20000,
    },
    "4": {
        "name": "नॉन क्रीमीलेअर दाखला",
        "documents": [
            "३ वर्ष तहसील उत्पन्न दाखला",
            "जातीचा दाखला",
            "रेशन कार्ड पुढून (Front)",
            "रेशन कार्ड माघून (Back)",
            "शाळेचा दाखला",
            "डोमासाईल",
        ],
        "amount_paise": 30000,
    },
    "5": {
        "name": "मराठा जातीचा दाखला",
        "documents": [
            "अर्जदार शाळेचा दाखला",
            "वडिलांचा शाळेचा दाखला",
            "आजोबांचा शाळेचा दाखला",
        ],
        "amount_paise": 50000,
    },
}

WELCOME_MSG = (
    "🙏 नमस्कार {name}!\n\n"
    "आपले *अक्षय मल्टी सर्व्हिसेस* मध्ये मनःपूर्वक स्वागत आहे.\n\n"
    "📌 टीप: ही सुविधा केवळ *पारनेर तालुक्यातील नागरिकांसाठी* उपलब्ध आहे.\n\n"
    "कृपया आपल्या गरजेनुसार सेवा निवडा (क्रमांक टाइप करा):\n"
)

MENU_TEXT = (
    "1️⃣  डोमासाईल  –  ₹200\n"
    "2️⃣  Nationality Certificate  –  ₹200\n"
    "3️⃣  उत्पन्न दाखला  –  ₹200\n"
    "4️⃣  नॉन क्रीमीलेअर दाखला  –  ₹300\n"
    "5️⃣  मराठा जातीचा दाखला  –  ₹500\n\n"
    "0️⃣  बाहेर पडा / Exit"
)

SHEET_HEADERS = [
    "Timestamp",
    "Phone",
    "Customer Name",
    "Service",
    "Service Fee (₹)",
    "Documents Uploaded",
    "Total Docs Required",
    "Docs Status",
    "Payment Status",
    "Transaction ID",
    "PhonePe Payment ID",
    "Payment Time",
    "Session Start",
]

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def now_ts() -> int:
    return int(time.time())


def now_ist_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def paise_to_rupees(paise: int) -> str:
    return str(paise // 100)


def profile_name() -> str:
    """Extract WhatsApp display name from Twilio payload."""
    return request.values.get("ProfileName") or "ग्राहक"


# ─────────────────────────────────────────────
# Session Management (Redis)
# ─────────────────────────────────────────────
def get_session(user: str) -> dict | None:
    if not redis_client:
        return None
    raw = redis_client.get(user)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def save_session(user: str, session: dict):
    if not redis_client:
        return
    session["last_active"] = now_ts()
    redis_client.setex(user, SESSION_TIMEOUT, json.dumps(session))


def start_new_session(user: str, name: str) -> dict:
    session = {
        "start_time": now_ist_str(),
        "last_active": now_ts(),
        "step": "menu",
        "user_name": name,
        "selected_service": None,
        "doc_progress": {},          # {doc_name: media_url}
        "doc_order": [],             # ordered list of doc names received
        "merchant_transaction_id": None,
        "payment_status": "Pending",
        "sheet_row": None,           # row number in Google Sheet for update
    }
    save_session(user, session)
    return session


def end_session(user: str):
    if redis_client:
        redis_client.delete(user)


# ─────────────────────────────────────────────
# Document helpers
# ─────────────────────────────────────────────
def build_docs_list(service_key: str) -> str:
    docs = SERVICES[service_key]["documents"]
    lines = "\n".join(f"  {i}. {d}" for i, d in enumerate(docs, 1))
    return f"📋 *आवश्यक कागदपत्रे:*\n{lines}\n\nकृपया वरील क्रमाने एक एक कागदपत्र पाठवा."


def next_required_doc(session: dict) -> str | None:
    svc = session.get("selected_service")
    if not svc:
        return None
    docs = SERVICES[svc]["documents"]
    uploaded = session.get("doc_progress", {})
    for d in docs:
        if d not in uploaded:
            return d
    return None


def docs_progress_summary(session: dict) -> str:
    svc = session.get("selected_service")
    if not svc:
        return ""
    docs = SERVICES[svc]["documents"]
    uploaded = session.get("doc_progress", {})
    total = len(docs)
    done  = sum(1 for d in docs if d in uploaded)
    return f"{done}/{total}"


# ─────────────────────────────────────────────
# Google Sheets
# ─────────────────────────────────────────────
def _get_or_create_monthly_sheet():
    """Return (spreadsheet, worksheet). Creates sheet + header if missing."""
    month_str = datetime.now(IST).strftime("%Y-%m")
    name = f"{SHEET_PREFIX}-{month_str}"
    try:
        sh = sheets_client.open(name)
        ws = sh.sheet1
        # Make sure headers exist
        if ws.row_count == 0 or not ws.row_values(1):
            ws.insert_row(SHEET_HEADERS, 1)
        return sh, ws
    except gspread.exceptions.SpreadsheetNotFound:
        pass
    except Exception:
        logger.exception("Error opening sheet '%s'", name)
        raise

    # Create new spreadsheet
    try:
        sh = sheets_client.create(name)
        ws = sh.sheet1
        ws.insert_row(SHEET_HEADERS, 1)
        # Make it accessible to anyone with the link (optional – comment out if unwanted)
        sh.share(None, perm_type="anyone", role="reader")
        logger.info("Created new monthly sheet: %s", name)
        return sh, ws
    except Exception:
        logger.exception("Failed to create monthly sheet '%s'", name)
        raise


def sheet_append_row(user: str, session: dict) -> int | None:
    """Append initial row when docs are complete; returns 1-based row index."""
    if not sheets_client:
        return None
    try:
        sh, ws = _get_or_create_monthly_sheet()
        svc     = session.get("selected_service", "")
        svc_obj = SERVICES.get(svc, {})
        docs_uploaded = list(session.get("doc_progress", {}).keys())
        total_docs    = len(svc_obj.get("documents", []))

        row = [
            now_ist_str(),                                   # Timestamp
            user.replace("whatsapp:", ""),                   # Phone
            session.get("user_name", ""),                    # Customer Name
            svc_obj.get("name", ""),                         # Service
            paise_to_rupees(svc_obj.get("amount_paise", 0)), # Service Fee
            ", ".join(docs_uploaded),                        # Documents Uploaded
            str(total_docs),                                 # Total Docs Required
            f"{len(docs_uploaded)}/{total_docs}",            # Docs Status
            "Pending",                                       # Payment Status
            session.get("merchant_transaction_id", ""),      # Transaction ID
            "",                                              # PhonePe Payment ID
            "",                                              # Payment Time
            session.get("start_time", ""),                   # Session Start
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        # gspread doesn't return the row index from append_row cleanly;
        # we fetch the last row number manually
        row_idx = len(ws.get_all_values())
        logger.info("Appended row %d for user %s", row_idx, user)
        return row_idx
    except Exception:
        logger.exception("sheet_append_row error for %s", user)
        return None


def sheet_update_payment(user: str, session: dict, payment_id: str):
    """Update the existing row with payment success info."""
    if not sheets_client:
        return
    try:
        _, ws = _get_or_create_monthly_sheet()
        row_idx = session.get("sheet_row")
        if not row_idx:
            logger.warning("No sheet_row stored for %s; appending instead.", user)
            sheet_append_row(user, session)
            return

        # Column indices (1-based) matching SHEET_HEADERS
        col_payment_status = SHEET_HEADERS.index("Payment Status") + 1          # 9
        col_payment_id     = SHEET_HEADERS.index("PhonePe Payment ID") + 1      # 11
        col_payment_time   = SHEET_HEADERS.index("Payment Time") + 1            # 12

        ws.update_cell(row_idx, col_payment_status, "Completed ✅")
        ws.update_cell(row_idx, col_payment_id,     payment_id)
        ws.update_cell(row_idx, col_payment_time,   now_ist_str())
        logger.info("Updated payment row %d for %s", row_idx, user)
    except Exception:
        logger.exception("sheet_update_payment error for %s", user)


# ─────────────────────────────────────────────
# Twilio Outbound Messaging
# ─────────────────────────────────────────────
def send_whatsapp(to: str, body: str):
    """Send an outbound WhatsApp message via Twilio."""
    if not twilio_client:
        logger.warning("send_whatsapp: Twilio not configured. Skipping to=%s", to)
        return
    try:
        twilio_client.messages.create(
            from_=TWILIO_PHONE_NUMBER,
            to=to,
            body=body,
        )
        logger.info("Sent WhatsApp to %s", to)
    except Exception:
        logger.exception("send_whatsapp error to %s", to)


# ─────────────────────────────────────────────
# Daily Scheduler – 9 PM IST
# ─────────────────────────────────────────────
def send_daily_sheet_link():
    logger.info("Scheduler: send_daily_sheet_link triggered.")
    if not all([sheets_client, twilio_client, ADMIN_NUMBER]):
        logger.warning("Scheduler: Missing sheets/twilio/admin config — skipping.")
        return
    try:
        sh, ws = _get_or_create_monthly_sheet()
        sheet_url  = f"https://docs.google.com/spreadsheets/d/{sh.id}"
        today_str  = datetime.now(IST).strftime("%d %B %Y")
        all_rows   = ws.get_all_values()
        # Count today's records (excluding header row)
        today_date = datetime.now(IST).strftime("%Y-%m-%d")
        today_count = sum(
            1 for row in all_rows[1:]
            if row and row[0].startswith(today_date)
        )
        body = (
            f"📊 *Daily Report – {today_str}*\n\n"
            f"आजचे अर्ज: *{today_count}*\n"
            f"एकूण (या महिन्यात): *{max(0, len(all_rows) - 1)}*\n\n"
            f"📎 Sheet Link:\n{sheet_url}"
        )
        send_whatsapp(ADMIN_NUMBER, body)
        logger.info("Daily sheet link sent to admin.")
    except Exception:
        logger.exception("Scheduler send_daily_sheet_link error")


# ─────────────────────────────────────────────
# UPI Payment
# ─────────────────────────────────────────────
UPI_ID   = "sarthakmandage7474@ibl"
UPI_NAME = "Akshay Multi Services"

def create_phonepe_payment_link(user: str, service_key: str) -> tuple[str | None, str]:
    """Returns (txn_id, message_text) using UPI deep link."""
    service  = SERVICES[service_key]
    amount   = paise_to_rupees(service["amount_paise"])
    txn_id   = str(uuid.uuid4()).replace("-", "")[:35]
    note     = service["name"].replace(" ", "%20")

    upi_link = (
        f"upi://pay?pa={UPI_ID}"
        f"&pn={UPI_NAME.replace(' ', '%20')}"
        f"&am={amount}"
        f"&cu=INR"
        f"&tn={note}"
    )

    msg = (
        f"💳 *पेमेंट करा*\n\n"
        f"सेवा: *{service['name']}*\n"
        f"रक्कम: *₹{amount}*\n"
        f"UPI ID: *{UPI_ID}*\n\n"
        f"👇 खालील लिंकवर क्लिक करून PhonePe / GPay / UPI ने पेमेंट करा:\n"
        f"{upi_link}\n\n"
        f"✅ पेमेंट झाल्यावर *PAID* असे टाइप करा."
    )
    return txn_id, msg


# ─────────────────────────────────────────────
# Main WhatsApp Webhook
# ─────────────────────────────────────────────
@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    user     = request.values.get("From", "")
    body_raw = (request.values.get("Body") or "").strip()
    incoming = body_raw.lower()
    num_media = int(request.values.get("NumMedia", "0"))

    resp = MessagingResponse()
    msg  = resp.message()

    # ── Guard: Redis required ──
    if not redis_client:
        msg.body("⚠️ क्षमस्व, तांत्रिक अडचणींमुळे सेवा तात्पुरती अनुपलब्ध आहे. कृपया नंतर प्रयत्न करा.")
        return str(resp)

    # ── Load or create session ──
    session = get_session(user)

    # Any first message (or expired session) → start fresh
    if not session:
        name    = profile_name()
        session = start_new_session(user, name)
        msg.body(WELCOME_MSG.format(name=name) + MENU_TEXT)
        save_session(user, session)
        return str(resp)

    # ── Global: exit command ──
    if incoming in ("0", "exit", "quit", "bye", "बाहेर"):
        end_session(user)
        msg.body(
            "✅ तुम्ही सेशन बंद केले.\n\n"
            "पुन्हा सुरू करण्यासाठी कोणताही मेसेज पाठवा. 🙏"
        )
        return str(resp)

    # ── Global: restart keyword ──
    if incoming in ("hi", "hello", "start", "menu", "नमस्कार", "हाय"):
        name    = profile_name()
        session = start_new_session(user, name)
        msg.body(WELCOME_MSG.format(name=name) + MENU_TEXT)
        save_session(user, session)
        return str(resp)

    step = session.get("step", "menu")

    # ══════════════════════════════
    # STEP: menu – service selection
    # ══════════════════════════════
    if step == "menu":
        if incoming in SERVICES:
            svc = SERVICES[incoming]
            session.update({
                "selected_service": incoming,
                "doc_progress":     {},
                "doc_order":        [],
                "step":             "docs",
            })
            # Log to sheet immediately when service is selected
            row_idx = sheet_append_row(user, session)
            session["sheet_row"] = row_idx
            save_session(user, session)
            first_doc = svc["documents"][0]
            reply = (
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ *{svc['name']}* — अर्ज सुरू\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 सेवा शुल्क: *₹{paise_to_rupees(svc['amount_paise'])}*\n\n"
                f"{build_docs_list(incoming)}\n"
                f"─────────────────────\n"
                f"📤 *पहिले कागदपत्र पाठवा:*\n"
                f"👉 {first_doc}"
            )
            msg.body(reply)
        else:
            msg.body(f"❓ कृपया 1 ते 5 मधील क्रमांक टाइप करा.\n\n{MENU_TEXT}")

    # ══════════════════════════════
    # STEP: docs – document upload
    # ══════════════════════════════
    elif step == "docs":
        if num_media > 0:
            current_doc = next_required_doc(session)
            if current_doc:
                media_url = request.values.get("MediaUrl0", "")
                media_type = request.values.get("MediaContentType0", "")

                # Accept image/* and application/pdf only
                if not (media_type.startswith("image/") or media_type == "application/pdf"):
                    msg.body(
                        f"⚠️ *चुकीचे स्वरूप!*\n\n"
                        f"*{current_doc}* साठी केवळ\n"
                        f"📷 फोटो (JPG/PNG) किंवा 📄 PDF पाठवा."
                    )
                    save_session(user, session)
                    return str(resp)

                session["doc_progress"][current_doc] = media_url
                session["doc_order"].append(current_doc)

                next_doc = next_required_doc(session)
                progress = docs_progress_summary(session)

                if next_doc:
                    # More docs needed
                    msg.body(
                        f"✅ *{current_doc}*\n"
                        f"📊 प्रगती: {progress}\n\n"
                        f"📤 *पुढील कागदपत्र:*\n"
                        f"👉 {next_doc}"
                    )
                else:
                    # All docs received → generate payment link
                    session["step"] = "payment"
                    txn_id, pay_text = create_phonepe_payment_link(user, session["selected_service"])

                    # Update existing sheet row (created at service selection)
                    if txn_id:
                        session["merchant_transaction_id"] = txn_id
                    if session.get("sheet_row"):
                        sheet_update_payment(user, session, txn_id or "")
                    else:
                        row_idx = sheet_append_row(user, session)
                        session["sheet_row"] = row_idx

                    if txn_id:
                        msg.body(
                            f"🎉 सर्व *{progress}* कागदपत्रे मिळाली!\n\n"
                            f"{pay_text}"
                        )
                    else:
                        msg.body(
                            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🎉 *सर्व कागदपत्रे मिळाली!*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"📋 {pay_text}\n\n"
                            f"📞 पेमेंटसाठी कृपया आमच्याशी\n"
                            f"संपर्क साधा."
                        )
            else:
                # All docs already uploaded but still in docs step (edge case)
                msg.body("✅ सर्व कागदपत्रे आधीच मिळाली आहेत. पेमेंटची प्रतीक्षा करा.")
        else:
            # User sent text instead of media
            required_doc = next_required_doc(session)
            if required_doc:
                progress = docs_progress_summary(session)
                msg.body(
                    f"📎 *{required_doc}*\n"
                    f"वरील कागदपत्राचा फोटो किंवा PDF पाठवा.\n\n"
                    f"📊 प्रगती: {progress}"
                )
            else:
                msg.body("✅ सर्व कागदपत्रे मिळाली आहेत. पेमेंटची वाट पाहत आहोत.")

    # ══════════════════════════════
    # STEP: payment – awaiting payment
    # ══════════════════════════════
    elif step == "payment":
        svc_name = SERVICES.get(session.get("selected_service", ""), {}).get("name", "")
        svc_key  = session.get("selected_service", "")
        amount   = paise_to_rupees(SERVICES[svc_key]["amount_paise"]) if svc_key else ""

        if incoming.strip().upper() == "PAID":
            # Customer confirmed payment manually
            session["step"] = "complete"
            session["payment_status"] = "MANUAL_CONFIRMED"
            sheet_update_payment(user, session, session.get("merchant_transaction_id", "MANUAL"))
            # Notify admin
            if twilio_client and ADMIN_NUMBER:
                try:
                    twilio_client.messages.create(
                        body=(
                            f"✅ *पेमेंट कन्फर्म*\n"
                            f"📱 {user.split(':')[-1]}\n"
                            f"सेवा: {svc_name}\n"
                            f"रक्कम: ₹{amount}\n"
                            f"_(Customer ने PAID confirm केले)_"
                        ),
                        from_=TWILIO_PHONE_NUMBER,
                        to=ADMIN_NUMBER,
                    )
                except Exception:
                    logger.exception("Admin payment alert error")
            msg.body(
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ *पेमेंट कन्फर्म झाले!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"सेवा: *{svc_name}*\n"
                f"रक्कम: *₹{amount}*\n\n"
                f"📄 तुमचे कागदपत्र *४ कामाच्या दिवसांत* तयार होतील. 🙏\n\n"
                f"🔄 नवीन अर्जासाठी *hi* टाइप करा."
            )
        else:
            # Resend payment instructions
            upi_link = (
                f"upi://pay?pa={UPI_ID}"
                f"&pn={UPI_NAME.replace(' ', '%20')}"
                f"&am={amount}"
                f"&cu=INR"
                f"&tn={svc_name.replace(' ', '%20')}"
            )
            msg.body(
                f"💳 *पेमेंट प्रतीक्षेत*\n"
                f"─────────────────────\n"
                f"सेवा: *{svc_name}*\n"
                f"रक्कम: *₹{amount}*\n"
                f"UPI ID: *{UPI_ID}*\n\n"
                f"👇 PhonePe / GPay ने पेमेंट करा:\n"
                f"{upi_link}\n\n"
                f"✅ पेमेंट झाल्यावर *PAID* टाइप करा. 🙏"
            )

    # ══════════════════════════════
    # STEP: complete
    # ══════════════════════════════
    elif step == "complete":
        msg.body(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ *अर्ज पूर्ण झाला!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📄 तुमचे कागदपत्र *४ कामाच्या दिवसांत*\n"
            "तयार होतील. 🙏\n\n"
            "🔄 नवीन अर्जासाठी *hi* टाइप करा."
        )

    save_session(user, session)
    return str(resp)


# ─────────────────────────────────────────────
# PhonePe Payment Webhook
# ─────────────────────────────────────────────
@app.route("/phonepe_webhook", methods=["POST"])
def phonepe_webhook():
    data = request.get_json(force=True, silent=True)

    if not data or "response" not in data:
        logger.warning("phonepe_webhook: invalid payload")
        return "Invalid payload", 400

    encoded  = data["response"]
    x_verify = request.headers.get("X-VERIFY") or request.headers.get("x-verify", "")

    if not all([encoded, x_verify, PHONEPE_SALT_KEY]):
        logger.warning("phonepe_webhook: missing data or config")
        return "Missing config", 400

    # Verify signature (support both signing variants)
    calc_pay     = _phonepe_x_verify_pay(encoded)
    calc_webhook = _phonepe_x_verify_webhook(encoded)

    if x_verify not in (calc_pay, calc_webhook):
        logger.error(
            "Signature mismatch. received=%s  pay=%s  webhook=%s",
            x_verify, calc_pay, calc_webhook,
        )
        return "Signature mismatch", 400

    try:
        payload = json.loads(base64.b64decode(encoded).decode())
    except Exception:
        logger.exception("phonepe_webhook: decode error")
        return "Decode error", 400

    logger.info("PhonePe webhook payload: %s", payload)

    if payload.get("success") and payload.get("code") == "PAYMENT_SUCCESS":
        txn_data   = payload.get("data", {})
        txn_id     = txn_data.get("merchantTransactionId")
        payment_id = txn_data.get("providerReferenceId", "")

        if not redis_client or not txn_id:
            logger.warning("phonepe_webhook: redis or txn_id missing")
            return "Error", 500

        user_bytes = redis_client.get(f"txn:{txn_id}")
        if not user_bytes:
            logger.warning("phonepe_webhook: no user found for txn %s", txn_id)
            return "OK", 200   # Not our txn or already processed

        user    = user_bytes.decode("utf-8")
        session = get_session(user)

        if not session:
            logger.warning("phonepe_webhook: session expired for %s", user)
            return "OK", 200

        # ── Update session & sheet ──
        session.update({
            "payment_status": "Completed",
            "step":           "complete",
        })
        save_session(user, session)
        sheet_update_payment(user, session, payment_id)

        # ── Notify user ──
        svc_name = SERVICES.get(session.get("selected_service", ""), {}).get("name", "")
        user_msg = (
            f"🎉 *पेमेंट यशस्वी झाले!*\n\n"
            f"सेवा: *{svc_name}*\n"
            f"Payment ID: `{payment_id}`\n\n"
            f"✅ तुमचा अर्ज नोंदवला गेला आहे.\n"
            f"तुमचे कागदपत्र *४ कामाच्या दिवसांत* तयार होतील.\n\n"
            f"धन्यवाद! 🙏 *अक्षय मल्टी सर्व्हिसेस*"
        )
        send_whatsapp(user, user_msg)

        # ── Notify admin ──
        clean_number = user.replace("whatsapp:", "")
        admin_msg = (
            f"🔔 *नवीन यशस्वी अर्ज!*\n\n"
            f"📱 ग्राहक नंबर: *{clean_number}*\n"
            f"👤 नाव: {session.get('user_name', 'N/A')}\n"
            f"📄 सेवा: *{svc_name}*\n"
            f"💳 Payment ID: {payment_id}\n"
            f"🕐 वेळ: {now_ist_str()}"
        )
        if ADMIN_NUMBER:
            send_whatsapp(ADMIN_NUMBER, admin_msg)

        # ── Cleanup ──
        end_session(user)
        redis_client.delete(f"txn:{txn_id}")
        logger.info("Payment completed for user %s, txn %s", user, txn_id)

    else:
        # Payment failed or pending
        txn_id = payload.get("data", {}).get("merchantTransactionId")
        code   = payload.get("code", "UNKNOWN")
        logger.warning("PhonePe non-success: code=%s txn=%s", code, txn_id)

        if txn_id and redis_client:
            user_bytes = redis_client.get(f"txn:{txn_id}")
            if user_bytes:
                user = user_bytes.decode("utf-8")
                svc_name = ""
                s = get_session(user)
                if s:
                    svc_name = SERVICES.get(s.get("selected_service", ""), {}).get("name", "")
                fail_msg = (
                    f"⚠️ *पेमेंट अयशस्वी* (Code: {code})\n\n"
                    f"सेवा: *{svc_name}*\n\n"
                    "कृपया पुन्हा प्रयत्न करा किंवा आमच्याशी संपर्क साधा."
                )
                send_whatsapp(user, fail_msg)

    return "OK", 200


# ─────────────────────────────────────────────
# Payment Status Redirect (after PhonePe redirect)
# ─────────────────────────────────────────────
@app.route("/payment-status", methods=["GET", "POST"])
def payment_status():
    """Simple landing page shown to user after PhonePe redirect."""
    return (
        "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
        "<h2>✅ पेमेंट झाले!</h2>"
        "<p>WhatsApp वर तुम्हाला कन्फर्मेशन मेसेज मिळेल.</p>"
        "<p>धन्यवाद! – <strong>अक्षय मल्टी सर्व्हिसेस</strong></p>"
        "</body></html>",
        200,
    )


# ─────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return {
        "status":    "ok",
        "redis":     bool(redis_client),
        "sheets":    bool(sheets_client),
        "twilio":    bool(twilio_client),
        "phonepe":   bool(PHONEPE_MERCHANT_ID),
        "timestamp": now_ist_str(),
    }, 200


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # Startup warnings
    if not twilio_client:
        logger.warning("⚠️  Twilio not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing).")
    if not PHONEPE_MERCHANT_ID:
        logger.warning("⚠️  PhonePe not configured (PHONEPE_MERCHANT_ID missing).")
    if not sheets_client:
        logger.warning("⚠️  Google Sheets not configured (GOOGLE_APPLICATION_CREDENTIALS missing/invalid).")
    if not redis_client:
        logger.warning("⚠️  Redis not configured (REDIS_URL missing). Sessions will NOT work.")
    if not ADMIN_NUMBER:
        logger.warning("⚠️  ADMIN_NUMBER not set – admin alerts disabled.")

    # Background scheduler – daily 9 PM IST
    scheduler = BackgroundScheduler(timezone=IST)
    scheduler.add_job(send_daily_sheet_link, "cron", hour=21, minute=0)
    scheduler.start()
    logger.info("✅ Scheduler started — daily report at 9:00 PM IST.")

    try:
        port = int(os.getenv("PORT", 5000))
        logger.info("🚀 Starting Flask on port %d …", port)
        app.run(host="0.0.0.0", port=port, debug=False)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler …")
        scheduler.shutdown()
