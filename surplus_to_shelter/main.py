"""AnnData advanced FastAPI platform.
Run: python -m uvicorn main:app --reload
Optional real SMS/email providers are configured through environment variables.
"""
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
import hashlib, os, random, secrets, smtplib, ssl, json, socket, io, base64, re
from email.message import EmailMessage
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen, HTTPError
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from matching import Donation, Driver, Recipient, assign_driver, impact, match_donation, score_recipient, haversine_km, travel_minutes

app=FastAPI(title="AnnData — Food, Nourish, Support")
app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")
COMPANY_USERNAME="company"
COMPANY_PASSWORD="company123"
FOOD_CATEGORIES=["rice","wheat","arhar dal","rajma","vegetable"]

def normalize_food_needs(value=None):
    out={c:0.0 for c in FOOD_CATEGORIES}
    if isinstance(value,dict):
        for c in FOOD_CATEGORIES:
            try: out[c]=max(0.0,float(value.get(c,0)))
            except Exception: out[c]=0.0
    return out

def public_base_url(request:Request):
    configured=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured: return configured
    host=request.headers.get("host", "127.0.0.1:8000")
    if host.split(":")[0] not in {"127.0.0.1","localhost","0.0.0.0"}:
        return f"http://{host}"
    port=host.split(":",1)[1] if ":" in host else "8000"
    try:
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); sock.connect(("8.8.8.8",80)); ip=sock.getsockname()[0]; sock.close()
        return f"http://{ip}:{port}"
    except Exception:
        return f"http://{host}"

def seed():
    # No demo recipients/drivers: every participant must register first.
    return [], [], []

recipients,drivers,donations=seed(); photos={}; users={}; notifications=[]
REG_FILE=Path(__file__).parent / "registrations.json"

def load_users():
    if not REG_FILE.exists(): return {}
    try:
        data=json.loads(REG_FILE.read_text(encoding="utf-8"))
        return {str(k):v for k,v in data.items()} if isinstance(data,dict) else {}
    except Exception:
        return {}

def save_users():
    safe={k:v for k,v in users.items()}
    REG_FILE.write_text(json.dumps(safe,indent=2),encoding="utf-8")

users=load_users()
driver_locations={}
# Rebuild registered driver/recipient profiles after a server restart.
# This helper is intentionally compatible with both the basic and advanced
# versions of matching.py (some versions have phone/email fields in the
# dataclasses and some do not).
def _make_driver_from_user(u):
    d = Driver(
        int(u.get("profile_id", 0)),
        u.get("name", "Driver"),
        float(u.get("lat", 26.9124)),
        float(u.get("lng", 75.7873)),
        True,
    )
    # Add optional fields when the imported Driver model supports them;
    # plain dataclasses also allow these attributes dynamically.
    d.phone = u.get("phone")
    d.email = u.get("email")
    return d

def _make_recipient_from_user(u):
    r = Recipient(
        int(u.get("profile_id", 0)),
        u.get("name", "Receiving Organization"),
        float(u.get("lat", 26.9124)),
        float(u.get("lng", 75.7873)),
        float(u.get("capacity_kg", 0)),
        set(u.get("accepts_list", ["any"])),
    )
    r.phone = u.get("phone")
    r.email = u.get("email")
    r.people_served = int(u.get("people_served", 0))
    r.food_needs = normalize_food_needs(u.get("food_needs"))
    return r

for _u in users.values():
    if _u.get("role") == "driver":
        drivers.append(_make_driver_from_user(_u))
    elif _u.get("role") == "recipient":
        recipients.append(_make_recipient_from_user(_u))
# Emergency requests are created only by registered receiving organizations/company users.
emergency_requests=[]
donation_emergency={}
pickup_tokens={}
pickup_codes={}
def normalize_pickup_code(value):
    clean="".join(ch for ch in str(value or "") if ch.isdigit())
    return clean if len(clean)==6 else ""
def otp(): return str(random.randint(100000,999999))
def pw(v): return hashlib.sha256(v.encode()).hexdigest()

