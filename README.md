# अक्षय मल्टी सर्व्हिसेस – WhatsApp Chatbot

WhatsApp-based document service bot with PhonePe payment integration.

---

## Flow

```
User sends any message
       ↓
  Welcome + Menu (5 services)
       ↓
  User picks 1–5
       ↓
  Bot lists required documents
       ↓
  User uploads docs one by one (photo/PDF)
       ↓
  All docs received → PhonePe payment link sent
       ↓
  User pays → PhonePe webhook fires
       ↓
  User gets confirmation + "4 working days" message
  Admin gets alert: client number + service chosen
  Google Sheet row updated: Payment Status = Completed ✅
```

---

## Google Sheets Structure

A new sheet is created every month: `AMS-Applications-YYYY-MM`

| Column              | Description                         |
|---------------------|-------------------------------------|
| Timestamp           | When docs were submitted            |
| Phone               | Customer's phone number             |
| Customer Name       | WhatsApp profile name               |
| Service             | Chosen service name (Marathi)       |
| Service Fee (₹)     | Amount charged                      |
| Documents Uploaded  | Comma-separated doc names           |
| Total Docs Required | Count of docs needed                |
| Docs Status         | e.g. 5/5                            |
| Payment Status      | Pending → Completed ✅              |
| Transaction ID      | PhonePe merchant transaction ID     |
| PhonePe Payment ID  | Provider reference ID               |
| Payment Time        | When payment was confirmed          |
| Session Start       | When user started the session       |

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your real credentials
```

### 3. Google Service Account
- Go to Google Cloud Console → Create service account
- Download JSON key → set path in `GOOGLE_APPLICATION_CREDENTIALS`
- Share the Google Drive folder with the service account email

### 4. Twilio WhatsApp
- Get a Twilio account and enable WhatsApp sandbox (or approved number)
- Set webhook URL: `https://your-domain.com/whatsapp`

### 5. PhonePe
- Register on PhonePe Business dashboard
- Get Merchant ID, Salt Key, Salt Index
- Set callback URL: `https://your-domain.com/phonepe_webhook`

### 6. Redis
- Install Redis locally (`redis-server`) or use Redis Cloud
- Set `REDIS_URL` in `.env`

### 7. Run
```bash
python app.py
# or production:
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

---

## Endpoints

| Route              | Method | Purpose                                  |
|--------------------|--------|------------------------------------------|
| `/whatsapp`        | POST   | Twilio webhook for incoming messages     |
| `/phonepe_webhook` | POST   | PhonePe payment status callback          |
| `/payment-status`  | GET    | Redirect page after PhonePe payment      |
| `/health`          | GET    | Health check (JSON)                      |

---

## Admin Features

- **Payment alert**: When a client completes payment, admin receives:
  - Customer phone number
  - Service chosen
  - Payment ID & timestamp

- **Daily 9 PM IST report**: Admin receives Google Sheet link with:
  - Today's application count
  - Total month count

---

## Services & Fees

| # | Service                  | Fee   | Docs |
|---|--------------------------|-------|------|
| 1 | डोमासाईल                | ₹200  | 5    |
| 2 | Nationality Certificate  | ₹200  | 5    |
| 3 | उत्पन्न दाखला           | ₹200  | 3    |
| 4 | नॉन क्रीमीलेअर दाखला   | ₹300  | 6    |
| 5 | मराठा जातीचा दाखला     | ₹500  | 3    |