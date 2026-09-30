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

Use **[TiDB Cloud Starter](https://tidbcloud.com/)** (formerly "Serverless"). It speaks the MySQL
wire protocol and dialect, is free with no credit card, and — the reason it is here — it
**scales to zero and wakes itself** on the next connection. An idle night costs a few seconds on
the first request, not an outage.

This used to say Aiven. Aiven's free plan *powers the service off* after a period of inactivity
and it stays off until someone logs in to the console and starts it again, which took the whole
site down whenever nobody had visited for a while. Supabase's free tier pauses the same way.
Avoid db4free and freesqldatabase too; a demo URL that 500s is worse than no demo.

**Create the cluster:** sign up, create a *Starter* cluster, pick the AWS **Frankfurt
(eu-central-1)** region to sit next to the Render service, and leave the spending limit at 0 so it
can never bill. Under **Connect**, generate a password and note host, port (`4000`) and user.
The user carries a cluster prefix, e.g. `3xAmPlE.root` — that dot is part of the name.

**Create the database** with a case-insensitive collation. TiDB defaults `utf8mb4` to
`utf8mb4_bin`, which is case-sensitive and sorts `Zadar` before `a…`; MariaDB locally does
neither, so match it explicitly (in TiDB's web *SQL Editor*, or with the client from step 2):

```sql
CREATE DATABASE boatdb CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
```

Then build a URL:

```
mysql+pymysql://USER:PASSWORD@HOST:4000/boatdb?charset=utf8mb4&ssl_ca=/etc/ssl/certs/ca-certificates.crt
```

`?charset=utf8mb4` is not optional — the seed data contains `Radića` and a zero-width space in
`'Tourlos Marina'`, and a client defaulting to `latin1` will mangle both.

**TLS goes in the same URL.** TiDB Cloud refuses plaintext connections. SQLAlchemy reads `ssl_ca`,
`ssl_cert`, `ssl_key`, `ssl_capath`, `ssl_cipher` and `ssl_check_hostname` straight out of the
query string and hands them to PyMySQL, which then verifies both the certificate and the
hostname — so no application code is involved. TiDB's certificate chains to a public root, so
the operating system's CA bundle is enough; `/etc/ssl/certs/ca-certificates.crt` exists on
Render's Python runtime and on Debian/Ubuntu. No Secret File to upload.

`Group05_Createtable.sql` has its `CREATE DATABASE` and `USE` lines commented out, so the schema
drops into whatever database the URL or client selects. **Do not uncomment them.**

## 2. Load the schema — once, from your machine

Feed the graded SQL to the MySQL client in the order MariaDB's entrypoint uses. The client
handles these files natively, comments and all, so there is nothing to install into the app and
nothing to parse.

There is no `mysql` binary on a typical Windows box, but the official image has one. Use the
**`mysql:8`** client against a cloud provider, not `mariadb`: the MariaDB client's TLS and auth
negotiation with managed MySQL-compatible services is unreliable, while the Oracle client just
works.

```bash
cd /path/to/boat_rental_webapp

for f in database/Group05_Createtable.sql          database/Student1/Student1_InsertData_Initial.sql          database/Student1/Student1_InsertData_Harbours.sql          database/Student2/Student2_InsertData_Initial.sql; do
  echo "-> $f"
  docker run --rm -i mysql:8 mysql     -h HOST -P 4000 -u 'PREFIX.root' -p'PASSWORD'     --ssl-mode=REQUIRED --default-character-set=utf8mb4 boatdb < "$f"
done
```

(For the *local* MariaDB, `docker run --rm -i mariadb:11.3 mariadb -h db ...` on the compose
network is the equivalent — that is the pairing this was verified against.)

Order matters: the schema first, then Student1 (which owns the shared `Office` rows), then
Student2. That order is what `database/init.sql` declares — but do not run `init.sql` itself, as
its `SOURCE` paths point inside the MariaDB container and will not resolve here.

The seeds are plain `INSERT`s, so this is a **one-time** load: running it twice fails on duplicate
keys. To start over, `DROP DATABASE boatdb`, recreate it as above and repeat.

Verify:

```bash
docker run --rm -i mysql:8 mysql -h HOST -P 4000 -u 'PREFIX.root' -p'PASSWORD'   --ssl-mode=REQUIRED boatdb   -e "SELECT COUNT(*) FROM Office; SELECT COUNT(*) FROM Boat;"
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
wake; TiDB Starter also scales to zero, but resumes on its own within seconds of the first
connection (gunicorn's `--timeout 60` covers both). Nothing needs a manual restart — the first load
after a quiet spell is just slow.

To skip even that, point a free uptime pinger (cron-job.org, UptimeRobot) at `/credits` every
10 minutes. That keeps the Render instance awake; one service running around the clock fits in
the free plan's monthly instance hours. `/credits` deliberately does not touch the database, so
TiDB still idles down and wakes on real traffic.

**`IMAGE_FETCH=off`** skips only the live Unsplash/Wikipedia lookups for harbours that have no
hand-picked photo; those cards fall back to the built-in pool. The hero rotation, hand-picked
harbour photos and boat photos are plain hotlinks loaded by the browser, so they always show.
Turn it on if you want city-specific photos for new harbours and can spend the request latency.

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

1. `/credits` — the health check target; no database, so it answers even while TiDB is scaled to zero.
2. `/login` — lists clients, which proves the seed data is really there.
3. Sign in, search a harbour, book a boat, pay with `4242 4242 4242 4242`.
4. `/manager/login` → `/manager/boats` for the manager side.