def notify(kind, to, subject, message):
    """Send real notifications. Email prefers Resend API, then Gmail/SMTP fallback; SMS uses Twilio."""
    sent=False; detail="demo mode"
    if kind=="email" and to and os.getenv("RESEND_API_KEY"):
        try:
            payload=json.dumps({
                "from": os.getenv("EMAIL_FROM", "onboarding@resend.dev"),
                "to": [to],
                "subject": subject,
                "html": f"""<div style='margin:0;background:#fff8ec;padding:28px;font-family:Arial,sans-serif;color:#173b24'>
<div style='max-width:620px;margin:auto;background:#ffffff;border-radius:22px;padding:30px;box-shadow:0 8px 28px rgba(23,59,36,.10)'>
<div style='font-size:28px;font-weight:800;color:#1f6b3a'>Ann<span style='color:#ef6c2f'>Data</span></div>
<div style='margin-top:5px;color:#6d766f;font-size:13px;font-weight:700;letter-spacing:.4px'>FOOD • NOURISH • SUPPORT</div>
<div style='height:3px;background:linear-gradient(90deg,#1f6b3a,#ef6c2f);margin:18px 0 24px;border-radius:99px'></div>
{message.replace(chr(10), '<br>')}
<div style='margin-top:26px;padding:14px 16px;background:#f1f8f1;border-radius:14px;color:#245c35;font-size:13px'><b>Thank you for turning surplus into support. 💚</b><br>Every safe meal shared can make a meaningful difference.</div>
</div></div>""",
            }).encode()
            req=URLRequest("https://api.resend.com/emails",data=payload,headers={"Authorization":f"Bearer {os.getenv('RESEND_API_KEY')}","Content-Type":"application/json","Accept":"application/json","User-Agent":"Surplus-to-Shelter/1.0"},method="POST")
            with urlopen(req,timeout=15) as resp:
                result=json.loads(resp.read().decode())
            sent=True; detail=f"email sent via Resend: {result.get('id','accepted')}"
        except HTTPError as e:
            # Resend returns the useful reason in the JSON response body. The
            # old code only showed "HTTP Error 403", which hid the real cause.
            try:
                raw = e.read().decode("utf-8", errors="replace")
                try:
                    err = json.loads(raw)
                    msg = err.get("message") or err.get("error") or raw
                    code = err.get("name") or err.get("code")
                    detail = f"Resend HTTP {e.code}: {code + ' - ' if code else ''}{msg}"
                except json.JSONDecodeError:
                    detail = f"Resend HTTP {e.code}: {raw or e.reason}"
            except Exception:
                detail = f"Resend HTTP {e.code}: {e.reason}"
        except Exception as e:
            detail=f"Resend email failed: {e}"

    # If Resend is still in its testing-only mode (or otherwise rejects the
    # recipient), fall back to Gmail SMTP. This allows the app to send to
    # normal user addresses once a Gmail App Password is configured.
    if kind=="email" and to and not sent and os.getenv("GMAIL_USER") and os.getenv("GMAIL_APP_PASSWORD"):
        try:
            gmail_user=os.getenv("GMAIL_USER", "").strip()
            gmail_password=os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
            msg=EmailMessage()
            msg["Subject"]=subject
            msg["From"]=os.getenv("GMAIL_FROM", gmail_user)
            msg["To"]=to
            msg.set_content(message)
            html_message=f"""<div style='font-family:Arial,sans-serif;background:#fff8ec;padding:28px;color:#173b24'><div style='max-width:620px;margin:auto;background:#fff;border-radius:22px;padding:30px'><div style='font-size:28px;font-weight:800;color:#1f6b3a'>Ann<span style='color:#ef6c2f'>Data</span></div><div style='font-size:13px;font-weight:700;color:#6d766f;margin-top:4px'>FOOD • NOURISH • SUPPORT</div><hr style='border:0;border-top:3px solid #1f6b3a;margin:18px 0'>{message.replace(chr(10), '<br>')}<div style='margin-top:24px;padding:14px;background:#f1f8f1;border-radius:14px'><b>Thank you for turning surplus into support. 💚</b></div></div></div>"""
            msg.add_alternative(html_message, subtype="html")
            with smtplib.SMTP("smtp.gmail.com",587,timeout=15) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                s.login(gmail_user,gmail_password)
                s.send_message(msg)
            sent=True
            detail="email sent via Gmail SMTP"
        except Exception as e:
            detail=f"Gmail SMTP failed: {e}"

    elif kind=="email" and to and os.getenv("SMTP_HOST"):
        try:
            msg=EmailMessage(); msg["Subject"]=subject; msg["From"]=os.getenv("SMTP_FROM",os.getenv("SMTP_USER","")); msg["To"]=to; msg.set_content(message)
            with smtplib.SMTP(os.getenv("SMTP_HOST"),int(os.getenv("SMTP_PORT","587")),timeout=10) as s:
                s.starttls(context=ssl.create_default_context()); s.login(os.getenv("SMTP_USER",""),os.getenv("SMTP_PASSWORD","")); s.send_message(msg)
            sent=True; detail="email sent"
        except Exception as e: detail=f"email failed: {e}"
    elif kind=="sms" and to and os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN") and os.getenv("TWILIO_FROM"):
        try:
            url=f"https://api.twilio.com/2010-04-01/Accounts/{os.getenv('TWILIO_ACCOUNT_SID')}/Messages.json"
            data=urlencode({"To":to,"From":os.getenv("TWILIO_FROM"),"Body":message}).encode()
            import base64
            auth=base64.b64encode(f"{os.getenv('TWILIO_ACCOUNT_SID')}:{os.getenv('TWILIO_AUTH_TOKEN')}".encode()).decode()
            req=URLRequest(url,data=data,headers={"Authorization":f"Basic {auth}"}); urlopen(req,timeout=10).read(); sent=True; detail="sms sent"
        except Exception as e: detail=f"sms failed: {e}"
    notifications.append({"time":datetime.now().isoformat(),"kind":kind,"to":to,"subject":subject,"message":message,"sent":sent,"detail":detail})
    return {"sent":sent,"detail":detail}

def find(items,item_id,label):
    for x in items:
        if x.id==item_id:return x
    raise HTTPException(404,f"{label} {item_id} not found")

def donation_view(d):
    out=asdict(d); out["expires_at"]=d.expires_at.isoformat(); out["photo"]=photos.get(d.id)
    r=next((x for x in recipients if x.id==d.recipient_id),None); drv=next((x for x in drivers if x.id==d.driver_id),None)
    out["recipient"]=r.name if r else None; out["driver"]=drv.name if drv else None
    if r: out["recipient_phone"]=r.phone
    if drv: out["driver_phone"]=drv.phone
    out["freshness_checked"]=bool(getattr(d,"freshness_checked",False))
    out["freshness_checked_at"]=getattr(d,"freshness_checked_at",None)
    out["minutes_left"]=max(0,round((d.expires_at-datetime.now()).total_seconds()/60)); return out

def recipient_view(r):
    o=asdict(r); o["accepts"]=sorted(r.accepts); o["open_until"]=r.open_until.isoformat() if r.open_until else None; o["food_needs"]=normalize_food_needs(getattr(r,"food_needs",None)); return o

class DonationIn(BaseModel):
    donor_name:str; description:str; quantity_kg:float=Field(gt=0); lat:float; lng:float; expires_in_minutes:int=Field(default=180,ge=15); photo:str|None=None; category:str="any"; donor_phone:str|None=None; donor_email:str|None=None; emergency_request_id:int|None=None; recipient_id:int|None=None
class AuthIn(BaseModel): name:str; email:str; password:str; role:str="donor"; phone:str|None=None
class RegistrationIn(BaseModel):
    name:str
    email:str
    password:str
    role:str
    phone:str|None=None
    lat:float|None=None
    lng:float|None=None
    capacity_kg:float|None=None
    accepts:str="any"
    food_needs:dict[str,float]|None=None
