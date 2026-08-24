# Deploying to Render

The app normally runs under Docker Compose, where MariaDB's entrypoint builds the database on
first boot. A managed cloud database has no entrypoint, so the one piece that does not carry
over is schema creation — `flask init-db` exists for exactly that.

`render.yaml` in the repository root is a Render blueprint covering the rest.

---

## ⚠️ Use the Python runtime, never Docker

`backend/Dockerfile` sets `FLASK_DEBUG=1` and runs the reloader. That is correct for local
development and **actively dangerous in public**: switching the Render service to the Docker
runtime puts an interactive Werkzeug traceback on the open internet. `render.yaml` pins
`runtime: python` and serves through gunicorn. Leave it that way.

---

## 1. A MySQL database

Render's managed database is **PostgreSQL only**, so MySQL comes from elsewhere. Porting the
schema to Postgres is not an option: `database/Group05_Createtable.sql` is a graded artifact.

[Aiven for MySQL](https://aiven.io/) has a genuine free plan, needs no credit card, and is real
MySQL rather than a wire-compatible substitute. Alwaysdata's free 100 MB MySQL also works — this
dataset is under 1 MB. Avoid db4free and freesqldatabase; a submitted demo URL that 500s is worse
than no demo.

Take the connection details and build a URL:

```
mysql+pymysql://USER:PASSWORD@HOST:PORT/DBNAME?charset=utf8mb4
```

`?charset=utf8mb4` is not optional — the seed data contains `Radića` and a zero-width space in
`'Tourlos Marina'`, and a provider defaulting to `latin1` will mangle both.

The database name comes from the provider (Aiven calls it `defaultdb`). That is fine:
`Group05_Createtable.sql` has its `CREATE DATABASE` and `USE` lines commented out, so the schema
drops into whatever database the URL selects. **Do not uncomment them.**

## 2. Create the schema — from your machine, before deploying

Render's free tier has no shell, so run this locally against the remote database:

```bash
cd backend
DATABASE_URL='mysql+pymysql://USER:PASSWORD@HOST:PORT/DBNAME?charset=utf8mb4' \
DB_SSL=1 \
  python -m flask --app app init-db
```

Doing it here rather than in a build hook means a TLS problem surfaces as a real traceback
instead of a truncated build log. TLS is the most likely first failure: every managed MySQL
requires it, and a `DATABASE_URL` alone cannot ask PyMySQL for it — hence `DB_SSL=1`. Set
`DB_SSL_CA=/path/to/ca.pem` as well to verify the certificate rather than merely encrypt.

A variable set in the shell beats the repository `.env`, because `load_dotenv()` does not
override — so the command above targets the cloud even with a local `.env` present.

The command prints what it is about to do before touching anything:

```
SQL directory: .../database
Target:        mysql+pymysql://user:***@host:3306/defaultdb?charset=utf8mb4
Schema:        Group05_Createtable.sql
Seed order:    Student1_InsertData_Initial.sql -> Student1_InsertData_Harbours.sql -> ...
```

The seed order is read out of `database/init.sql` rather than hardcoded, because that file is the
graded declaration of it and the order matters: Student1 owns the shared `Office` rows.

| Invocation | Effect |
|---|---|
| `init-db` | Creates tables (`IF NOT EXISTS`), seeds **only if `Office` is empty** |
| `init-db --reset --yes` | Drops all 12 tables, rebuilds, reseeds |
| `init-db --dry-run` | Prints the plan, executes nothing |

Re-running the bare command is a safe no-op. The seeds are plain `INSERT`s and would fail on
duplicate keys, which is what the `Office` gate prevents.

## 3. Deploy

Point Render at the repository; it picks up `render.yaml`. Set `DATABASE_URL` in the dashboard —
it is marked `sync: false` so it is never committed. `SECRET_KEY` is generated for you.

| Variable | Set by | Why |
|---|---|---|
| `DATABASE_URL` | you, in the dashboard | the MySQL above |
| `DB_SSL` | blueprint (`1`) | managed MySQL requires TLS |
| `SECRET_KEY` | blueprint (generated) | the fallback is the well-known string `dev` |
| `SESSION_COOKIE_SECURE` | blueprint (`1`) | Render terminates TLS |
| `TZ` | blueprint (`Europe/Vienna`) | payment deadlines are naive `DATETIME`s |
| `IMAGE_FETCH` | blueprint (`off`) | 0.1 CPU; blocking photo lookups per request |
| `MONGO_URI` | *(unset)* | see below |

## 4. What is degraded, and what is not

**MongoDB is not deployed.** `nosql.available()` returns `False` and only `/manager/nosql`,
`/manager/nosql/migrate` and `/manager/nosql/indexes` show an offline notice. Booking, payment,
the client logbook and every manager page are unaffected — Mongo is a read-side redesign, never
the system of record. Add a MongoDB Atlas free URI as `MONGO_URI` to light those three pages up.

**Cold starts.** Render's free instance sleeps after ~15 minutes and takes about a minute to
wake; a free database may idle off too. First load after a quiet spell is slow. Worth saying out
loud if you are handing the link to someone who will judge it.

**`IMAGE_FETCH=off`** means harbour cards fall back to the built-in pool instead of fetching
photos. Turn it on if the site looks bare and you are willing to spend the request latency.

## 5. Security notes

Passwordless login and the simulated card are deliberate coursework decisions, documented in the
README — not oversights. Three things genuinely matter once this is public:

- `SECRET_KEY` must not be the `dev` fallback. The blueprint generates one; the app now warns at
  startup if it is unset.
- `SESSION_COOKIE_SECURE=1` behind TLS.
- **Never the Docker runtime**, per the warning at the top.

`POST /generate-data` is anonymous and wipes the demo data, which is intentional so a fresh
deployment can be filled without signing in. It is CSRF-protected, so it cannot be triggered
from another site, and it is self-healing by design — pressing it again refills everything.

## 6. Verifying a deployment

1. `/credits` — the health check target; no database, so it answers even if MySQL is asleep.
2. `/login` — lists clients, which proves the seed data is really there.
3. Sign in, search a harbour, book a boat, pay with `4242 4242 4242 4242`.
4. `/manager/login` → `/manager/boats` for the manager side.
