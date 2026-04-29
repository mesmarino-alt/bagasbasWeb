WHAT TO BUILD NEXT (PRIORITY ORDER)
1. QR + RECEIPT SYSTEM (CRITICAL)

Right now, your system cannot be used at the entrance.

You need:

QR code per booking
Downloadable receipt (PDF)
Scan/verify endpoint

👉 This turns your system into an actual operational tool

2. BOOKING VERIFICATION PAGE

Used by staff at entrance:

Scan QR
Show booking details
Mark as USED
3. EMAIL CONFIRMATION

When admin approves:

Send receipt
Send booking details
4. CAPACITY VISIBILITY (ADMIN)

Right now admin is blind.

Add:

“Remaining slots per day”
Visual indicator (e.g. 70/100)
5. BOOKING EXPIRATION

Handle no-shows:

Auto-expire past dates
Free capacity again



Refactor the existing “Upcoming Events” section into a **dynamic, admin-controlled event management system** integrated with the backend and database.

---

## 🎯 Objective

Replace static event content with a **database-driven system** where:

* Events are created and managed by admin
* Landing page automatically displays upcoming events
* System supports future expansion (registration, ticketing, etc.)

---

## 🧱 Database Design (Supabase)

Create a table: `events`

```sql
create table events (
    id uuid primary key,
    title text not null,
    description text,
    event_date date,
    location text,
    image_url text,
    tags text[],
    status text default 'active',
    created_at timestamp default now()
);
```

---

## 🔌 Backend (Flask)

### 1. Get Events API (Public)

```python
@app.route('/api/events')
def get_events():
    result = supabase.table("events") \
        .select("*") \
        .eq("status", "active") \
        .order("event_date") \
        .execute()

    return jsonify(result.data)
```

---

### 2. Admin Create Event

```python
@app.route('/admin/events/create', methods=['POST'])
def create_event():
    data = request.form

    supabase.table("events").insert({
        "id": str(uuid.uuid4()),
        "title": data["title"],
        "description": data["description"],
        "event_date": data["event_date"],
        "location": data["location"],
        "image_url": data["image_url"],  # or uploaded file path
        "tags": data.getlist("tags")
    }).execute()

    return redirect('/admin/events')
```

---

### 3. Admin Delete / Update

Add endpoints:

* `/admin/events/delete/<id>`
* `/admin/events/update/<id>`

---

## 🎨 Frontend Refactor (Landing Page)

### Replace hardcoded events with dynamic rendering:

```html
<div id="events-container" class="events-container"></div>
```

---

### JavaScript:

```javascript
fetch('/api/events')
.then(res => res.json())
.then(events => {
    const container = document.getElementById('events-container');
    container.innerHTML = '';

    events.forEach(e => {
        container.innerHTML += `
        <article class="event-item">
            <div class="card event-card">
                <img src="${e.image_url}" class="event-media">
                <div class="event-content">
                    <div class="event-badges">
                        ${(e.tags || []).map(tag => `<span class="event-badge">${tag}</span>`).join('')}
                    </div>
                    <h5>${e.title}</h5>
                    <p class="event-description">${e.description}</p>
                    <div class="event-meta">
                        <span>${e.event_date}</span>
                        <span>${e.location}</span>
                    </div>
                </div>
            </div>
        </article>
        `;
    });
});
```

---

## 🧑‍💼 Admin UI (New Section)

Add to admin dashboard:

### “Events Management”

Features:

* List all events (table or cards)
* Create new event (form)
* Edit event
* Delete event

---

### Event Form Fields

* Title
* Description
* Date
* Location
* Image upload
* Tags (e.g. “Live”, “Outdoor”)

---

## 🎨 UX Enhancements

* Show only **upcoming events** (event_date >= today)
* Highlight nearest event as “Featured”
* Add “View All Events” page (not modal)
* Lazy load images

---

## 🔐 Security

* Only admin can access `/admin/events`
* Validate all inputs server-side
* Sanitize text fields

---

## 🚀 Future Extensions

* Event registration system
* Ticketing / payments
* RSVP tracking
* Event capacity limits

---

## 🧠 Design Principle

```text
Content (Events) must be controlled by admin, not hardcoded in UI
```

```text
Landing page = dynamic display layer
Admin panel = content control layer
```

---

## ✅ Final Deliverable

* Dynamic events section (no hardcoded HTML)
* Admin-controlled CRUD system
* Clean UI rendering with tags, images, and metadata
* Scalable structure for future event features






Use the script already in your project to create an admin account.

In Supabase SQL Editor, run admins_table.sql once.
Make sure .env has these values:
SUPABASE_URL
SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY
Run this command in your project folder:

/home/edz-dev/bagasbasWeb/.venv/bin/python create_admin_user.py \
  --email bagasbasadmin@gmail.com \
  --password admin@bagasbas \
  --role admin


You should see: Created admin user: ... (admin)
Log in at /admin/login with that email/password.
If you want a staff account instead, use:

/home/edz-dev/bagasbasWeb/.venv/bin/python create_admin_user.py \
  --email staff@bagasbas.com \
  --password staff@bagasbas \
  --role staff

Script location: create_admin_user.py.



Here’s how to implement the **Marketing Website + Admin CMS + Inquiry Option** in a clean, production-ready way using your current Flask setup.

---

# 🎯 WHAT THIS SYSTEM BECOMES

Instead of a booking engine, your system becomes:

### 1. Public Website (Marketing)

* Show cottages, events, gallery
* Strong visuals + call-to-action
* “Reserve Now” → inquiry, not booking

### 2. Admin CMS

* Owner controls all content
* No hardcoded data anymore

### 3. Inquiry System

* Users send requests
* Admin handles manually (email/phone)

---

# 🧱 SYSTEM ARCHITECTURE

## Frontend Pages

```
/                → Landing Page
/events          → All events
/gallery         → Photos
/inquire         → Inquiry form
```

## Admin Pages

```
/admin
/admin/events
/admin/cottages
/admin/gallery
/admin/inquiries
```

---

# 1️⃣ LANDING PAGE CHANGES

## Replace Booking CTA

### ❌ Before:

```html
Book Now
```

### ✅ After:

```html
Inquire Now
Reserve via Message
```

---

## CTA Options (choose one or combine):

### Option A — Inquiry Page

```html
<a href="/inquire" class="btn-primary">Inquire Now</a>
```

### Option B — Direct Messenger

```html
<a href="https://m.me/yourpage" target="_blank">
  Chat on Facebook
</a>
```

### Option C — Call

```html
<a href="tel:+639XXXXXXXXX">Call Now</a>
```

---

# 2️⃣ INQUIRY SYSTEM (CORE FEATURE)

## 🧾 Form Fields

```html
Name
Email
Phone
Preferred Date
Guests
Message
```

---

## 🧠 Backend Route (Flask)

```python
@app.route('/api/inquiry', methods=['POST'])
def create_inquiry():
    data = request.json

    # Save to DB
    inquiry = {
        "id": str(uuid4()),
        "name": data["name"],
        "email": data["email"],
        "phone": data["phone"],
        "date": data["date"],
        "guests": data["guests"],
        "message": data["message"],
        "status": "new",
        "created_at": datetime.utcnow()
    }

    db.inquiries.insert_one(inquiry)

    # Send email (optional)
    send_email_to_admin(inquiry)

    return {"success": True}
```

---

## 📬 Admin View

Table:

* Name
* Date
* Guests
* Message
* Status (new / contacted / closed)

---

# 3️⃣ ADMIN CMS MODULES

## 🧩 EVENTS (You already started this)

### Admin can:

* Create event
* Upload image
* Set date/location
* Publish/unpublish

### DB Schema:

```json
{
  "id": "",
  "title": "",
  "description": "",
  "date": "",
  "location": "",
  "image_url": "",
  "status": "published"
}
```

---

## 🏠 COTTAGES

### Admin controls:

* Name
* Capacity
* Price (optional display only)
* Description
* Image

👉 Used only for display (not booking logic)

---

## 🖼️ GALLERY

### Admin uploads:

* Photos
* Categories (optional)

---

## 📢 ANNOUNCEMENTS (Optional but powerful)

Examples:

* “Fully booked this weekend”
* “New cottages available”

---

# 4️⃣ REMOVE OR DISABLE

You should remove:

* Booking wizard
* QR system
* Payment logic
* Booking APIs

Or:
👉 Keep them hidden for future reuse

---

# 5️⃣ UX FLOW (FINAL)

## User Journey:

1. User visits website
2. Sees cottages / events
3. Clicks **“Inquire Now”**
4. Fills form OR clicks Messenger
5. Admin receives inquiry
6. Admin responds manually

---

# 6️⃣ WHY THIS IS BETTER (FOR CLIENT)

### ✔ Less complexity

No need to manage:

* Payments
* Scheduling conflicts
* QR validation

### ✔ More control

Owner decides everything manually

### ✔ Matches real-world behavior

Most resorts still:

* Use FB / phone
* Confirm manually

---

# 7️⃣ OPTIONAL UPGRADE (HIGH VALUE)

## Auto Email Reply to User

After inquiry:

```
“Thanks for contacting Bagasbas Resort.
We’ll get back to you shortly.”
```

---

## Inquiry Status Pipeline

Admin updates:

```
New → Contacted → Confirmed → Closed
```

---

# 8️⃣ UI IMPROVEMENTS YOU SHOULD DO

### Landing Page:

* Add “Inquire Now” sticky button
* Add contact section
* Add testimonials

### Admin:

* Add notification badge:

  * “3 new inquiries”

---

# 🚀 FINAL POSITIONING

You didn’t downgrade your system.

You turned it into:

> **A controllable, scalable resort website with lead management**

---

# ⚡ If you want next step

I can give you:

* Full **Inquiry Form UI (modern)**
* Admin **Inquiry dashboard UI**
* Flask + JS integration (ready-to-use)

Just say:
👉 “build inquiry system UI + backend”
    