class LoginIn(BaseModel): email:str; password:str
class OTPIn(BaseModel): otp:str
class ProfileIn(BaseModel): phone:str|None=None; email:str|None=None; name:str|None=None
class CapacityIn(BaseModel): capacity_kg:float=Field(ge=0)
class LocationIn(BaseModel): lat:float; lng:float
class TrackingOut(BaseModel): driver_id:int; lat:float; lng:float; updated_at:str
class FoodCheckIn(BaseModel):
    description:str
    category:str="any"
    expires_in_minutes:int=Field(default=180,ge=15)
class EmergencyIn(BaseModel):
    shelter_id:int
    food_type:str="any"
    quantity_kg:float=Field(gt=0)
    people:int=Field(default=10,ge=1)
    urgency:str="high"
    note:str=""
    requester_email:str
class QRVerifyIn(BaseModel):
    token:str
class PickupCodeIn(BaseModel):
    code:str

@app.get("/")
def home(): return FileResponse(Path(__file__).parent/"index.html")

@app.get("/donor-register")
def donor_register_page(): return FileResponse(Path(__file__).parent/"donor_register.html")

@app.get("/donate_qr.jpeg")
def donate_qr(): return FileResponse(Path(__file__).parent/"donate_qr.jpeg", media_type="image/jpeg")

@app.get("/site_shared.css")
def shared_css(): return FileResponse(Path(__file__).parent/"site_shared.css", media_type="text/css")


# Four separate role websites
@app.get("/donor")
def donor_site(): return FileResponse(Path(__file__).parent/"donor.html")

@app.get("/driver")
def driver_site(): return FileResponse(Path(__file__).parent/"driver.html")

@app.get("/recipient")
def recipient_site(): return FileResponse(Path(__file__).parent/"recipient.html")

@app.get("/company")
def company_site(): return FileResponse(Path(__file__).parent/"company.html")

@app.post("/auth/signup")
def signup(b:RegistrationIn):
    global recipients, drivers
    email=b.email.strip().lower()
    allowed={"donor","driver","recipient"}
    if b.role not in allowed: raise HTTPException(400,"Invalid registration type")
    if email in users: raise HTTPException(409,"Email already registered")
    if len(b.password)<6: raise HTTPException(400,"Password must be at least 6 characters")
    uid=max([u.get("id",0) for u in users.values()] or [0])+1
    user={"id":uid,"name":b.name.strip(),"email":email,"password":pw(b.password),"role":b.role,"phone":b.phone,"verified":True,"registered_at":datetime.now().isoformat()}
    if b.role=="driver":
        did=max([x.id for x in drivers] or [0])+1
        driver_user={"profile_id":did,"name":b.name.strip(),"lat":b.lat if b.lat is not None else 26.9124,"lng":b.lng if b.lng is not None else 75.7873,"phone":b.phone,"email":email}
        drivers.append(_make_driver_from_user(driver_user))
        user["profile_id"]=did; user["lat"]=driver_user["lat"]; user["lng"]=driver_user["lng"]
    elif b.role=="recipient":
        if b.lat is None or b.lng is None or not b.capacity_kg or b.capacity_kg<=0:
            raise HTTPException(400,"Receiving organization needs latitude, longitude and capacity")
        rid=max([x.id for x in recipients] or [0])+1
        accepts_set={x.strip().lower() for x in b.accepts.split(",") if x.strip()} or {"any"}
        recipient_user={"profile_id":rid,"name":b.name.strip(),"lat":b.lat,"lng":b.lng,"capacity_kg":b.capacity_kg,"accepts_list":sorted(accepts_set),"phone":b.phone,"email":email,"people_served":0,"food_needs":normalize_food_needs(b.food_needs)}
        recipients.append(_make_recipient_from_user(recipient_user))
        user["profile_id"]=rid; user["lat"]=b.lat; user["lng"]=b.lng; user["capacity_kg"]=b.capacity_kg; user["accepts_list"]=sorted(accepts_set); user["food_needs"]=normalize_food_needs(b.food_needs)
    users[email]=user; save_users()
    return {k:v for k,v in user.items() if k!="password"}

@app.post("/company/login")
def company_login(b:LoginIn):
    if b.email.strip() != COMPANY_USERNAME or b.password != COMPANY_PASSWORD:
        raise HTTPException(401,"Invalid company login")
    return {"name":"Company Command Center","email":COMPANY_USERNAME,"role":"company"}

@app.get("/company/summary")
def company_summary():
    vals=list(users.values())
    return {
        "registered": {
            "total": len(vals),
            "donors": [u for u in vals if u.get("role")=="donor"],
            "drivers": [u for u in vals if u.get("role")=="driver"],
            "recipients": [u for u in vals if u.get("role")=="recipient"],
        },
        "donations": [donation_view(d) for d in donations],
        "drivers_live": [asdict(d) for d in drivers],
        "recipients_live": [recipient_view(r) for r in recipients],
        "emergency_requests": emergency_requests,
    }

@app.post("/auth/login")
def login(b:LoginIn):
    u=users.get(b.email.strip().lower())
    if not u or u["password"]!=pw(b.password): raise HTTPException(401,"Invalid email or password")
    return {k:v for k,v in u.items() if k!="password"}

@app.get("/registrations")
def registration_summary():
    vals=list(users.values())
    return {
        "total":len(vals),
        "donors":sum(u.get("role")=="donor" for u in vals),
        "drivers":sum(u.get("role")=="driver" for u in vals),
        "donating_companies":sum(u.get("role")=="company_donor" for u in vals),
        "receiving_organizations":sum(u.get("role")=="recipient" for u in vals),
        "profiles":[{k:u.get(k) for k in ("id","profile_id","name","email","phone","role","registered_at")} for u in vals]
    }

def get_registered_user(email: str):
    clean=(email or "").strip().lower()
    u=users.get(clean)
    if not u:
        raise HTTPException(401,"Please login with a registered email and password.")
    return u

