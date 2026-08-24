# Deploying to Render

The app normally runs under Docker Compose, where MariaDB's entrypoint builds the database on
first boot. A managed cloud database has no entrypoint, so the one thing that does not carry over
is schema creation — you run the SQL yourself, once.

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

**TLS goes in the same URL.** Managed MySQL requires it, and SQLAlchemy reads `ssl_ca`,
`ssl_cert`, `ssl_key`, `ssl_capath`, `ssl_cipher` and `ssl_check_hostname` straight out of the
query string and hands them to PyMySQL — so no application code is involved. With the provider's
CA file:

```
mysql+pymysql://USER:PASSWORD@HOST:PORT/DBNAME?charset=utf8mb4&ssl_ca=/etc/secrets/ca.pem
```

On Render, upload the CA as a Secret File (it lands in `/etc/secrets/`) and point `ssl_ca` at it.

The database name comes from the provider (Aiven calls it `defaultdb`). That is fine:
`Group05_Createtable.sql` has its `CREATE DATABASE` and `USE` lines commented out, so the schema
drops into whatever database the URL selects. **Do not uncomment them.**

## 2. Load the schema — once, from your machine

Feed the graded SQL to the MySQL client in the order MariaDB's entrypoint uses. The client
handles these files natively, comments and all, so there is nothing to install into the app and
nothing to parse.

There is no `mysql` binary on a typical Windows box, but the MariaDB image has one:

```bash
cd /path/to/boat_rental_webapp

for f in database/Group05_Createtable.sql \
         database/Student1/Student1_InsertData_Initial.sql \
         database/Student1/Student1_InsertData_Harbours.sql \
         database/Student2/Student2_InsertData_Initial.sql; do
  echo "-> $f"
  docker run --rm -i mariadb:11.3 mariadb \
    -h HOST -P PORT -u USER -pPASSWORD --ssl DBNAME < "$f"
done
```

Order matters: the schema first, then Student1 (which owns the shared `Office` rows), then
Student2. That order is what `database/init.sql` declares — but do not run `init.sql` itself, as
its `SOURCE` paths point inside the MariaDB container and will not resolve here.

The seeds are plain `INSERT`s, so this is a **one-time** load: running it twice fails on duplicate
keys. To start over, drop the tables and repeat.

Verify:

```bash
docker run --rm -i mariadb:11.3 mariadb -h HOST -P PORT -u USER -pPASSWORD --ssl DBNAME \
  -e "SELECT COUNT(*) FROM Office; SELECT COUNT(*) FROM Boat;"
```

Twenty offices and six boats means it worked.

## 3. Deploy

Point Render at the repository; it picks up `render.yaml`. Set `DATABASE_URL` in the dashboard —
it is marked `sync: false` so it is never committed. `SECRET_KEY` is generated for you.

| Variable | Set by | Why |
|---|---|---|
| `DATABASE_URL` | you, in the dashboard | the MySQL above, TLS params included |
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

- `SECRET_KEY` must not be the `dev` fallback. The blueprint generates one; the app warns at
  startup if it is unset.
- `SESSION_COOKIE_SECURE=1` behind TLS.
- **Never the Docker runtime**, per the warning at the top.

`POST /generate-data` is anonymous and wipes the demo data, which is intentional so a fresh
deployment can be filled without signing in. It is CSRF-protected, so it cannot be triggered from
another site, and it is self-healing by design — pressing it again refills everything.

## 6. Verifying a deployment

1. `/credits` — the health check target; no database, so it answers even if MySQL is asleep.
2. `/login` — lists clients, which proves the seed data is really there.
3. Sign in, search a harbour, book a boat, pay with `4242 4242 4242 4242`.
4. `/manager/login` → `/manager/boats` for the manager side.
