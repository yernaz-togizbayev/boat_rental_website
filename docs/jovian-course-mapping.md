# Jovian — Web Development with Python and Flask

Course project submission. The capstone asks for three things: an original website idea, built
with HTML and CSS, using a database to show dynamic pages.

**The project:** a boat rental service. Clients search a harbour over a date range, book a boat
and pay for the charter; managers run the fleet, the offices, the staff and the bookings behind
it. It was originally built for a university database course, which is why the data model is
heavier than a typical course project — ten entities, two inheritance hierarchies, a weak entity
and two many-to-many relationships.

Live site: *(add your Render URL here)* · Source: this repository

---

## Lesson mapping

| Lesson | Requirement | Where it is |
|---|---|---|
| **1** | Building web pages with HTML & CSS | 22 Jinja templates plus 3 shared partials in `backend/templates/`, all extending `base.html`. Bootstrap 5 with a 1,089-line custom stylesheet at `backend/static/main.css`. |
| **2** | Flask templates and cloud deployment | 38 routes in `backend/boat_rental/routes.py`. Template inheritance throughout — one `base.html` carries the navbar, which branches on whether a client or a manager is signed in. Deployment: `render.yaml` + `docs/DEPLOYMENT.md`. |
| **3** | Setting up & connecting a database | SQLAlchemy models in `backend/boat_rental/models.py` (10 classes over 12 tables). Schema in `database/Group05_Createtable.sql`. Local MariaDB via Docker Compose; managed MySQL in the cloud, built by `flask init-db`. |
| **4** | Dynamic database-driven web pages | `/booking` (availability over a date range), `/report` (the client's logbook), `/analytics` (per-city figures), and the whole `/manager/*` CRUD surface. |
| **5** | Using HTML forms to capture data | 12 WTForms classes in `backend/boat_rental/forms.py`, CSRF protection on every POST, server-side revalidation of booking submissions, and a simulated card checkout. |

## Beyond the syllabus

- **Inheritance in the data model.** `Boat` → `Yacht`/`Motorboat`/`Catamaran` and `Employee` →
  `Staff`/`Manager`, as subclass tables keyed off the superclass primary key. Changing an
  employee's role moves their row between tables.
- **A second database.** The same data is redesigned as MongoDB documents and migrated into it,
  with both analytics reports running against Mongo alongside their SQL originals, plus an index
  benchmark using `explain`. See `backend/boat_rental/nosql.py`.
- **A test suite.** `backend/smoke_test.py` runs 266 assertions end to end against a throwaway
  SQLite database — no Docker needed.
- **Booking correctness.** Availability is re-derived server-side on submit, so a crafted POST
  cannot book a boat that is taken, under maintenance, in another city, or has no price. Unpaid
  bookings are holds that expire on a real clock.

## Running it

Locally, Docker is the only prerequisite:

```bash
docker compose up --build     # app on :5000, MariaDB on :3306, MongoDB on :27017
```

Deployment is in `docs/DEPLOYMENT.md`. The one thing that does not carry over from Compose is
schema creation: MariaDB's Docker entrypoint builds the database on first boot, and a managed
cloud database has no entrypoint, so `flask init-db` runs the same SQL against `DATABASE_URL`.

## Screenshots

In `docs/slides/`: the home page, booking results, checkout, the client logbook, the availability
report, hiring staff, supervision assignments, and the two NoSQL pages.