@app.get("/dashboard/summary")
def dashboard_summary(email:str):
    u=get_registered_user(email)
    role=u.get("role")
    mine=[]
    if role=="donor":
        mine=[donation_view(d) for d in donations if (d.donor_email or "").lower()==u["email"]]
    elif role=="driver":
        pid=int(u.get("profile_id",0))
        mine=[donation_view(d) for d in donations if d.driver_id==pid]
    elif role=="recipient":
        pid=int(u.get("profile_id",0))
        mine=[donation_view(d) for d in donations if d.recipient_id==pid]
    else:
        raise HTTPException(403,"This account does not have a dashboard role.")

    recipient_data=None
    if role=="recipient":
        r=find(recipients,int(u.get("profile_id",0)),"Recipient")
        recipient_data=recipient_view(r)
        recipient_data["received_kg"]=round(sum(d.quantity_kg for d in donations if d.recipient_id==r.id and d.status in {"picked_up","delivered"}),1)
        recipient_data["delivered_kg"]=round(sum(d.quantity_kg for d in donations if d.recipient_id==r.id and d.status=="delivered"),1)
        recipient_data["active_kg"]=round(sum(d.quantity_kg for d in donations if d.recipient_id==r.id and d.status in {"matched","picked_up"}),1)
        recipient_data["emergency_requests"]=[e for e in emergency_requests if e.get("shelter_id")==r.id]

    driver_data=None
    if role=="driver":
        drv=find(drivers,int(u.get("profile_id",0)),"Driver")
        loc=driver_locations.get(drv.id,{"lat":drv.lat,"lng":drv.lng,"updated_at":None})
        driver_data={**asdict(drv),"location_updated_at":loc.get("updated_at")}

    impact_data=impact(donations)
    statuses={}
    for d in donations: statuses[d.status]=statuses.get(d.status,0)+1
    return {
        "user":{"id":u.get("id"),"name":u.get("name"),"email":u.get("email"),"role":role,"profile_id":u.get("profile_id")},
        "role":role,
        "donations":mine,
        "recipient":recipient_data,
        "driver":driver_data,
        "impact":impact_data,
        "statuses":statuses,
        "all_donations_count":len(donations),
        "registered_recipients":len(recipients),
        "registered_drivers":len(drivers),
        "emergency_requests":emergency_requests,
    }

@app.get("/dashboard/recipient/{recipient_id}")
def company_recipient_view(recipient_id:int):
    r=find(recipients,recipient_id,"Recipient")
    rows=[donation_view(d) for d in donations if d.recipient_id==recipient_id]
    emergency=[e for e in emergency_requests if e.get("shelter_id")==recipient_id]
    return {
        "recipient":recipient_view(r),
        "donations":rows,
        "emergency_requests":emergency,
        "received_kg":round(sum(d.quantity_kg for d in donations if d.recipient_id==recipient_id and d.status in {"picked_up","delivered"}),1),
        "delivered_kg":round(sum(d.quantity_kg for d in donations if d.recipient_id==recipient_id and d.status=="delivered"),1),
        "active_kg":round(sum(d.quantity_kg for d in donations if d.recipient_id==recipient_id and d.status in {"matched","picked_up"}),1),
    }

@app.get("/notifications/config")
def notification_config():
    return {
        "resend_configured": bool(os.getenv("RESEND_API_KEY")),
        "email_from": os.getenv("EMAIL_FROM", "onboarding@resend.dev"),
        "smtp_configured": bool(os.getenv("SMTP_HOST")),
        "gmail_configured": bool(os.getenv("GMAIL_USER") and os.getenv("GMAIL_APP_PASSWORD")),
    }

@app.post("/notifications/test")
def test_notification(kind:str="email",to:str=""):
    return notify(kind,to,"Surplus-to-Shelter notification","This is a test notification from your Surplus-to-Shelter demo.")
@app.get("/notifications")
def get_notifications(): return notifications[-50:][::-1]

def _send_donor_email_async(email, donor_name, quantity_kg, donation_id, status, recipient_name=None):
    """Send a branded, useful donor confirmation after the donation is matched."""
    if not email:
        return
    recipient_line = (f"Your food has been matched with <b>{recipient_name}</b>." if recipient_name
                      else "We are currently finding the right recipient for your donation.")
    plain_recipient = (f"Your food has been matched with {recipient_name}." if recipient_name
                       else "We are currently finding the right recipient for your donation.")
    subject = f"💚 AnnData — Your food donation #{donation_id} is on its way"
    message = (
        f"<h2 style='margin:0 0 10px;color:#173b24'>Thank you, {donor_name}! 💚</h2>"
        f"<p style='font-size:16px'>Your <b>{quantity_kg:g} kg</b> food donation has been successfully posted on AnnData.</p>"
        f"<p>{recipient_line}</p>"
        f"<p style='font-size:14px;color:#657067'>Donation ID: <b>#{donation_id}</b><br>Status: <b>{status.replace('_',' ').title()}</b></p>"
        f"<p>We will keep you updated as the pickup and delivery progress.</p>"
    )
    plain = (
        f"Thank you, {donor_name}!\n\n"
        f"Your {quantity_kg:g} kg food donation has been successfully posted on AnnData.\n"
        f"{plain_recipient}\n\n"
        f"Donation ID: #{donation_id}\nStatus: {status.replace('_',' ').title()}\n\n"
        "We will keep you updated as the pickup and delivery progress.\n\n"
        "Thank you for turning surplus into support. 💚"
    )
    # notify() currently accepts a plain message, while its email renderer also supports HTML.
    # Use a small wrapper so Gmail/Resend both receive the branded content.
    try:
        result = notify("email", email, subject, plain)
        if not result.get("sent"):
            print(f"[AnnData email] donor #{donation_id}: {result.get('detail','email failed')}")
    except Exception as exc:
        print(f"[AnnData email] donor #{donation_id} failed: {exc}")

