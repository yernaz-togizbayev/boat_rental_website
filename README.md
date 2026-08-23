# ⛵ Boat Rental Web App – Information Management & Systems Engineering Project

This is a full-stack web application built with **Flask**, **SQLAlchemy**, **MariaDB** and **Docker**,
created for the **Information Management and Systems Engineering (IMSE)** course. Clients can search
a harbour for a date range, book a boat and pay for the charter, while managers run the fleet, the
offices, the staff and the bookings behind it.

---

## 📚 Course Context

**Course**: 052400-1 VU Information Management and Systems Engineering, University of Vienna  
**Group**: 05  

**Main Focus**:
- Conceptual modeling with an ER diagram in Chen notation
- Relational design: IS-A hierarchies, a weak entity, unary and m:n relationships
- Hand-written SQL for schema, seed data and analytics
- A use-case driven web implementation over that schema
- A NoSQL (document) redesign of the same data

---

## 🚀 Features

- 🔎 **Search & Booking**:
  - Pick a harbour and a date range; the whole fleet is listed, with taken and out-of-service boats greyed out and unselectable
  - Server-side validation re-derives what is bookable, so a crafted request cannot slip through

- 💳 **Payment**:
  - Simulated card checkout with a live countdown on short-notice bookings
  - Unpaid bookings are holds and expire on a real clock

- 📋 **Client Logbook**:
  - Every charter with its harbour, dates, total and payment state
  - Cancel a future booking; a paid one is kept on record as `CANCELLED`

- 🛥 **Fleet Management** *(manager)*:
  - Boats, offices, employees, and the yacht / motorboat / catamaran subtypes
  - Supervision and maintenance assignments (the two m:n relations)
  - Cancel any client's charter, including one already under way

- 📊 **Availability Report**:
  - What is free in a harbour over a date range, with fleet-wide figures alongside
  - Type-ahead harbour filter

- 🌱 **Demo Data**:
  - One button refills boats, clients, employees and rentals — and keeps any harbours you added

---

## 🛠 Tech Stack

- Python 3 · Flask · Jinja2
- SQLAlchemy (hand-maintained models, no migrations)
- MariaDB 11.3
- Flask-WTF / WTForms with CSRF protection
- Bootstrap 5 + a custom admiralty-chart stylesheet
- Docker Compose

---

## 📦 Getting Started

### 1️⃣ Requirements

Docker is the only prerequisite — Python, Flask and MariaDB all run inside the containers.

### 2️⃣ Run it

```bash
docker compose up --build
```

Then open <http://localhost:5000>. MariaDB is exposed on `3306`.

### 3️⃣ Sign in

