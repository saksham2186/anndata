# AnnData — Surplus to Shelter

**AnnData** is a food-rescue platform that connects food donors, delivery drivers, and recipient organizations to reduce food waste and help distribute surplus food to people in need.

The platform provides registration, smart food matching, delivery tracking, emergency food requests, pickup verification, notifications, and impact analytics in one system.

## 🚀 Features

- **Donor Management**
  - Register and log in as a donor
  - Post surplus food with quantity, category, location, and expiry time
  - Track active and completed donations
  - Pickup approval using verification codes

- **Recipient Management**
  - Register as a recipient organization
  - Set food requirements and available capacity
  - View incoming and received donations
  - Create emergency food requests

- **Driver Management**
  - Driver registration and login
  - View available food donations
  - Accept delivery assignments
  - Update delivery status
  - Live location tracking

- **Smart Matching**
  - Automatically matches donations with suitable recipients
  - Considers distance, food category, recipient capacity, delivery time, and urgency

- **Emergency Food Requests**
  - Recipients can request food urgently
  - Donors can view open emergency requirements

- **Food Safety & Expiry**
  - Food expiry tracking
  - Expiry alerts
  - AI-based food quality check interface

- **Pickup & Delivery Verification**
  - QR-based verification
  - Pickup codes
  - Delivery status tracking

- **Notifications**
  - Email notifications
  - SMS support through Twilio
  - Resend/Gmail SMTP integration

- **Impact Analytics**
  - Food rescued
  - Meals supported
  - Food diverted from waste
  - Estimated CO₂ emissions avoided
  - Active and completed donations

- **Additional Features**
  - Donor leaderboard
  - Demand prediction
  - Route optimization
  - Live operations dashboard
  - Multi-language support

## 🛠️ Tech Stack

### Backend
- Python
- FastAPI
- Pydantic

### Frontend
- HTML5
- CSS3
- JavaScript

### Integrations
- Resend / SMTP for email
- Twilio for SMS
- Google Translate for language support

### Data Storage
- JSON-based local storage

## 📁 Project Structure

```text
surplus_to_shelter/
│
├── main.py                  # FastAPI backend and API routes
├── matching.py              # Smart matching and impact calculation
├── registrations.json       # Registered user data
├── site_shared.css          # Shared styling
│
├── index.html               # Main AnnData website
├── donor.html               # Donor dashboard
├── donor_register.html      # Donor registration
├── driver.html              # Driver dashboard
├── recipient.html           # Recipient dashboard
├── company.html             # Company command center
│
├── assets/
│   ├── anndata_logo.jpg
│   ├── child_food.jpg
│   ├── children_sharing.jpg
│   └── food_hands.jpg
│
└── donate_qr.jpeg           # Donation QR image
```

## ⚙️ Installation

### 1. Clone the repository

```bash
git clone https://github.com/saksham2186/anndata.git
cd anndata/surplus_to_shelter
```

### 2. Install dependencies

```bash
pip install fastapi uvicorn pydantic python-dotenv
```

### 3. Start the server

```bash
python -m uvicorn main:app --reload
```

The application will normally be available at:

```text
http://127.0.0.1:8000
```

## 👥 User Roles

| Role | Main Responsibilities |
|---|---|
| Donor | Post and manage surplus food |
| Driver | Pick up and deliver donations |
| Recipient | Request and receive food |
| Company | Monitor and manage operations |

## 🧠 Smart Matching

The matching engine calculates a score for potential recipients using factors such as:

```text
Distance
+ Capacity Fit
+ Food Category Match
+ Urgency
+ Delivery Time
```

The system uses the **Haversine formula** to calculate geographical distance between locations.

## 📊 Impact Calculation

AnnData calculates environmental and social impact from completed deliveries.

The current implementation estimates:

```text
2 meals per kg of food rescued
2.5 kg CO₂e avoided per kg of food rescued
```

These values are used to display the platform's estimated impact.

## 🔐 Notifications

The backend supports multiple notification methods:

- Resend API
- Gmail SMTP
- Generic SMTP
- Twilio SMS

These services require environment variables/API credentials to be configured.

Example:

```env
RESEND_API_KEY=your_key
GMAIL_USER=your_email
GMAIL_APP_PASSWORD=your_app_password
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_FROM=your_number
```

**Do not commit real API keys, passwords, or credentials to GitHub.**

## 🔄 Basic Workflow

```text
Donor
  │
  │ Posts surplus food
  ▼
AnnData Platform
  │
  │ Smart Matching
  ▼
Recipient Organization
  │
  │ Delivery Assignment
  ▼
Driver
  │
  │ Pickup & Delivery
  ▼
Recipient
  │
  ▼
Food Delivered
```

## 🎯 Purpose

AnnData aims to create a digital connection between **food surplus and food demand**.

Instead of allowing usable food to become waste, the platform helps coordinate its collection, matching, transportation, and delivery to organizations that need it.

## 🔮 Future Improvements

- Production-grade database such as PostgreSQL
- Secure authentication with JWT/OAuth
- Cloud deployment
- Real-time WebSocket tracking
- Advanced food-quality computer vision
- Improved demand forecasting
- Automated route optimization
- Mobile application for donors and drivers
- Stronger security and role-based access control

## 👨‍💻 Author

**Saksham Agarwal**

GitHub: https://github.com/saksham2186

## 📄 License

This project is intended for educational and development purposes. Add an appropriate open-source license before distributing the project publicly.