@app.post("/donations")
def create_donation(b:DonationIn, background_tasks:BackgroundTasks):
    donor_email=(b.donor_email or "").strip().lower()
    donor_user=users.get(donor_email) if donor_email else None
    if not donor_user or donor_user.get("role") != "donor":
        raise HTTPException(401,"Please register as a Donor or Food Donating Company before posting food")
    b.donor_name=donor_user.get("name") or b.donor_name
    if not b.donor_email:
        raise HTTPException(400,"Please enter donor email so the confirmation mail can be sent immediately.")

    if b.emergency_request_id is not None:
        er=next((x for x in emergency_requests if x["id"]==b.emergency_request_id and x["status"]=="open"),None)
        if not er: raise HTTPException(400,"That emergency food request is no longer open.")
        if b.quantity_kg < float(er["quantity_kg"]):
            raise HTTPException(400,f"This emergency request needs at least {er['quantity_kg']} kg.")

    selected=None
    if b.recipient_id is not None:
        selected=next((x for x in recipients if x.id==b.recipient_id),None)
        if not selected: raise HTTPException(404,"Selected recipient not found")
        category=(b.category or "").strip().lower()
        needs=normalize_food_needs(getattr(selected,"food_needs",None))
        if category not in FOOD_CATEGORIES: raise HTTPException(400,"Please select one of the supported food categories.")
        if needs.get(category,0)<b.quantity_kg: raise HTTPException(400,f"{selected.name} currently needs only {needs.get(category,0):g} kg of {category}.")
        if selected.capacity_kg<b.quantity_kg: raise HTTPException(400,f"{selected.name} does not have enough receiving capacity for {b.quantity_kg} kg.")

    now=datetime.now(); d=Donation(len(donations)+1,b.donor_name,b.description,b.quantity_kg,b.lat,b.lng,now+timedelta(minutes=b.expires_in_minutes),category=b.category,donor_phone=b.donor_phone,donor_email=b.donor_email,meals=int(b.quantity_kg*2),otp_pickup=otp(),otp_delivery=otp())
    donations.append(d)
    if b.emergency_request_id is not None:
        donation_emergency[d.id]=b.emergency_request_id
    if b.photo and len(b.photo)<600000: photos[d.id]=b.photo

    if selected is not None:
        score=score_recipient(d,selected,now)
        if score is None: raise HTTPException(400,"The selected recipient cannot safely receive this donation before expiry.")
        result=(selected,score)
    else:
        result=match_donation(d,recipients,now)
    if result is None: d.status="unmatched"
    else:
        r,score=result; d.recipient_id=r.id; d.match_score=score; r.capacity_kg-=d.quantity_kg
        if selected is not None:
            needs=normalize_food_needs(getattr(r,"food_needs",None)); needs[d.category]=round(max(0,needs[d.category]-d.quantity_kg),2); r.food_needs=needs
            for u in users.values():
                if u.get("role")=="recipient" and int(u.get("profile_id",0))==r.id: u["food_needs"]=needs; u["accepts_list"]=[c for c,v in needs.items() if v>0]
            save_users()
        # Driver selection is manual: every registered driver can see the matched pickup.
        d.status="matched"
        if d.id in donation_emergency:
            er=next((x for x in emergency_requests if x["id"]==donation_emergency[d.id]),None)
            if er:
                er["status"]="fulfilled"; er["fulfilled_by_donation"] = d.id; er["closed_at"] = datetime.now().isoformat()
    recipient_name = None
    if d.status=="matched":
        r=next(x for x in recipients if x.id==d.recipient_id)
        recipient_name=r.name
        msg=f"Donation #{d.id} matched to {r.name}. A registered driver can now choose the pickup. Pickup OTP will be generated when the driver accepts."
        notify("sms",d.donor_phone,"Donation matched",msg)

    # Send the donor email after matching so it contains useful status information.
    # BackgroundTasks keeps the donation response fast while still sending a real email.
    background_tasks.add_task(_send_donor_email_async, d.donor_email, d.donor_name, d.quantity_kg, d.id, d.status, recipient_name)
    response=donation_view(d)
    response["email_sent"]=bool(d.donor_email)
    response["email_detail"]="donor confirmation queued" if d.donor_email else "no donor email"
    return response

@app.get("/donations")
def list_donations(): return [donation_view(d) for d in donations]

@app.post("/donations/{donation_id}/claim")
def claim_donation(donation_id:int, driver_id:int):
    d=find(donations,donation_id,"Donation")
    drv=find(drivers,driver_id,"Driver")
    if d.status!="matched": raise HTTPException(400,"This donation is not available for pickup.")
    if d.driver_id and d.driver_id!=driver_id: raise HTTPException(409,"This pickup has already been chosen by another driver.")
    if not drv.available and d.driver_id!=driver_id: raise HTTPException(400,"Driver is currently on another delivery.")
    d.driver_id=driver_id; drv.available=False
    return donation_view(d)

@app.post("/donations/{donation_id}/pickup")
def mark_picked_up(donation_id:int,b:OTPIn|None=None):
    d=find(donations,donation_id,"Donation")
    if d.status!="matched": raise HTTPException(400,f"Cannot pick up a donation with status '{d.status}'")
    if b and b.otp and b.otp!=d.otp_pickup: raise HTTPException(400,"Invalid pickup OTP")
    d.status="picked_up"; notify("sms",d.donor_phone,f"Pickup confirmed #{d.id}","Your surplus food has been picked up successfully. Driver will now complete the freshness check before starting delivery."); return donation_view(d)
@app.post("/features/delivery-start/{donation_id}")
def start_delivery(donation_id:int, driver_id:int, freshness_confirmed:bool=False):
    d=find(donations,donation_id,"Donation")
    if d.status!="picked_up": raise HTTPException(400,"Donor approval is required before the freshness check.")
    if d.driver_id!=driver_id: raise HTTPException(403,"Only the assigned driver can start this delivery.")
    if not freshness_confirmed: raise HTTPException(400,"Please confirm the food passed the adulteration/freshness instant-kit check.")
    d.freshness_checked=True
    d.freshness_checked_at=datetime.now().isoformat()
    d.status="in_transit"
    return donation_view(d)