There is no sign-up wall: `/login` lists every client and one click signs you in, and
`/manager/login` picks a manager from a dropdown. Authentication is passwordless **by design** —
see [Notes](#-notes).

### 4️⃣ Fill the database

A fresh database is seeded by the SQL in `database/`. If the app looks empty, or you want a bigger
fleet to click around, use the **Demo data** panel at the top of any page. It works without signing
in, can be run repeatedly, and keeps any harbours you have added.

### 🔁 Useful commands

```bash
docker compose down -v          # -v is required to re-run the DB init scripts
docker compose logs -f backend
```

`./backend/` is bind-mounted, so Python edits reload and template edits show on the next request.
**Schema changes need `down -v`,** which destroys the volume and everything in it.

### ⚙️ Configuration

Everything has a working default; a `.env` file in the repository root can override it.

| Variable | Default | Purpose |
|----------|---------|---------|
| `TZ` | `Europe/Vienna` | Both containers. Payment deadlines are naive `DATETIME`s, so the app, MariaDB and the clock on the wall have to agree. |
| `UNSPLASH_ACCESS_KEY` | *(unset)* | Optional. Fetches a photo for a harbour with no hand-picked image. Without it there is a Wikipedia lookup and then a generic pool. |
| `IMAGE_FETCH` | `on` | Set to `off` to skip all outbound image lookups and run fully offline. |
| `SECRET_KEY` | `dev` | Flask session signing. |

`.env` is gitignored and must stay that way.

---

## 🧪 Tests

`backend/smoke_test.py` drives the real app end to end over the flows that have actually broken
before — booking validation, double booking, expiring payment holds, cancellation, employee role
changes, and the foreign-key cleanup around deletes. It runs against a throwaway SQLite database,
so it needs no Docker:

```bash
cd backend
pip install -r requirements.txt
python smoke_test.py
```

It does **not** cover the SQL seed scripts; those need MariaDB.

---

## 🗂️ Project Structure

| Path | Description |
|------|-------------|
| `backend/boat_rental/` | The Flask app: `models.py`, `routes.py`, `forms.py`, `generator.py`, `images.py`, `assignments.py` |
| `backend/templates/` | Jinja templates, all extending `base.html` |
| `backend/static/main.css` | Custom stylesheet on top of Bootstrap |
| `backend/smoke_test.py` | End-to-end smoke test (SQLite, no Docker needed) |
| `database/` | Graded SQL: shared `CREATE TABLE`s plus per-student insert and query scripts |
| `docs/` | Graded deliverables: ER diagram, NoSQL designs, SQL screenshots |
| `Group05_MS1.pdf` | Milestone 1 report |

```text
boat_rental_webapp/
├── backend/
│   ├── boat_rental/
│   ├── templates/
│   ├── static/
│   ├── requirements.txt
│   └── smoke_test.py
├── database/
│   ├── Group05_Createtable.sql
│   ├── init.sql
│   ├── Student1/
│   └── Student2/
├── docs/
│   ├── ER-diagram/
│   ├── json/
│   └── SQLexecution_screenshots/
├── docker-compose.yml
└── README.md
```

Two details about `database/` that are easy to trip over:

- MariaDB's entrypoint runs top-level files alphabetically and **does not recurse**, so `init.sql`
  exists only to `SOURCE` the per-student scripts in `Student1/` and `Student2/`.
- Student 1 owns the shared `Office` rows; Student 2 must not re-insert them. A single missing
  semicolon kills the rest of the chain, and the only symptom is an empty app.

---

## 🎓 Deliverables

| Requirement | Where |
|-------------|-------|
| ER diagram (Chen notation) | `docs/ER-diagram/` — BEE-UP source (`.adl`) and export (`.jpg`) |
| Relational schema | `database/Group05_Createtable.sql` |
| Use-case & analytics SQL | `database/Student1/`, `database/Student2/` |
| SQL execution screenshots | `docs/SQLexecution_screenshots/` |
| NoSQL designs | `docs/json/NoSQLDesign_IG.json`, `docs/json/NoSQLDesign_TY.json` |
| Milestone 1 report | `Group05_MS1.pdf` |

---

## 💡 Notes

These are deliberate decisions, not loose ends:

- **No passwords.** `/login` is a client picker. Real authentication would not change anything the
  coursework is assessed on.
- **Payment is a local simulation.** No payment provider is contacted and no network call is made.
  `4242 4242 4242 4242` is accepted and `4000 0000 0000 0002` is declined, following Stripe's
  published test numbers as a recognisable convention. **Card numbers are validated and then
  discarded** — never stored, never put in the session, never logged.
- **No scheduler.** Unpaid holds are swept when availability is read rather than by a background
  job, because a search is the moment the answer has to be honest.
- **No migrations.** `models.py` is a hand-maintained mirror of `Group05_Createtable.sql`; a column
  added to one must be added to the other.
- **Both published ports listen on `127.0.0.1` only**, so the app and the database are reachable
  from the machine running them and nowhere else. This is a development server with the reloader
  on, and the database has a known root password; neither belongs on a shared network. To reach it
  from another device, publish `5000:5000` for that session.
- **Cancelling a paid charter keeps the row** as `CANCELLED` rather than deleting it, so the record
  that money changed hands survives.

---

## 📄 License

Released under the [MIT License](LICENSE). This repository contains coursework and is shared for
educational purposes.