@app.post("/donations/{donation_id}/deliver")
def mark_delivered(donation_id:int,b:OTPIn|None=None):
    d=find(donations,donation_id,"Donation")
    if d.status!="in_transit": raise HTTPException(400,f"Cannot deliver a donation with status '{d.status}'")
    if b and b.otp and b.otp!=d.otp_delivery: raise HTTPException(400,"Invalid delivery OTP")
    d.status="delivered"; dvr=find(drivers,d.driver_id,"Driver") if d.driver_id else None
    if dvr: dvr.available=True; dvr.deliveries+=1
    notify("email",d.donor_email,f"Donation delivered #{d.id}","Your food donation has reached the recipient. Thank you!"); return donation_view(d)
@app.post("/donations/{donation_id}/cancel")
def cancel(donation_id:int):
    d=find(donations,donation_id,"Donation")
    if d.status in {"delivered","cancelled"}: raise HTTPException(400,"Donation cannot be cancelled")
    if d.recipient_id:
        r=find(recipients,d.recipient_id,"Recipient"); r.capacity_kg+=d.quantity_kg
    if d.driver_id: find(drivers,d.driver_id,"Driver").available=True
    d.status="cancelled"; return donation_view(d)

@app.get("/recipients")
def list_recipients(): return [recipient_view(r) for r in recipients]
@app.patch("/recipients/{recipient_id}/capacity")
def update_capacity(recipient_id:int,b:CapacityIn):
    r=find(recipients,recipient_id,"Recipient"); r.capacity_kg=b.capacity_kg; return recipient_view(r)
@app.get("/recipients/{recipient_id}/needs")
def get_recipient_needs(recipient_id:int):
    r=find(recipients,recipient_id,"Recipient")
    return {"recipient_id":r.id,"recipient":r.name,"needs":normalize_food_needs(getattr(r,"food_needs",None))}

class NeedsIn(BaseModel):
    needs:dict[str,float]

@app.patch("/recipients/{recipient_id}/needs")
def update_recipient_needs(recipient_id:int,b:NeedsIn):
    r=find(recipients,recipient_id,"Recipient")
    needs=normalize_food_needs(b.needs); r.food_needs=needs
    r.accepts={c for c,v in needs.items() if v>0} or {"any"}
    for u in users.values():
        if u.get("role")=="recipient" and int(u.get("profile_id",0))==recipient_id:
            u["food_needs"]=needs; u["accepts_list"]=sorted(r.accepts)
    save_users(); return {"recipient_id":r.id,"recipient":r.name,"needs":needs}

@app.get("/drivers")
def list_drivers(): return [asdict(x) for x in drivers]
@app.patch("/drivers/{driver_id}/location")
def driver_location(driver_id:int,b:LocationIn):
    d=find(drivers,driver_id,"Driver"); d.lat=b.lat; d.lng=b.lng
    driver_locations[driver_id]={"lat":b.lat,"lng":b.lng,"updated_at":datetime.now().isoformat()}
    return {**asdict(d),"location_updated_at":driver_locations[driver_id]["updated_at"]}

@app.get("/drivers/{driver_id}/location")
def get_driver_location(driver_id:int):
    d=find(drivers,driver_id,"Driver")
    loc=driver_locations.get(driver_id,{"lat":d.lat,"lng":d.lng,"updated_at":None})
    return {"driver_id":driver_id,"driver":d.name,"lat":loc["lat"],"lng":loc["lng"],"updated_at":loc["updated_at"],"available":d.available}

@app.get("/donations/{donation_id}/tracking")
def donation_tracking(donation_id:int):
    d=find(donations,donation_id,"Donation")
    r=next((x for x in recipients if x.id==d.recipient_id),None)

    # Tracking can be opened before a driver has been assigned. Return a
    # normal response instead of a 404 so the dashboard does not show an
    # API error for a perfectly valid donation state.
    if not d.driver_id:
        return {
            "donation_id":d.id, "status":d.status, "tracking_available":False,
            "driver_id":None, "driver":None, "driver_lat":None, "driver_lng":None,
            "location_updated_at":None, "donor_lat":d.lat, "donor_lng":d.lng,
            "recipient_lat":r.lat if r else None, "recipient_lng":r.lng if r else None,
            "message":"Driver has not been assigned yet."
        }

    drv=find(drivers,d.driver_id,"Driver")
    loc=driver_locations.get(drv.id,{"lat":drv.lat,"lng":drv.lng,"updated_at":None})
    return {
        "donation_id":d.id,"status":d.status,"tracking_available":True,
        "driver_id":drv.id,"driver":drv.name,"driver_lat":loc["lat"],"driver_lng":loc["lng"],
        "location_updated_at":loc["updated_at"],"donor_lat":d.lat,"donor_lng":d.lng,
        "recipient_lat":r.lat if r else None,"recipient_lng":r.lng if r else None
    }
@app.patch("/drivers/{driver_id}/availability")
def driver_availability(driver_id:int,available:bool=True):
    d=find(drivers,driver_id,"Driver"); d.available=available; return asdict(d)

@app.get("/impact")
def get_impact(): return impact(donations)
@app.get("/analytics")
def analytics():
    cats={}; statuses={}
    for d in donations: cats[d.category]=cats.get(d.category,0)+1; statuses[d.status]=statuses.get(d.status,0)+1
    return {"categories":cats,"statuses":statuses,"avg_match_score":round(sum(d.match_score for d in donations if d.match_score)/max(1,sum(bool(d.match_score) for d in donations)),1),"total":len(donations)}
@app.get("/donations/{donation_id}/route")
def route(donation_id:int):
    d=find(donations,donation_id,"Donation"); r=next((x for x in recipients if x.id==d.recipient_id),None); drv=next((x for x in drivers if x.id==d.driver_id),None)
    return {"donor":{"lat":d.lat,"lng":d.lng},"driver":{"lat":drv.lat,"lng":drv.lng} if drv else None,"recipient":{"lat":r.lat,"lng":r.lng} if r else None,"driver_to_donor_km":round(haversine_km(drv.lat,drv.lng,d.lat,d.lng),2) if drv else None,"donor_to_recipient_km":round(haversine_km(d.lat,d.lng,r.lat,r.lng),2) if r else None,"eta_minutes":round(travel_minutes(haversine_km(d.lat,d.lng,r.lat,r.lng))+15) if r else None}

@app.post("/features/ai-food-check")
def ai_food_check(b:FoodCheckIn):
    """Fast local food-photo/description assistant. It is a heuristic, not a food-safety certification."""
    text=(b.description or "").lower()
    rules={
        "cooked meal":["rice","dal","curry","meal","food","roti","chapati","biryani","sabzi","pasta","noodles"],
        "bakery":["bread","cake","bun","bakery","croissant","pastry","biscuit"],
        "produce":["fruit","vegetable","vegetables","apple","banana","tomato","potato","produce","salad"],
        "packaged goods":["packet","packaged","can","tin","sealed","snack","chips","cereal"],
        "beverages":["juice","milk","water","beverage","drink","tea","coffee"],
        "desserts":["dessert","sweet","gulab jamun","halwa","kheer","ice cream"],
    }
    scores={k:sum(1 for word in words if word in text) for k,words in rules.items()}
    suggested=max(scores,key=scores.get) if max(scores.values(),default=0)>0 else (b.category if b.category!="any" else "cooked meal")
    confidence=92 if scores.get(suggested,0)>=2 else (78 if scores.get(suggested,0)==1 else 65)
    warnings=[]
    if b.expires_in_minutes<=60: warnings.append("Very short expiry window — prioritize immediate pickup.")
    if suggested in {"cooked meal","beverages"}: warnings.append("Keep temperature control and hygiene checks during pickup.")
    warnings.append("Visual/description analysis is only a suggestion; donor must confirm food is safe to donate.")
    return {"suggested_category":suggested,"confidence":confidence,"quality":"Review required","checks":["Packaging/cleanliness","Expiry time","Temperature where applicable","Allergen information"],"warnings":warnings}

@app.get("/features/leaderboard")
def leaderboard():
    totals={}
    delivered={}
    for d in donations:
        if d.status=="delivered":
            totals[d.donor_name]=totals.get(d.donor_name,0)+d.quantity_kg
            delivered[d.donor_name]=delivered.get(d.donor_name,0)+1
    rows=sorted(totals.items(),key=lambda x:x[1],reverse=True)[:10]
    return [{"rank":i+1,"name":name,"kg":round(kg,1),"deliveries":delivered.get(name,0),"badge":"Community Champion" if kg>=100 else ("Food Hero" if kg>=25 else "First Rescuer")} for i,(name,kg) in enumerate(rows)]

@app.get("/features/carbon")
def carbon_impact():
    delivered_kg=sum(d.quantity_kg for d in donations if d.status=="delivered")
    return {"food_kg":round(delivered_kg,1),"co2e_kg":round(delivered_kg*2.5,1),"meals":int(delivered_kg*2),"method":"Estimated using 2.5 kg CO2e per kg food diverted; illustrative project metric."}

@app.get("/features/expiry-alerts")
def expiry_alerts():
    now=datetime.now(); alerts=[]
    for d in donations:
        if d.status in {"delivered","cancelled"}: continue
        mins=max(0,round((d.expires_at-now).total_seconds()/60))
        if mins<=60:
            alerts.append({"donation_id":d.id,"food":d.description,"minutes_left":mins,"quantity_kg":d.quantity_kg,"status":d.status,"severity":"critical" if mins<=20 else "high"})
    return sorted(alerts,key=lambda x:x["minutes_left"])

@app.get("/features/demand")
def demand_prediction():
    demand={r.id:{"shelter":r.name,"area":r.name.split("(")[-1].rstrip(")"),"current_capacity_kg":round(r.capacity_kg,1),"needs":{}} for r in recipients}
    for e in emergency_requests:
        if e["shelter_id"] in demand:
            demand[e["shelter_id"]]["needs"][e["food_type"]]=demand[e["shelter_id"]]["needs"].get(e["food_type"],0)+e["quantity_kg"]
    historical={}
    for d in donations:
        historical[d.category]=historical.get(d.category,0)+d.quantity_kg
    common=sorted(historical.items(),key=lambda x:x[1],reverse=True)
    for row in demand.values():
        if not row["needs"]:
            row["needs"]={cat:round(max(5,kg*0.35),1) for cat,kg in common[:3]} if common else {"cooked meal":20,"produce":10}
    return {"forecast":list(demand.values()),"note":"Forecast uses current capacity, emergency requests and recent donation mix; it is a demo planning estimate."}

@app.post("/features/emergency")
def create_emergency(b:EmergencyIn):
    requester=users.get(b.requester_email.strip().lower())
    if not requester or requester.get("role")!="recipient":
        raise HTTPException(401,"Only a registered receiving organization can post an emergency food request")
    r=find(recipients,b.shelter_id,"Recipient")
    item={"id":len(emergency_requests)+1,"shelter_id":r.id,"shelter":r.name,"food_type":b.food_type,"quantity_kg":b.quantity_kg,"people":b.people,"urgency":b.urgency,"note":b.note,"created_at":datetime.now().isoformat(),"status":"open"}
    emergency_requests.append(item)
    notify("email",r.email,"Emergency food request created",f"Emergency request: {b.quantity_kg} kg of {b.food_type} for about {b.people} people. Urgency: {b.urgency}.") if r.email else None
    return item

@app.get("/features/emergency")
def list_emergency():
    return sorted(emergency_requests,key=lambda x:(x["status"]!="open",x["urgency"]!="critical",x["created_at"]))

@app.post("/features/emergency/{request_id}/close")
def close_emergency(request_id:int):
    item=next((x for x in emergency_requests if x["id"]==request_id),None)
    if not item: raise HTTPException(404,"Emergency request not found")
    item["status"]="fulfilled"; item["closed_at"]=datetime.now().isoformat(); return item

@app.get("/features/route/{driver_id}")
def optimized_route(driver_id:int):
    drv=find(drivers,driver_id,"Driver")
    active=[d for d in donations if d.status in {"matched","picked_up"} and d.driver_id==driver_id]
    remaining=active[:]; cur=(drv.lat,drv.lng); stops=[]; total=0.0
    while remaining:
        nxt=min(remaining,key=lambda d:haversine_km(cur[0],cur[1],d.lat,d.lng))
        km=haversine_km(cur[0],cur[1],nxt.lat,nxt.lng); total+=km
        stops.append({"donation_id":nxt.id,"food":nxt.description,"donor":nxt.donor_name,"lat":nxt.lat,"lng":nxt.lng,"km_from_previous":round(km,2)})
        cur=(nxt.lat,nxt.lng); remaining.remove(nxt)
    return {"driver_id":driver_id,"driver":drv.name,"stops":stops,"total_km":round(total,2),"eta_minutes":round(travel_minutes(total),1)}

@app.get("/features/qr/{donation_id}")
def pickup_qr(donation_id:int, request:Request):
    d=find(donations,donation_id,"Donation")
    if d.status!="matched":
        raise HTTPException(400,"QR pickup approval is available only after a driver is assigned.")
    token=pickup_tokens.get(d.id)
    code=pickup_codes.get(d.id)
    if not token:
        token=secrets.token_urlsafe(24)
        pickup_tokens[d.id]=token
    if not code:
        # Generate a unique 6-digit code among all currently active pickups.
        active_codes={str(v) for k,v in pickup_codes.items() if any(x.id==k and x.status=="matched" for x in donations)}
        for _ in range(100):
            candidate=f"{random.randint(0,999999):06d}"
            if candidate not in active_codes:
                code=candidate
                break
        else:
            raise HTTPException(503,"Could not generate a unique pickup code. Please try again.")
        pickup_codes[d.id]=code
    return {"donation_id":d.id,"token":token,"code":code,"purpose":"donor_approval","status":d.status,"driver":donation_view(d).get("driver"),"scan_url":f"{public_base_url(request)}/donor?pickup={d.id}&token={token}","message":"Show this QR to the donor. The donor scans it on their phone and approves pickup. If camera scanning fails, enter the 6-digit pickup code."}

@app.get("/features/pickup-approval/{donation_id}")
def pickup_approval_info(donation_id:int, token:str="", code:str=""):
    d=find(donations,donation_id,"Donation")
    expected=pickup_tokens.get(d.id); expected_code=pickup_codes.get(d.id)
    valid_token=bool(expected and token and secrets.compare_digest(token.strip(),expected))
    valid_code=bool(expected_code and code and secrets.compare_digest(code.strip(),expected_code))
    if not (valid_token or valid_code):
        raise HTTPException(400,"This pickup QR/code is invalid or expired.")
    if d.status!="matched":
        raise HTTPException(400,f"Pickup approval is not available for status '{d.status}'.")
    v=donation_view(d)
    return {"donation_id":d.id,"donor":d.donor_name,"food":d.description,"quantity_kg":d.quantity_kg,"driver":v.get("driver"),"recipient":v.get("recipient"),"expires_at":v.get("expires_at"),"status":d.status}

@app.get("/features/pickup-code/lookup")
def pickup_code_lookup(code:str):
    clean=normalize_pickup_code(code)
    if len(clean)!=6:
        raise HTTPException(400,"Pickup code must be exactly 6 digits.")
    # One source of truth: the same in-memory code map used by QR generation
    # and final pickup approval. This prevents a code from being displayed by
    # the Driver card but rejected by the Donor card.
    for d in donations:
        if d.status=="matched" and normalize_pickup_code(pickup_codes.get(d.id))==clean:
            v=donation_view(d)
            return {"donation_id":d.id,"donor":d.donor_name,"food":d.description,"quantity_kg":d.quantity_kg,"driver":v.get("driver"),"recipient":v.get("recipient"),"expires_at":v.get("expires_at"),"status":d.status,"code":clean}
    raise HTTPException(404,"Pickup code not found or already used. Copy the current code from the Driver card.")

def _complete_pickup(d):
    d.status="picked_up"
    pickup_tokens.pop(d.id,None)
    pickup_codes.pop(d.id,None)
    notify("email",d.donor_email,f"Pickup approved #{d.id}",f"You approved pickup of {d.quantity_kg} kg of {d.description}. The parcel is now marked as picked up.") if d.donor_email else None
    return {"verified":True,"status":d.status,"message":"Pickup confirmed. The parcel has been automatically marked as picked up."}

@app.post("/features/pickup-approval/{donation_id}")
def approve_pickup(donation_id:int,b:QRVerifyIn):
    d=find(donations,donation_id,"Donation")
    expected=pickup_tokens.get(d.id); expected_code=pickup_codes.get(d.id)
    supplied=b.token.strip()
    if not ((expected and secrets.compare_digest(supplied,expected)) or (expected_code and secrets.compare_digest(supplied,expected_code))):
        raise HTTPException(400,"This pickup QR/code is invalid or expired.")
    if d.status!="matched":
        raise HTTPException(400,f"Pickup cannot be approved from status '{d.status}'.")
    return _complete_pickup(d)

@app.post("/features/pickup-code/approve")
def approve_pickup_code(b:PickupCodeIn):
    clean=normalize_pickup_code(b.code)
    if len(clean)!=6:
        raise HTTPException(400,"Pickup code must be exactly 6 digits.")
    for d in donations:
        if d.status=="matched" and normalize_pickup_code(pickup_codes.get(d.id))==clean:
            return _complete_pickup(d)
    raise HTTPException(400,"This pickup code is invalid or expired. Copy the current code from the Driver card.")

# Backward-compatible endpoint for the older demo button.
@app.post("/features/qr/{donation_id}/verify")
def verify_qr(donation_id:int,b:QRVerifyIn):
    return approve_pickup(donation_id,b)

@app.post("/reset")
def reset():
    global recipients,drivers,donations,driver_locations,pickup_tokens,pickup_codes,donation_emergency,users
    recipients,drivers,donations=seed(); photos.clear(); notifications.clear(); driver_locations.clear(); emergency_requests.clear(); pickup_tokens.clear(); pickup_codes.clear(); donation_emergency.clear(); users={}
    save_users()
    return {"status":"hard reset complete; all registered users and operational data erased"}
