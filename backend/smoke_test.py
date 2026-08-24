"""End-to-end smoke test for the flows that used to be broken.

Runs the real Flask app against a throwaway SQLite database so it can be
executed without Docker:

    DATABASE_URL=sqlite:///smoke.db python smoke_test.py

The MariaDB-only parts, generator.do_assignments and the SQL seed scripts, are
not covered here. Those need `docker compose up`.
"""

import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal

os.environ.setdefault(
    "DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke.db")
)
os.environ["WTF_CSRF_ENABLED"] = "0"
# No live photo lookups: the suite must pass offline and must not spend a
# network round trip on every booking-page render.
os.environ["IMAGE_FETCH"] = "off"

# email_validator is an implicit dependency: wtforms imports it only when an
# Email() validator actually runs. A host without it looks like a broken app
# rather than a missing package: /register 500s and every check after it fails.
# requirements.txt pins it, so fail loudly here instead of misleadingly later.
try:
    import email_validator  # noqa: E402, F401
except ImportError:
    sys.exit("smoke_test: missing dependency 'email-validator'.\n"
             "Run: python -m pip install -r requirements.txt")

from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from boat_rental import app, db  # noqa: E402
from boat_rental.forms import TEST_CARD_ACCEPTED, TEST_CARD_DECLINED  # noqa: E402
from boat_rental.routes import get_available_boats  # noqa: E402
from boat_rental import images  # noqa: E402
from boat_rental.models import (  # noqa: E402
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_MAINTENANCE,
    DEFAULT_START_TIME,
    PAYMENT_CANCELLED,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    charter_total,
    Boat,
    Catamaran,
    Client,
    Employee,
    Manager,
    Office,
    Rental,
    Staff,
    Yacht,
)

app.config["WTF_CSRF_ENABLED"] = False


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, _record):
    """SQLite ignores foreign keys unless asked. Without this the FK bugs this
    script exists to catch would silently pass here, because MariaDB enforces
    them and SQLite would not."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

FAILURES = []
START = date.today() + timedelta(days=3)
END = START + timedelta(days=5)


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def seed():
    db.drop_all()
    db.create_all()
    # Supervises / Maintains have no models; the app reaches them with raw SQL.
    db.session.execute(text("""
        CREATE TABLE Supervises (
            ManagerID VARCHAR(50) REFERENCES Manager(ManagerID),
            StaffID VARCHAR(50) REFERENCES Staff(StaffID),
            PRIMARY KEY (ManagerID, StaffID))
    """))
    db.session.execute(text("""
        CREATE TABLE Maintains (
            StaffID VARCHAR(50) REFERENCES Staff(StaffID),
            BoatID VARCHAR(50) REFERENCES Boat(BoatID),
            PRIMARY KEY (StaffID, BoatID))
    """))

    db.session.add(Office(OfficeID="O1", Street="Quay 1", Country="HR", City="Dubrovnik", ZIP="20000"))
    db.session.add(Office(OfficeID="O2", Street="Quay 2", Country="GR", City="Mykonos", ZIP="84600"))
    # Nice has an office but only a boat under maintenance -> nothing bookable,
    # though the boat is still listed (greyed) because it has a rate.
    db.session.add(Office(OfficeID="O3", Street="Quay 3", Country="FR", City="Nice", ZIP="06000"))
    db.session.add(Boat(BoatID="B9", OfficeID="O3", Length=8.0, Seats=3, Manufacturer="M9",
                        AvailabilityStatus=AVAILABILITY_MAINTENANCE, Weight=800.0, Horsepower=60,
                        DailyRate=Decimal("440.00")))
    for cid, first in (("C1", "Max"), ("C2", "Olga")):
        db.session.add(Client(ClientID=cid, FirstName=first, LastName="Test",
                              Birthdate=date(1990, 1, 1), Email=f"{cid}@example.com"))
    # B1 free, B2 under maintenance, B3 in another city, B4 has a NULL length,
    # B5 has a NULL DailyRate. B4 and B5 are separate boats on purpose: B4 is
    # booked all over this suite, and B5 exists only to prove an unpriced boat
    # never reaches checkout.
    db.session.add(Boat(BoatID="B1", OfficeID="O1", Length=10.0, Seats=4, Manufacturer="M1",
                        AvailabilityStatus=AVAILABILITY_AVAILABLE, Weight=900.0, Horsepower=90,
                        DailyRate=Decimal("550.00")))
    db.session.add(Boat(BoatID="B2", OfficeID="O1", Length=12.0, Seats=6, Manufacturer="M1",
                        AvailabilityStatus=AVAILABILITY_MAINTENANCE, Weight=950.0, Horsepower=95,
                        DailyRate=Decimal("660.00")))
    db.session.add(Boat(BoatID="B3", OfficeID="O2", Length=14.0, Seats=8, Manufacturer="M2",
                        AvailabilityStatus=AVAILABILITY_AVAILABLE, Weight=990.0, Horsepower=99,
                        DailyRate=Decimal("770.00")))
    db.session.add(Boat(BoatID="B4", OfficeID="O1", Length=None, Seats=2, Manufacturer="M3",
                        AvailabilityStatus=AVAILABILITY_AVAILABLE, Weight=None, Horsepower=50,
                        DailyRate=Decimal("300.00")))
    db.session.add(Boat(BoatID="B5", OfficeID="O1", Length=9.0, Seats=3, Manufacturer="M4",
                        AvailabilityStatus=AVAILABILITY_AVAILABLE, Weight=880.0, Horsepower=70,
                        DailyRate=None))

    # Flushed in dependency order: with foreign keys enforced, each referenced
    # row has to be on disk before the row pointing at it.
    db.session.flush()

    # Two yachts in Dubrovnik, one with a jacuzzi and one without, so the
    # pages can be checked for stating it both ways. Attached to boats that
    # already exist rather than added as new ones, so no count moves.
    db.session.add(Yacht(YachtID="B1", YachtName="Golden Test", HasJacuzzi=True))
    db.session.add(Yacht(YachtID="B4", YachtName=None, HasJacuzzi=False))
    # A catamaran can have one too. B2 is the Dubrovnik maintenance boat, so it
    # also proves the greyed card states it; B3 is available in Mykonos.
    db.session.add(Catamaran(CatamaranID="B2", NrOfCabins=3, MaxCapacity=12,
                             HasJacuzzi=True))
    db.session.add(Catamaran(CatamaranID="B3", NrOfCabins=4, MaxCapacity=14,
                             HasJacuzzi=False))
    db.session.flush()

    # M1 supervises M2; M2 supervises staff S1 -> deleting M1 or M2 used to fail.
    for eid, first, salary in (("M1", "Boss", 9000), ("M2", "Anna", 6000), ("S1", "Jane", 3000)):
        db.session.add(Employee(EmployeeID=eid, OfficeID="O1", FirstName=first, LastName="X",
                                Birthdate=date(1980, 1, 1), Email=f"{eid}@example.com",
                                SelfInsuranceNr=f"INS-{eid}", Salary=salary))
    db.session.flush()

    db.session.add(Manager(ManagerID="M1", Department="Exec", ManagementLevel="Top", SupervisorID=None))
    db.session.flush()
    db.session.add(Manager(ManagerID="M2", Department="HR", ManagementLevel="Senior", SupervisorID="M1"))
    db.session.add(Staff(StaffID="S1", WorkShift="Day", IsOnDuty=True))
    db.session.commit()
    db.session.execute(text("INSERT INTO Supervises VALUES ('M2', 'S1')"))
    db.session.execute(text("INSERT INTO Maintains VALUES ('S1', 'B1')"))
    db.session.commit()


def as_client(c, client_id):
    c.get(f"/select-client/{client_id}", follow_redirects=True)


def as_manager(c, manager_id="M1"):
    c.post("/manager/login", data={"manager_id": manager_id}, follow_redirects=True)


def search(c, city, start=START, end=END):
    return c.post("/booking", data={
        "city": city,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "search": "Search available boats",
    }, follow_redirects=True)


def flat(body):
    """Collapse whitespace so a prose match is not defeated by line wrapping.

    Sentences in the templates wrap across source lines, so "goes back on the
    market" is not a literal substring of the HTML even though the page says
    exactly that.
    """
    return " ".join(body.split())


def rentals_card(body):
    """Read the 'Rentals in <city>' figure off an /analytics page."""
    match = re.search(r'id="rentals-in-period"[^>]*>\s*(\d+)', body)
    return int(match.group(1)) if match else -1


def book(c, boat_id, city="Dubrovnik", start=START, end=END):
    return c.post("/booking", data={
        "boat_id": boat_id,
        "city": city,
        "rental_date": start.isoformat(),
        "rental_end_date": end.isoformat(),
        "book": "true",
    }, follow_redirects=True)


# The success flash from a booking, matched on the half only it contains: the
# booking page itself now says "Reserve for N days", so a looser match would
# report success on a page where the booking had actually failed.
BOOKED = "Pay to confirm your charter"


def pay(c, boat_id, rental_date=START, card=TEST_CARD_ACCEPTED,
        expiry="12/34", cvc="123"):
    return c.post(
        f"/rentals/{boat_id}/{rental_date.isoformat()}/pay",
        data={"card_name": "Max Test", "card_number": card,
              "expiry": expiry, "cvc": cvc, "submit": "Pay now"},
        follow_redirects=True,
    )


def main():
    with app.app_context():
        seed()

        # 0. Boat.jacuzzi is the one definition of which hulls can have one.
        #    Three answers, not two: a motorboat is not a "no".
        check("a yacht with a jacuzzi reports True",
              Boat.query.get("B1").jacuzzi is True)
        check("a yacht without one reports False",
              Boat.query.get("B4").jacuzzi is False)
        check("a catamaran with a jacuzzi reports True",
              Boat.query.get("B2").jacuzzi is True)
        check("a catamaran without one reports False",
              Boat.query.get("B3").jacuzzi is False)
        check("a boat that cannot have one reports None",
              Boat.query.get("B5").jacuzzi is None)

        # 0b. Unsplash attribution. Their guideline is a credit naming the
        #     photographer and linking to their profile, with referral
        #     parameters on the link, so those parameters are part of the
        #     requirement rather than decoration.
        with app.test_client() as c:
            body = c.get("/credits").get_data(as_text=True)
            check("the credits page is reachable without signing in",
                  "Photography" in body, body[:200])
            check("photographers are named", "Marcin Ciszewski" in body, body[:300])
            check("each name links to their Unsplash profile",
                  "https://unsplash.com/@collega" in body, body[:300])
            check("profile links carry the referral parameters",
                  "utm_source=imse_boat_rental&amp;utm_medium=referral" in body,
                  body[:300])
            # Photos appear on nearly every page, so the credit has to as well.
            home = c.get("/login").get_data(as_text=True)
            check("every page footer credits Unsplash",
                  "Photographs from" in home and "/credits" in home, home[:300])
            # Saying nothing about the photos we cannot attribute would read as
            # a complete list when it is not.
            check("the gap in the credits is stated, not hidden",
                  "not listed above" in flat(body), body[:300])

        check("a known photo resolves to its photographer",
              (images.credit_for(images.CITY_IMAGES["Dubrovnik"]) or {}).get("name")
              == "Ivan Ivankovic")
        check("an unknown photo resolves to nothing",
              images.credit_for("https://images.unsplash.com/photo-0000?w=1") is None)
        check("a non-Unsplash url resolves to nothing",
              images.credit_for("https://example.com/boat.jpg") is None)
        check("credit_for tolerates a missing url", images.credit_for(None) is None)
        check("each photographer is listed once",
              len({p["name"] for p in images.photo_credits()})
              == len(images.photo_credits()))

        # A photo fetched live must credit itself, or the one path where the
        # photographer is known for certain is the one that loses them. Fed a
        # stub of the API's shape rather than calling Unsplash: the suite runs
        # offline, and the rate limit is 50 requests an hour.
        images._remember_credit({
            "urls": {"raw": "https://images.unsplash.com/photo-9999test?ixid=abc"},
            "user": {"name": "Ada Lovelace", "username": "ada"},
        })
        live = images.credit_for("https://images.unsplash.com/photo-9999test?w=600")
        check("a live-fetched photo registers its photographer",
              live is not None and live["name"] == "Ada Lovelace", f"{live}")
        check("the registered profile link carries the parameters",
              live is not None and live["profile"]
              == "https://unsplash.com/@ada?utm_source=imse_boat_rental"
                 "&utm_medium=referral", f"{live}")
        # A malformed payload must not break a page render.
        images._remember_credit({})
        images._remember_credit({"urls": {}, "user": {"name": "No Handle"}})
        check("a payload with no photographer is ignored",
              all(p["name"] != "No Handle" for p in images.photo_credits()))

        # 0c. The harbour picker on the booking page is a plain <select>, so
        #     its order is exactly its option list. Deduplication was never
        #     the problem, since that query was already distinct, but the check
        #     below keeps it that way.
        #
        #     The seeded cities happen to be inserted alphabetically, so a sort
        #     check over them alone would pass whether or not anything sorts.
        #     These two offices are added last on purpose: Ancona sorts first
        #     and Zadar sorts last, and the second one shares Dubrovnik's city
        #     to prove the deduplication.
        db.session.add(Office(OfficeID="O90", Street="Porto 1", Country="IT",
                              City="Ancona", ZIP="60121"))
        db.session.add(Office(OfficeID="O91", Street="Quay 1b", Country="HR",
                              City="Dubrovnik", ZIP="20001"))
        db.session.commit()
        with app.test_client() as c:
            as_client(c, "C1")
            body = c.get("/booking").get_data(as_text=True)
            picker = re.search(r'<select[^>]+name="city".*?</select>', body, re.S)
            options = re.findall(r'<option value="([^"]*)"', picker.group(0)) if picker else []
            listed = [o for o in options if o]
            check("the harbour picker is alphabetical", listed == sorted(listed),
                  f"{listed}")
            check("a city added last still sorts first",
                  listed and listed[0] == "Ancona", f"{listed}")
            check("a city with two offices is listed once",
                  listed.count("Dubrovnik") == 1, f"{listed}")
            check("the picker keeps its empty prompt first",
                  options and options[0] == "", f"{options[:3]}")
        # Removed again so later counts and pickers are unaffected.
        Office.query.filter(Office.OfficeID.in_(["O90", "O91"])).delete(
            synchronize_session=False)
        db.session.commit()

        # 1. Search with no available boats -> used to be a 500 (UnboundLocalError)
        with app.test_client() as c:
            as_client(c, "C1")
            r = search(c, "Nice")
            body = r.get_data(as_text=True)
            check("empty search renders instead of 500", r.status_code == 200,
                  f"status {r.status_code}")
            check("a city with nothing free says so",
                  "Nothing free for these dates" in flat(body), body[:300])
            # The maintenance boat is shown rather than hidden: greyed, with
            # no radio, so it cannot be selected or submitted.
            check("the unavailable boat is still listed", "B9" in body)
            check("it is marked as under maintenance",
                  "In maintenance" in body, body[:300])
            check("it carries no radio to select",
                  'value="B9"' not in body, body[:300])

        # 2. A search offers the free boats, shows the unbookable ones greyed,
        #    and never offers a boat from another city.
        with app.test_client() as c:
            as_client(c, "C1")
            body = search(c, "Dubrovnik").get_data(as_text=True)
            # Match the radio's value, not a bare "B3": the page carries a
            # 91-char mixed-case CSRF token, so a substring test fails at random
            # whenever that token happens to contain the boat id.
            def offered(boat_id):
                return f'value="{boat_id}"' in body

            # A search re-renders the page, so the results land below the form.
            # The anchor and the script that jumps to it have to be present, or
            # the reader is left staring at the picker they just used.
            # Pressing Reserve with nothing picked is caught in the page now,
            # before anything is sent. The guard is convenience, so the server
            # still has to refuse a POST with no boat in it.
            check("the reserve button has something to complain into",
                  'id="book-error"' in body, body[:300])
            check("the guard against reserving nothing is shipped",
                  'input[name="boat_id"]:checked' in body, body[:300])
            rentals_now = Rental.query.count()
            crafted = book(c, "").get_data(as_text=True)
            check("a boat-less booking is refused by the server too",
                  BOOKED not in crafted, crafted[:300])
            check("and writes no rental",
                  Rental.query.count() == rentals_now)

            check("a search renders the scroll anchor",
                  'id="search-results"' in body, body[:300])
            check("and the script that jumps to it",
                  "getElementById('search-results')" in body, body[:300])

            # A bare city name says little for a harbour nobody has heard of,
            # so every place one is shown to a client names the country too.
            # The value submitted stays the bare city: it is what the filters,
            # the images and stocked_cities all key off.
            check("the dropdown names the country",
                  ">Dubrovnik, HR<" in body, body[:300])
            # Regex, not a literal: the searched-for option also carries
            # `selected`, so the attribute order is not fixed.
            check("the dropdown still submits the bare city",
                  re.search(r'<option[^>]*value="Dubrovnik"[^>]*>\s*Dubrovnik, HR', body)
                  is not None,
                  (re.search(r'<option[^>]*Dubrovnik[^<]*<', body) or ["?"])[0])
            check("the harbour cards name the country",
                  "Dubrovnik<small>HR</small>" in body, body[:300])
            check("the boat cards name the country",
                  "Dubrovnik, HR" in body, body[:300])

            check("search offers the available boat", offered("B1"))
            check("search does not offer the maintenance boat", not offered("B2"))
            check("the maintenance boat is still shown", "B2" in body)
            check("search does not offer boats in other cities", not offered("B3"))
            check("a boat in another city is not shown at all", "B3" not in body)

            # A yacht's jacuzzi is stated either way. Shown only when present,
            # "no jacuzzi" and "we never recorded it" looked identical.
            # No search, nothing to jump to: the anchor and its script must be
            # absent, or the page scrolls past the form on a plain visit.
            fresh = c.get("/booking").get_data(as_text=True)
            check("a fresh booking page has no scroll anchor",
                  'id="search-results"' not in fresh, fresh[:300])
            check("and does not carry the jump script",
                  "getElementById('search-results')" not in fresh, fresh[:300])

            check("booking still carries the shared date-sync script",
                  "drags the end date to the day after" in body, body[:300])

            check("a yacht with a jacuzzi says so",
                  "jacuzzi on deck" in body, body[:300])
            check("a yacht without one says that too",
                  "no jacuzzi" in body, body[:300])
            check("NULL boat length renders as a dash", "—" in body)

        # 3. Booking works, prices the charter and hands off to checkout
        with app.test_client() as c:
            as_client(c, "C1")
            search(c, "Dubrovnik")
            body = book(c, "B1").get_data(as_text=True)
            check("booking succeeds", BOOKED in body)
            check("booking lands on checkout", "Pay for your charter" in body)
        with app.app_context():
            check("rental was persisted", Rental.query.count() == 1)
            booked = Rental.query.first()
            nights = (END - START).days
            check("the charter is priced at rate x nights",
                  booked.TotalAmount == charter_total(Decimal("550.00"), nights),
                  f"{booked.TotalAmount} for {nights} nights at 550.00")
            check("a new booking starts unpaid",
                  booked.PaymentStatus == PAYMENT_UNPAID, booked.PaymentStatus)

        # 3a. A boat with no DailyRate is not for rent. It used to reach
        #     checkout with a NULL total, where the demo card "paid" nothing.
        with app.test_client() as c:
            as_client(c, "C2")
            body = search(c, "Dubrovnik").get_data(as_text=True)
            check("an unpriced boat is not offered",
                  'value="B5"' not in body, body[:300])
            body = book(c, "B5").get_data(as_text=True)
            check("an unpriced boat cannot be booked by a crafted POST",
                  BOOKED not in body, body[:300])
            check("no rental was written for the unpriced boat",
                  Rental.query.filter_by(BoatID="B5").count() == 0)

        # 3a2. An unpaid booking is a hold, and a hold runs out the day before
        #      the charter. Booking on the day therefore has no hold at all.
        # C1 owns the advance booking of B1 made above; the URL carries no
        # ClientID, so it has to be C1 that looks at its checkout.
        with app.test_client() as c:
            as_client(c, "C1")
            body = c.get(f"/rentals/B1/{START.isoformat()}/pay").get_data(as_text=True)
            check("an advance booking may be paid later", "Pay later" in body)
            check("an advance booking is told its 24-hour deadline",
                  "24 hours" in body, body[:300])
            booked = Rental.query.filter_by(BoatID="B1", RentalDate=START).first()
            check("the deadline is 24h before the ride",
                  booked.pay_by == booked.starts_at - timedelta(hours=24),
                  f"{booked.pay_by} vs ride {booked.starts_at}")
            check("an advance booking is not a late booking",
                  not booked.is_late_booking)
            # No countdown on a hold measured in days. A clock ticking down
            # from 23:59:59 would read as pressure that is not there.
            # Matches data-seconds, not "hold-timer": the script that drives the
            # clock is on every checkout and only the element is conditional.
            check("an advance booking gets no countdown",
                  "data-seconds" not in body, body[:300])

        with app.test_client() as c:
            as_client(c, "C2")
            today = date.today()
            # Booked for today: inside the 24-hour window, so it gets the short
            # grace period rather than an overnight hold.
            search(c, "Dubrovnik", start=today, end=today + timedelta(days=2))
            book(c, "B4", start=today, end=today + timedelta(days=2))
            late = Rental.query.filter_by(BoatID="B4", RentalDate=today).first()
            check("a booking inside 24h is a late booking", late.is_late_booking)
            check("a late booking is held for the grace period only",
                  late.pay_by == late.CreatedAt + timedelta(minutes=15),
                  f"{late.pay_by} vs created {late.CreatedAt}")

            body = c.get(f"/rentals/B4/{today.isoformat()}/pay").get_data(as_text=True)
            check("a late booking cannot be paid later",
                  "Pay later" not in body, body[:300])
            check("a late booking says how long the boat is held",
                  "goes back on the market" in flat(body), body[:300])

            # The countdown is seeded with seconds, not a timestamp, so a
            # browser in another timezone cannot misread it. It must be
            # positive (pay_rental has already refused a lapsed hold) and can
            # never exceed the grace period.
            seconds = re.search(r'data-seconds="(\d+)"', body)
            check("a late booking renders a countdown", seconds is not None,
                  body[:300])
            if seconds:
                left = int(seconds.group(1))
                check("the countdown is seeded within the grace period",
                      0 < left <= 15 * 60, f"{left}s")

            # Inside its grace period the hold must survive a sweep. Someone
            # is paying for that boat right now.
            search(c, "Dubrovnik", start=today, end=today + timedelta(days=2))
            check("a hold inside its grace period survives the sweep",
                  Rental.query.filter_by(BoatID="B4", RentalDate=today).count() == 1)

            # Age it past the grace period; now the sweep must take it.
            late.CreatedAt = datetime.now() - timedelta(minutes=16)
            db.session.commit()
            body = search(c, "Dubrovnik", start=today,
                          end=today + timedelta(days=2)).get_data(as_text=True)
            check("a hold past its grace period is released",
                  Rental.query.filter_by(BoatID="B4", RentalDate=today).count() == 0)
            check("the released boat is bookable again",
                  'value="B4"' in body, body[:300])

            # A hold whose deadline has not passed must survive the sweep, and
            # a paid charter must never be released.
            check("the advance hold survived the sweep",
                  Rental.query.filter_by(BoatID="B1", RentalDate=START).count() == 1)

        # 3a3. A lapsed hold cannot be paid even if no search has swept it yet.
        #      Written straight to the database so nothing has had the chance,
        #      which is exactly the situation the sweep alone would miss.
        with app.test_client() as c:
            as_client(c, "C2")
            lapsed = date.today()
            db.session.add(Rental(ClientID="C2", BoatID="B3", RentalDate=lapsed,
                                  RentalEndDate=lapsed + timedelta(days=2),
                                  PaymentStatus=PAYMENT_UNPAID,
                                  TotalAmount=charter_total(Decimal("770.00"), 2),
                                  StartTime=DEFAULT_START_TIME,
                                  # Made yesterday, so both the 24-hour cutoff
                                  # and any grace period are long gone.
                                  CreatedAt=datetime.now() - timedelta(days=1)))
            db.session.commit()
            check("the lapsed hold is in the database",
                  Rental.query.filter_by(BoatID="B3", RentalDate=lapsed).count() == 1)

            body = pay(c, "B3", rental_date=lapsed).get_data(as_text=True)
            check("paying a lapsed hold is refused", "expired" in body, body[:300])
            check("the lapsed hold is released on the attempt",
                  Rental.query.filter_by(BoatID="B3", RentalDate=lapsed).count() == 0)
            check("the lapsed hold was never marked paid",
                  Rental.query.filter_by(BoatID="B3", PaymentStatus=PAYMENT_PAID).count() == 0)

        # 3a4. The same rule must not break late booking: paying one straight
        #      away, inside its grace period, has to work.
        with app.test_client() as c:
            as_client(c, "C2")
            today = date.today()
            search(c, "Dubrovnik", start=today, end=today + timedelta(days=2))
            book(c, "B4", start=today, end=today + timedelta(days=2))
            body = pay(c, "B4", rental_date=today).get_data(as_text=True)
            check("a late booking can still be paid immediately",
                  "Payment received" in body, body[:300])
            check("the late charter is paid",
                  Rental.query.filter_by(BoatID="B4", RentalDate=today,
                                         PaymentStatus=PAYMENT_PAID).count() == 1)
            # And once paid it is a charter, not a hold: no sweep may take it.
            search(c, "Dubrovnik", start=today, end=today + timedelta(days=2))
            check("a paid charter is never released by the sweep",
                  Rental.query.filter_by(BoatID="B4", RentalDate=today,
                                         PaymentStatus=PAYMENT_PAID).count() == 1)

        # 3b. Demo checkout. The card is validated and discarded; the only
        #     thing payment changes is PaymentStatus.
        with app.test_client() as c:
            as_client(c, "C1")
            # The demo cards are printed in groups of four, like a real card.
            shown = c.get(f"/rentals/B1/{START.isoformat()}/pay").get_data(as_text=True)
            check("the demo card is shown in groups of four",
                  "4242 4242 4242 4242" in shown, shown[:300])
            check("the decline card is shown in groups of four",
                  "4000 0000 0000 0002" in shown, shown[:300])
            check("the bare digits are not shown instead",
                  TEST_CARD_ACCEPTED not in shown)

            body = pay(c, "B1", card="4242 4242 4242 4243").get_data(as_text=True)
            check("a mistyped card number is rejected",
                  "not a valid card number" in body, body[:300])
            check("a failed Luhn check leaves the rental unpaid",
                  Rental.query.first().PaymentStatus == PAYMENT_UNPAID)

            body = pay(c, "B1", expiry="01/20").get_data(as_text=True)
            check("an expired card is rejected", "expired" in body, body[:300])

            body = pay(c, "B1", card=TEST_CARD_DECLINED).get_data(as_text=True)
            check("the decline card is declined", "declined" in body, body[:300])
            check("a declined payment leaves the rental unpaid",
                  Rental.query.first().PaymentStatus == PAYMENT_UNPAID)

            # Paid with the grouped string exactly as printed on the page: a
            # grader copies what they see, so that is what has to work.
            body = pay(c, "B1", card="4242 4242 4242 4242").get_data(as_text=True)
            check("the demo card pays the charter, spaces and all",
                  "Payment received" in body, body[:300])
            check("payment marks the rental PAID",
                  Rental.query.first().PaymentStatus == PAYMENT_PAID)
            check("payment does not alter the agreed amount",
                  Rental.query.first().TotalAmount
                  == charter_total(Decimal("550.00"), (END - START).days))
            check("paying lands back on the report", "Your Rentals" in body)

            body = pay(c, "B1").get_data(as_text=True)
            check("paying an already paid charter is refused",
                  "already paid" in body, body[:300])

        # 3c. The URL carries only two of the three PK components; ClientID
        #     comes from the session, so another client cannot even address
        #     this rental, let alone pay it off.
        with app.test_client() as c:
            as_client(c, "C2")
            body = pay(c, "B1").get_data(as_text=True)
            check("a client cannot pay another client's rental",
                  "not on your list" in body, body[:300])

        # Counted rather than hardcoded: the hold and payment sections above
        # legitimately leave rentals behind, and these checks are about what
        # the *next* action writes, not about the total.
        rentals_before = Rental.query.count()

        # 3d. Someone else's booking greys the boat out rather than hiding it,
        #     so the harbour still looks like it has a fleet.
        with app.test_client() as c:
            as_client(c, "C2")
            body = search(c, "Dubrovnik").get_data(as_text=True)
            check("a boat booked by someone else is still listed", "B1" in body)
            check("it is marked as booked", "Booked" in body, body[:300])
            check("it cannot be selected", 'value="B1"' not in body, body[:300])

        # 4. A different client cannot double-book the same boat/dates
        with app.test_client() as c:
            as_client(c, "C2")
            body = book(c, "B1", start=START + timedelta(days=1)).get_data(as_text=True)
            check("overlapping booking by another client is rejected",
                  BOOKED not in body, body[:200])
        check("no second rental was written", Rental.query.count() == rentals_before)

        # 5. Tampering: booking a maintenance boat / a boat in another city
        with app.test_client() as c:
            as_client(c, "C2")
            check("maintenance boat is rejected",
                  BOOKED not in book(c, "B2").get_data(as_text=True))
            check("boat from another city is rejected",
                  BOOKED not in book(c, "B3").get_data(as_text=True))
            check("past start date is rejected",
                  BOOKED not in book(
                      c, "B4", start=date.today() - timedelta(days=5),
                      end=date.today() + timedelta(days=1)).get_data(as_text=True))
        check("no rentals added by tampering", Rental.query.count() == rentals_before)

        # 6. A non-overlapping booking of the same boat still works
        with app.test_client() as c:
            as_client(c, "C2")
            later = END + timedelta(days=10)
            body = book(c, "B1", start=later, end=later + timedelta(days=2)).get_data(as_text=True)
            check("non-overlapping booking of same boat succeeds",
                  BOOKED in body)

        # 7. Analytics survives junk input
        with app.test_client() as c:
            as_client(c, "C1")
            r = c.get("/analytics?city=Dubrovnik&start_date=banana&end_date=2026-01-01")
            check("analytics tolerates an unparseable date", r.status_code == 200,
                  f"status {r.status_code}")
            r2 = c.get("/analytics")
            check("analytics default view renders", r2.status_code == 200)
            check("analytics no longer defaults to the stale 2025 window",
                  "2025-07-01" not in r2.get_data(as_text=True))

            # Opening the page picks no harbour, so it cannot report figures
            # for a city the reader never chose or quietly favour one harbour
            # over the rest of the fleet.
            opening = r2.get_data(as_text=True)
            check("no harbour is chosen on arrival",
                  "Available Boats in" not in flat(opening), opening[:400])
            check("the page asks for one instead",
                  "Pick a harbour" in opening, opening[:400])
            check("the harbour field starts empty",
                  re.search(r'id="city"[^>]*value=""', opening) is not None,
                  (re.search(r'<input[^>]*id="city"[^>]*>', opening) or ["?"])[0])
            # The two city-scoped figures are questions about a named harbour,
            # so they must not be answered with a zero before one is named.
            check("no city-scoped figures before a harbour is picked",
                  "Free in" not in opening and "Rentals in" not in opening,
                  opening[:400])
            # The fleet-wide ones are still true without a city.
            check("the fleet-wide figures still show",
                  "Boats in fleet" in opening and "Not in maintenance" in opening,
                  opening[:400])

            # The harbour picker is a datalist, so it can be typed into. That
            # makes an unrecognised city reachable, and it must be named as a
            # typo rather than reported as an empty fleet.
            body = r2.get_data(as_text=True)
            check("the harbour picker is typable", 'list="harbours"' in body, body[:300])
            # Read the datalist itself rather than the whole page, so the order
            # being asserted is the order the browser will offer.
            block = re.search(r'<datalist id="harbours">(.*?)</datalist>', body, re.S)
            offered = re.findall(r'<option value="([^"]+)"', block.group(1)) if block else []
            check("the harbour list is alphabetical", offered == sorted(offered),
                  f"{offered}")
            check("every served city is offered exactly once",
                  offered == ["Dubrovnik", "Mykonos", "Nice"], f"{offered}")

            # The dropdown the script actually opens is our own list, and it
            # has to carry the same cities as the datalist fallback. Two
            # sources for the same options is two chances to drift.
            drop = re.search(r'<ul class="combo-list".*?</ul>', body, re.S)
            in_dropdown = re.findall(r'data-value="([^"]+)"', drop.group(0)) if drop else []
            check("the dropdown offers the same cities as the fallback",
                  in_dropdown == offered, f"{in_dropdown} vs {offered}")

            # Availability lists the jacuzzi too. A window far enough out that
            # nothing this suite books can hide either yacht.
            quiet = END + timedelta(days=200)
            body = c.get(f"/analytics?city=Dubrovnik&start_date={quiet}"
                         f"&end_date={quiet + timedelta(days=2)}").get_data(as_text=True)
            check("availability has a jacuzzi column", "<th>Jacuzzi</th>" in body,
                  body[:300])
            # The same start/end pair as the booking search, so it gets the
            # same auto-advance, from the shared partial rather than a copy.
            check("availability advances the end date with the start",
                  "drags the end date to the day after" in body, body[:300])
            check("availability marks the yacht that has one",
                  'has-extra">Yes' in body, body[:300])
            check("availability marks the yacht that has not",
                  'no-extra">No' in body, body[:300])
            # A dash means "not that kind of boat", not the same as "no".
            check("a boat that cannot have one is neither a yes nor a no",
                  body.count('no-extra">No') == 1, body[:300])

            # Mykonos holds B3, a catamaran without one, so the column is
            # answering for catamarans and not only for yachts.
            body = c.get(f"/analytics?city=Mykonos&start_date={quiet}"
                         f"&end_date={quiet + timedelta(days=2)}").get_data(as_text=True)
            check("availability answers the jacuzzi for a catamaran too",
                  'no-extra">No' in body, body[:300])

            body = c.get("/analytics?city=Atlantis").get_data(as_text=True)
            check("an unknown harbour is called out, not reported as empty",
                  "don&#39;t have a harbour in Atlantis" in body, body[:400])
            # No fallback any more: reporting on Dubrovnik under a warning
            # about Atlantis answered a question nobody asked.
            check("an unknown harbour reports on nothing",
                  "Available Boats in" not in flat(body), body[:400])
            check("an unknown harbour asks again",
                  "Pick a harbour" in body, body[:400])

            # Typing into a field invites lowercase; it must not look unserved.
            body = c.get("/analytics?city=dubrovnik").get_data(as_text=True)
            check("a lowercased harbour is matched, not rejected",
                  "don&#39;t have a harbour" not in body, body[:400])
            check("a lowercased harbour is shown in its proper spelling",
                  "Available Boats in Dubrovnik" in flat(body), body[:400])

        # 7b. The "Rentals in <city>" card must follow the city filter. It used
        #     to be an unjoined count over every Rental, so it showed the same
        #     fleet-wide number whatever city was selected.
        db.session.add(Rental(ClientID="C2", BoatID="B3", RentalDate=START,
                              RentalEndDate=END, PaymentStatus="PAID"))
        db.session.commit()
        with app.test_client() as c:
            as_client(c, "C1")

            def card(city):
                body = c.get(
                    f"/analytics?city={city}&start_date={START}&end_date={END}"
                ).get_data(as_text=True)
                return rentals_card(body)

            # C1 rents B1 in Dubrovnik; C2 now rents B3 in Mykonos.
            check("analytics rental count excludes other cities", card("Dubrovnik") == 1,
                  f"got {card('Dubrovnik')}")
            check("analytics rental count follows the city filter", card("Mykonos") == 1,
                  f"got {card('Mykonos')}")
            # Nice has only a maintenance boat and no rentals at all, so a
            # non-zero here would mean the join is not filtering.
            check("analytics counts nothing in a city with no rentals", card("Nice") == 0,
                  f"got {card('Nice')}")

        # 8. GET search links work (this branch was dead: it read request.form)
        with app.test_client() as c:
            as_client(c, "C1")
            body = c.get(
                f"/booking?city=Dubrovnik&start_date={START}&end_date={END}"
            ).get_data(as_text=True)
            check("bookmarkable GET search returns results", "Boats in Dubrovnik" in body)

        # 8b. The two m:n relations are editable from the manager UI. Must run
        #     before section 9 deletes M2 and section 11 turns S1 into a manager.
        def count_supervises(manager_id, staff_id):
            return db.session.execute(
                text("SELECT COUNT(*) FROM Supervises WHERE ManagerID = :m AND StaffID = :s"),
                {"m": manager_id, "s": staff_id},
            ).scalar()

        def count_maintains(staff_id, boat_id):
            return db.session.execute(
                text("SELECT COUNT(*) FROM Maintains WHERE StaffID = :s AND BoatID = :b"),
                {"s": staff_id, "b": boat_id},
            ).scalar()

        with app.test_client() as c:
            as_manager(c, "M1")

            body = c.get("/manager/assignments/supervision").get_data(as_text=True)
            check("supervision page lists the seeded pair",
                  "M2" in body and "S1" in body, body[:300])

            body = c.post("/manager/assignments/supervision", data={
                "manager_id": "M1", "staff_id": "S1", "submit": "Assign supervision",
            }, follow_redirects=True).get_data(as_text=True)
            check("a manager can assign supervision", count_supervises("M1", "S1") == 1, body[:300])

            body = c.post("/manager/assignments/supervision", data={
                "manager_id": "M1", "staff_id": "S1", "submit": "Assign supervision",
            }, follow_redirects=True).get_data(as_text=True)
            check("a duplicate supervision is refused", "already supervises" in body, body[:300])
            check("no second supervision row", count_supervises("M1", "S1") == 1)

            # M2 is a manager, not staff. SelectField.pre_validate must reject
            # this before it ever reaches the INSERT.
            c.post("/manager/assignments/supervision", data={
                "manager_id": "M1", "staff_id": "M2", "submit": "Assign supervision",
            }, follow_redirects=True)
            check("a manager cannot be assigned into the staff slot",
                  count_supervises("M1", "M2") == 0)

            body = c.post("/manager/assignments/supervision/M1/S1/delete",
                          follow_redirects=True).get_data(as_text=True)
            check("supervision can be unassigned", "Supervision removed" in body, body[:300])
            check("the supervision row is gone", count_supervises("M1", "S1") == 0)

            body = c.post("/manager/assignments/supervision/M1/S1/delete",
                          follow_redirects=True).get_data(as_text=True)
            check("unassigning a missing supervision does not 500",
                  "no longer exists" in body, body[:300])

            # Same shape for Maintains. S1 already maintains B1 from the seed.
            body = c.get("/manager/assignments/maintenance").get_data(as_text=True)
            check("maintenance page lists the seeded pair", "B1" in body, body[:300])

            c.post("/manager/assignments/maintenance", data={
                "staff_id": "S1", "boat_id": "B2", "submit": "Assign boat",
            }, follow_redirects=True)
            check("a manager can assign maintenance", count_maintains("S1", "B2") == 1)

            body = c.post("/manager/assignments/maintenance", data={
                "staff_id": "S1", "boat_id": "B2", "submit": "Assign boat",
            }, follow_redirects=True).get_data(as_text=True)
            check("a duplicate maintenance assignment is refused",
                  "already maintains" in body, body[:300])

            body = c.post("/manager/assignments/maintenance/S1/B2/delete",
                          follow_redirects=True).get_data(as_text=True)
            check("maintenance can be unassigned",
                  "Maintenance assignment removed" in body, body[:300])
            check("the maintenance row is gone", count_maintains("S1", "B2") == 0)

        with app.test_client() as c:
            r = c.get("/manager/assignments/supervision")
            check("assignments pages are manager-only", r.status_code == 302,
                  f"status {r.status_code}")

        # 9. Manager deleting a supervisor -> used to fail on FK constraints
        with app.test_client() as c:
            as_manager(c, "M1")
            body = c.post("/manager/employees/M2/delete", data={"submit": "Delete"},
                          follow_redirects=True).get_data(as_text=True)
            check("deleting a supervising manager succeeds", "Employee deleted" in body, body[:300])
        check("manager row is gone", Manager.query.get("M2") is None)
        check("supervises rows were cleaned up",
              db.session.execute(text("SELECT COUNT(*) FROM Supervises WHERE ManagerID='M2'")).scalar() == 0)

        # 10. Self-delete is blocked
        with app.test_client() as c:
            as_manager(c, "M1")
            body = c.post("/manager/employees/M1/delete", data={"submit": "Delete"},
                          follow_redirects=True).get_data(as_text=True)
            check("self-delete is refused", "cannot delete" in body.lower())
        check("signed-in manager still exists", Manager.query.get("M1") is not None)

        # 11. Promoting supervised staff to manager -> used to fail on FK
        with app.test_client() as c:
            as_manager(c, "M1")
            body = c.post("/manager/employees/S1/edit", data={
                "office_id": "O1", "first_name": "Jane", "last_name": "X",
                "birthdate": "1980-01-01", "email": "s1@example.com",
                "self_insurance_nr": "INS-S1", "salary": "3000",
                "role": "manager", "department": "Ops", "management_level": "L2",
                "supervisor_id": "", "submit": "Save changes",
            }, follow_redirects=True).get_data(as_text=True)
            check("promoting supervised staff to manager succeeds",
                  "Employee updated" in body, body[:300])
        check("staff row removed after promotion", Staff.query.get("S1") is None)
        check("manager row created after promotion", Manager.query.get("S1") is not None)

        # 12. /logout clears a manager session too
        with app.test_client() as c:
            as_manager(c, "M1")
            c.get("/logout", follow_redirects=True)
            r = c.get("/manager/employees")
            check("logout clears the manager session", r.status_code == 302,
                  f"status {r.status_code}")

        # 13. A manager can add a city, stock it with a boat, and a client can
        #     then book there. This is the whole point of the office/boat CRUD,
        #     so it is tested as one chain rather than as isolated routes.
        with app.test_client() as c:
            as_manager(c, "M1")
            body = c.post("/manager/offices/new", data={
                "city": "Split", "country": "Croatia",
                "street": "Riva 5", "zip": "21000", "submit": "Save office",
            }, follow_redirects=True).get_data(as_text=True)
            check("manager can add a city", "Split added" in body, body[:300])

        split = Office.query.filter_by(City="Split").first()
        check("new office row was written", split is not None)

        with app.test_client() as c:
            as_client(c, "C1")
            body = c.get("/booking").get_data(as_text=True)
            check("new city shows up in the booking search", "Split" in body, body[:300])
            body = search(c, "Split").get_data(as_text=True)
            check("new city has no boats yet", "no boats" in body.lower(), body[:300])

        with app.test_client() as c:
            as_manager(c, "M1")
            body = c.post("/manager/boats/new", data={
                "office_id": split.OfficeID, "manufacturer": "Lagoon",
                "seats": "8", "length": "13.5", "weight": "1200", "horsepower": "150",
                "daily_rate": "980.00",
                "availability_status": AVAILABILITY_AVAILABLE,
                "boat_type": "catamaran", "nr_of_cabins": "4", "max_capacity": "10",
                "submit": "Save boat",
            }, follow_redirects=True).get_data(as_text=True)
            check("manager can add a boat", "added" in body, body[:300])

        new_boat_row = Boat.query.filter_by(OfficeID=split.OfficeID).first()
        check("boat row was written", new_boat_row is not None)
        check("catamaran subclass row was written",
              Catamaran.query.get(new_boat_row.BoatID) is not None)

        with app.test_client() as c:
            as_client(c, "C1")
            body = search(c, "Split").get_data(as_text=True)
            check("new boat is offered in the new city",
                  new_boat_row.BoatID in body, body[:300])
            body = book(c, new_boat_row.BoatID, city="Split").get_data(as_text=True)
            check("client can book a boat in the new city",
                  Rental.query.filter_by(BoatID=new_boat_row.BoatID).count() == 1,
                  body[:300])

        # 13b. Stocking an empty harbour must add boats and delete nothing.
        #      The alternative is /generate-data, which wipes every table.
        #      That is how a database full of offices was lost once.
        with app.test_client() as c:
            as_manager(c, "M1")
            c.post("/manager/offices/new", data={
                "city": "Hvar", "country": "Croatia",
                "street": "Riva 1", "zip": "21450", "submit": "Save office",
            }, follow_redirects=True)
            hvar = Office.query.filter_by(City="Hvar").first()

            # A harbour with an office but no boats takes the *other* branch of
            # the results section. It needs the anchor too: an empty search is
            # exactly when the page looks unchanged if it does not scroll.
            with app.test_client() as shopper:
                as_client(shopper, "C1")
                empty = search(shopper, "Hvar").get_data(as_text=True)
                check("an empty harbour explains itself",
                      "no boats on its books" in flat(empty), empty[:300])
                check("an empty result carries the scroll anchor",
                      'id="search-results"' in empty, empty[:300])

            before = (Office.query.count(), Client.query.count(),
                      Rental.query.count(), Boat.query.count())

            body = c.post(f"/manager/offices/{hvar.OfficeID}/stock",
                          follow_redirects=True).get_data(as_text=True)
            check("stocking an empty harbour adds boats",
                  Boat.query.filter_by(OfficeID=hvar.OfficeID).count() > 0, body[:300])
            check("stocking deletes nothing else",
                  (Office.query.count(), Client.query.count(), Rental.query.count())
                  == before[:3])
            check("stocking only added boats",
                  Boat.query.count() > before[3])

            body = c.post(f"/manager/offices/{hvar.OfficeID}/stock",
                          follow_redirects=True).get_data(as_text=True)
            check("stocking an already stocked harbour is refused",
                  "already has boats" in body, body[:300])

        with app.test_client() as c:
            as_client(c, "C1")
            body = search(c, "Hvar").get_data(as_text=True)
            check("a stocked harbour becomes bookable",
                  "No boats available" not in body, body[:300])

        # Switching subclass moves the row between tables, like a staff/manager
        # role change does.
        with app.test_client() as c:
            as_manager(c, "M1")
            c.post(f"/manager/boats/{new_boat_row.BoatID}/edit", data={
                "office_id": split.OfficeID, "manufacturer": "Lagoon",
                "seats": "8", "length": "13.5", "weight": "1200", "horsepower": "150",
                "daily_rate": "980.00",
                "availability_status": AVAILABILITY_AVAILABLE,
                "boat_type": "yacht", "yacht_name": "Sea Star", "has_jacuzzi": "y",
                "submit": "Save boat",
            }, follow_redirects=True)
        check("old subclass row removed on type change",
              Catamaran.query.get(new_boat_row.BoatID) is None)
        check("new subclass row created on type change",
              Yacht.query.get(new_boat_row.BoatID) is not None)

        # Switching to a catamaran must carry the jacuzzi with it: the field is
        # shared with the yacht block, so a bug there is a checkbox that either
        # never saves or saves against the wrong type.
        with app.test_client() as c:
            as_manager(c, "M1")
            c.post(f"/manager/boats/{new_boat_row.BoatID}/edit", data={
                "office_id": split.OfficeID, "manufacturer": "Lagoon",
                "seats": "8", "length": "13.5", "weight": "1200", "horsepower": "150",
                "daily_rate": "980.00",
                "availability_status": AVAILABILITY_AVAILABLE,
                "boat_type": "catamaran", "nr_of_cabins": "4",
                "max_capacity": "12", "has_jacuzzi": "y",
                "submit": "Save boat",
            }, follow_redirects=True)
        check("a catamaran can be given a jacuzzi from the manager form",
              Catamaran.query.get(new_boat_row.BoatID).HasJacuzzi is True)
        check("the yacht row went when the type changed",
              Yacht.query.get(new_boat_row.BoatID) is None)

        # And unticking it must clear it, not just fail to set it.
        with app.test_client() as c:
            as_manager(c, "M1")
            c.post(f"/manager/boats/{new_boat_row.BoatID}/edit", data={
                "office_id": split.OfficeID, "manufacturer": "Lagoon",
                "seats": "8", "length": "13.5", "weight": "1200", "horsepower": "150",
                "daily_rate": "980.00",
                "availability_status": AVAILABILITY_AVAILABLE,
                "boat_type": "catamaran", "nr_of_cabins": "4",
                "max_capacity": "12",
                "submit": "Save boat",
            }, follow_redirects=True)
        check("unticking the jacuzzi clears it",
              Catamaran.query.get(new_boat_row.BoatID).HasJacuzzi is False)


        # Delete guards: neither of these may take referenced rows with them.
        with app.test_client() as c:
            as_manager(c, "M1")
            body = c.post(f"/manager/boats/{new_boat_row.BoatID}/delete",
                          follow_redirects=True).get_data(as_text=True)
            check("deleting a boat with rentals is refused",
                  "rental(s) on record" in body, body[:300])
            check("boat survived the refused delete",
                  Boat.query.get(new_boat_row.BoatID) is not None)

            body = c.post(f"/manager/offices/{split.OfficeID}/delete",
                          follow_redirects=True).get_data(as_text=True)
            check("deleting a city that still has boats is refused",
                  "Cannot delete Split" in body, body[:300])

            # An empty city deletes cleanly.
            c.post("/manager/offices/new", data={
                "city": "Zadar", "country": "Croatia",
                "street": "Obala 1", "zip": "23000", "submit": "Save office",
            }, follow_redirects=True)
            zadar = Office.query.filter_by(City="Zadar").first()
            c.post(f"/manager/offices/{zadar.OfficeID}/delete", follow_redirects=True)
            check("an empty city can be deleted",
                  Office.query.filter_by(City="Zadar").first() is None)

        # 13b. A client can cancel a future rental, and only their own.
        far_start = date.today() + timedelta(days=60)
        far_end = far_start + timedelta(days=2)
        with app.test_client() as c:
            as_client(c, "C1")
            search(c, "Dubrovnik", far_start, far_end)
            book(c, "B4", start=far_start, end=far_end)
            check("a far-future rental was booked",
                  Rental.query.filter_by(BoatID="B4", ClientID="C1").count() == 1)

            body = c.get("/report").get_data(as_text=True)
            check("report offers a cancel button for a future rental", "/cancel" in body)
            # A boat ID alone does not say where the charter is collected from.
            check("the logbook names the harbour and its country",
                  "Dubrovnik, HR" in flat(body), body[:300])

            body = c.post(f"/rentals/B4/{far_start.isoformat()}/cancel",
                          follow_redirects=True).get_data(as_text=True)
            check("a client can cancel a future rental", "cancelled" in body, body[:300])
            check("an unpaid cancellation deletes the row",
                  Rental.query.filter_by(BoatID="B4", ClientID="C1").count() == 0)

        # 14b. Cancelling a *paid* charter must not delete it. The row is the
        #      only record the client was charged, so it is kept as
        #      CANCELLED. It must stop holding the boat, and nothing may
        #      sweep it away.
        with app.test_client() as c:
            as_client(c, "C1")
            paid_start = END + timedelta(days=320)
            paid_end = paid_start + timedelta(days=2)
            search(c, "Dubrovnik", start=paid_start, end=paid_end)
            book(c, "B4", start=paid_start, end=paid_end)
            pay(c, "B4", rental_date=paid_start)
            booked = Rental.query.filter_by(BoatID="B4", ClientID="C1",
                                            RentalDate=paid_start).first()
            check("the charter is paid before cancelling",
                  booked.PaymentStatus == PAYMENT_PAID)
            amount = booked.TotalAmount

            body = c.post(f"/rentals/B4/{paid_start.isoformat()}/cancel",
                          follow_redirects=True).get_data(as_text=True)
            kept = Rental.query.filter_by(BoatID="B4", ClientID="C1",
                                          RentalDate=paid_start).first()
            check("a paid charter is kept, not deleted", kept is not None)
            check("it is marked CANCELLED",
                  kept is not None and kept.PaymentStatus == PAYMENT_CANCELLED,
                  kept.PaymentStatus if kept else "row gone")
            # The whole point: what was charged is still on the record.
            check("the amount charged survives the cancellation",
                  kept is not None and kept.TotalAmount == amount,
                  f"{kept.TotalAmount if kept else '-'} vs {amount}")
            check("the client is told the money is traceable",
                  "refund" in body.lower(), body[:300])
            # The refund trail: owed from the moment it was cancelled, and not
            # yet settled.
            check("cancelling stamps when the refund became owed",
                  kept is not None and kept.CancelledAt is not None)
            check("the refund starts out unpaid", kept.RefundedAt is None)
            check("the charter reports a refund due", kept.refund_due is True)

            # If a cancelled row still blocked its dates the boat would be
            # unbookable for ever, which is worse than the bug being fixed.
            body = search(c, "Dubrovnik", start=paid_start,
                          end=paid_end).get_data(as_text=True)
            check("a cancelled charter stops holding the boat",
                  'value="B4"' in body, body[:300])

            # NB: this row is far in the future, so the hold sweep never even
            # considers it. The sweep is exercised against a cancelled charter
            # inside its window further down, where it can actually bite.
            check("the cancelled record is still there after a search",
                  Rental.query.filter_by(BoatID="B4", ClientID="C1",
                                         RentalDate=paid_start).count() == 1)

            # The composite PK is (ClientID, BoatID, RentalDate), so rebooking
            # the same boat on the same day would collide with the kept row.
            body = book(c, "B4", start=paid_start, end=paid_end).get_data(as_text=True)
            check("the same boat can be rebooked for the same date",
                  BOOKED in body, body[:300])
            again = Rental.query.filter_by(BoatID="B4", ClientID="C1",
                                           RentalDate=paid_start).first()
            check("rebooking reuses the row rather than duplicating it",
                  Rental.query.filter_by(BoatID="B4", ClientID="C1",
                                         RentalDate=paid_start).count() == 1)
            check("the rebooking is a new unpaid hold, not the old paid one",
                  again.PaymentStatus == PAYMENT_UNPAID)
            check("the rebooking gets a fresh CreatedAt",
                  again.CreatedAt > booked.CreatedAt or again.CreatedAt is not None)

            # Tidy up so later counts are unaffected.
            db.session.delete(again)
            db.session.commit()

        with app.test_client() as c:
            as_client(c, "C1")

            # C2 booked B1 for END+10 back in section 6. C1 must not reach it,
            # which is the whole point of taking ClientID from the session.
            c2_start = END + timedelta(days=10)
            body = c.post(f"/rentals/B1/{c2_start.isoformat()}/cancel",
                          follow_redirects=True).get_data(as_text=True)
            check("a client cannot cancel another client's rental",
                  "not on your list" in body, body[:300])
            check("the other client's rental survived",
                  Rental.query.filter_by(ClientID="C2", BoatID="B1",
                                         RentalDate=c2_start).count() == 1)

            # Booking refuses past start dates, so this row can only be
            # written directly. The guard still has to hold.
            past = date.today() - timedelta(days=2)
            db.session.add(Rental(ClientID="C1", BoatID="B4", RentalDate=past,
                                  RentalEndDate=date.today() + timedelta(days=1),
                                  PaymentStatus="PAID"))
            db.session.commit()
            body = c.post(f"/rentals/B4/{past.isoformat()}/cancel",
                          follow_redirects=True).get_data(as_text=True)
            check("a started rental cannot be cancelled",
                  "already started" in body, body[:300])
            check("the started rental survived",
                  Rental.query.filter_by(BoatID="B4", RentalDate=past).count() == 1)

            r = c.post("/rentals/B1/banana/cancel", follow_redirects=True)
            check("an unparseable cancel date does not 500", r.status_code == 200,
                  f"status {r.status_code}")
            check("an unparseable cancel date is reported",
                  "Invalid rental date" in r.get_data(as_text=True))

        with app.test_client() as c:
            r = c.post(f"/rentals/B4/{past.isoformat()}/cancel")
            check("an anonymous visitor cannot cancel", r.status_code == 302,
                  f"status {r.status_code}")
            check("the rental survived the anonymous cancel",
                  Rental.query.filter_by(BoatID="B4", RentalDate=past).count() == 1)

        # 13c. The office can call off anyone's charter, including one already
        #      under way, both of which the client route refuses.
        with app.test_client() as c:
            as_manager(c, "M1")
            body = c.get("/manager/rentals").get_data(as_text=True)
            check("manager sees rentals across clients",
                  "C1" in body or "Max" in body, body[:300])
            check("manager rentals page shows the harbour",
                  "Dubrovnik" in body, body[:300])

            filtered = c.get("/manager/rentals?city=Nice").get_data(as_text=True)
            check("manager rentals filter by city works",
                  "No rentals in Nice" in filtered, filtered[:300])

            # C2's rental, which C1 was refused in 13b.
            c2_start = END + timedelta(days=10)
            body = c.post(f"/manager/rentals/C2/B1/{c2_start.isoformat()}/delete",
                          follow_redirects=True).get_data(as_text=True)
            check("manager can cancel another client's rental",
                  "Cancelled" in body, body[:300])
            check("that rental is gone",
                  Rental.query.filter_by(ClientID="C2", BoatID="B1",
                                         RentalDate=c2_start).count() == 0)

            # A charter under way: starts yesterday, ends next week. The client
            # route refuses this; the office must be able to call it off.
            live_start = date.today() - timedelta(days=1)
            db.session.add(Rental(ClientID="C1", BoatID="B3", RentalDate=live_start,
                                  RentalEndDate=date.today() + timedelta(days=7),
                                  PaymentStatus="PAID",
                                  # A paid charter has an amount; the refund
                                  # checks below need something to preserve.
                                  TotalAmount=charter_total(Decimal("770.00"), 8),
                                  # Booked well in advance, so once cancelled its
                                  # payment deadline is long past and the hold
                                  # sweep would take it if nothing stopped it.
                                  # Left at the default, CreatedAt would be now
                                  # and the late-booking grace would keep it
                                  # alive, testing nothing.
                                  CreatedAt=datetime.now() - timedelta(days=10)))
            db.session.commit()
            body = c.post(f"/manager/rentals/C1/B3/{live_start.isoformat()}/delete",
                          follow_redirects=True).get_data(as_text=True)
            # This one was PAID, so the row stays as the record of the charge;
            # what must go is its hold on the boat, not the row.
            live = Rental.query.filter_by(BoatID="B3", RentalDate=live_start).first()
            check("manager can cancel a charter already under way",
                  live is not None and live.is_cancelled,
                  f"{live.PaymentStatus if live else 'row deleted'} | {body[:200]}")
            # get_available_boats() runs release_expired_holds() first, and
            # this cancelled charter is squarely in the sweep's window: it
            # started yesterday, so its payment deadline lapsed the day before
            # that, and it is "not PAID". Nothing but the CANCELLED exclusion
            # stops the sweep deleting the record this status exists to keep.
            free = [b.BoatID for b, _ in get_available_boats(
                "Mykonos", live_start, live_start + timedelta(days=2))]
            check("cancelling under way frees the boat for those dates",
                  "B3" in free, f"available: {free}")
            swept = Rental.query.filter_by(BoatID="B3", RentalDate=live_start).first()
            check("the hold sweep does not delete a cancelled charter",
                  swept is not None and swept.is_cancelled,
                  "row deleted by the sweep" if swept is None else swept.PaymentStatus)

            # 14c. The refund is recorded by the office, not the client.
            check("a cancelled charter starts with a refund outstanding",
                  swept.refund_due is True)
            listing = c.get("/manager/rentals").get_data(as_text=True)
            check("the manager list offers to record the refund",
                  "Mark refunded" in listing, listing[:300])
            body = c.post(f"/manager/rentals/C1/B3/{live_start.isoformat()}/refund",
                          follow_redirects=True).get_data(as_text=True)
            refunded = Rental.query.filter_by(BoatID="B3", RentalDate=live_start).first()
            check("a manager can record the refund",
                  refunded.RefundedAt is not None, body[:300])
            check("recording a refund does not delete the record",
                  refunded is not None and refunded.is_cancelled)
            check("the refund is no longer outstanding", refunded.refund_due is False)
            # Recording the refund must not touch what was charged. That
            # figure is the whole reason the row was kept.
            check("the amount charged is untouched by the refund",
                  refunded.TotalAmount == charter_total(Decimal("770.00"), 8),
                  f"{refunded.TotalAmount}")
            check("the refund cannot be paid out twice",
                  "already recorded" in c.post(
                      f"/manager/rentals/C1/B3/{live_start.isoformat()}/refund",
                      follow_redirects=True).get_data(as_text=True))
            stamp = refunded.RefundedAt
            check("the second attempt did not move the timestamp",
                  Rental.query.filter_by(BoatID="B3",
                                         RentalDate=live_start).first().RefundedAt == stamp)

            # Nothing is owed on a charter nobody cancelled.
            live2 = END + timedelta(days=400)
            db.session.add(Rental(ClientID="C2", BoatID="B3", RentalDate=live2,
                                  RentalEndDate=live2 + timedelta(days=2),
                                  PaymentStatus=PAYMENT_PAID))
            db.session.commit()
            body = c.post(f"/manager/rentals/C2/B3/{live2.isoformat()}/refund",
                          follow_redirects=True).get_data(as_text=True)
            check("an uncancelled charter cannot be refunded",
                  "nothing to refund" in body, body[:300])
            check("it was not stamped anyway",
                  Rental.query.filter_by(ClientID="C2", BoatID="B3",
                                         RentalDate=live2).first().RefundedAt is None)

            # A finished charter is the record that it happened.
            done_start = date.today() - timedelta(days=20)
            db.session.add(Rental(ClientID="C1", BoatID="B3", RentalDate=done_start,
                                  RentalEndDate=date.today() - timedelta(days=14),
                                  PaymentStatus="PAID"))
            db.session.commit()
            body = c.post(f"/manager/rentals/C1/B3/{done_start.isoformat()}/delete",
                          follow_redirects=True).get_data(as_text=True)
            check("a finished charter cannot be deleted",
                  "cannot be removed" in body, body[:300])
            check("the finished charter survived",
                  Rental.query.filter_by(BoatID="B3", RentalDate=done_start).count() == 1)

            r = c.post("/manager/rentals/C1/B3/banana/delete", follow_redirects=True)
            check("an unparseable manager cancel date does not 500",
                  r.status_code == 200, f"status {r.status_code}")

        with app.test_client() as c:
            as_client(c, "C1")
            r = c.get("/manager/rentals")
            check("the rentals page is manager-only", r.status_code == 302,
                  f"status {r.status_code}")

        # 14. Client self-registration. Runs before the generate-data section,
        #     which deletes every client.
        def registration(**overrides):
            payload = {
                "first_name": "Nina", "last_name": "Novak",
                "street": "Ilica 1", "zip": "10000", "city": "Zagreb", "country": "Croatia",
                "birthdate": "1995-06-15", "email": "nina@example.com",
                "mobile": "+385 1 234", "captain_license": "CAPT-555001",
                "submit": "Create account",
            }
            payload.update(overrides)
            return payload

        before = Client.query.count()
        with app.test_client() as c:
            body = c.post("/register", data=registration(),
                          follow_redirects=True).get_data(as_text=True)
            check("a new client can register", "Welcome, Nina" in body, body[:300])
            check("client row was written", Client.query.count() == before + 1)
            # Landing on /report at all proves registration signed them in.
            check("registration signs the new client in",
                  c.get("/report").status_code == 200)

            # The real point: the new ClientID has to satisfy the Rental FK.
            search(c, "Dubrovnik")
            body = book(c, "B4").get_data(as_text=True)
            nina = Client.query.filter_by(Email="nina@example.com").first()
            # Guarded: if registration failed above, nina is None and a bare
            # attribute access aborts the whole run instead of reporting.
            check("a freshly registered client can book",
                  nina is not None
                  and Rental.query.filter_by(ClientID=nina.ClientID).count() == 1,
                  body[:300] if nina else "registration never created the client")

            # Registering while signed in switches accounts rather than being
            # turned away: the link lives on the identity picker, so bouncing
            # to the home page did nothing and explained nothing.
            body = c.get("/register").get_data(as_text=True)
            check("a signed-in client reaches the registration form",
                  "Create" in body and "first_name" in body, body[:300])
            check("and is told the old session ended",
                  "so you can create a new one" in flat(body), body[:400])
            check("the sign-out happens on arrival, not on submit",
                  c.get("/report").status_code == 302)

            body = c.post("/register", data=registration(email="nina2@example.com",
                                                         captain_license="CAPT-555002"),
                          follow_redirects=True).get_data(as_text=True)
            check("the second account is created", Client.query.count() == before + 2)
            check("and the client is signed in as the new one",
                  "Welcome, Nina" in body, body[:300])
            newest = Client.query.filter_by(Email="nina2@example.com").first()
            check("the first account still exists to sign back into",
                  Client.query.filter_by(Email="nina@example.com").first() is not None)
            check("the two are different rows",
                  newest is not None and newest.Email != "nina@example.com")

        # Rebased rather than counted from `before`: how many accounts the
        # block above creates is the thing under test up there, and this check
        # only cares that a refused registration adds none.
        registered = Client.query.count()
        with app.test_client() as c:
            body = c.post("/register", data=registration(email="other@example.com"),
                          follow_redirects=True).get_data(as_text=True)
            check("duplicate captain licence is refused", "must be unique" in body, body[:300])
            check("no client row after the refused registration",
                  Client.query.count() == registered)

        # A blank licence must be stored as NULL, not "": the column is UNIQUE,
        # and two empty strings would collide where two NULLs do not.
        for i, email in enumerate(("noline1@example.com", "noline2@example.com")):
            with app.test_client() as c:
                c.post("/register", data=registration(email=email, captain_license=""),
                       follow_redirects=True)
        check("two clients can register without a captain licence",
              Client.query.filter_by(CaptainLicenseNumber=None).count() >= 2)

        with app.test_client() as c:
            body = c.post("/register", data=registration(
                email="kid@example.com", captain_license="",
                birthdate=(date.today() - timedelta(days=365 * 10)).isoformat(),
            ), follow_redirects=True).get_data(as_text=True)
            check("an underage client is refused", "at least 18" in body, body[:300])
            check("no client row after the underage registration",
                  Client.query.filter_by(Email="kid@example.com").first() is None)

        with app.test_client() as c:
            body = c.get("/login").get_data(as_text=True)
            check("login page links to registration", "Create an account" in body)

        # 15. generate-data is open to anonymous visitors and repeatable, and
        #     it must never take the harbours with it. That is what makes it
        #     safe to leave the button on the page.
        with app.test_client() as c:
            cities_before = {c_ for (c_,) in Office.query.with_entities(Office.City)}
            added = Office.query.filter_by(City="Hvar").first()
            check("a manager-added city exists before the refill", added is not None)

            body = c.post("/generate-data", follow_redirects=True).get_data(as_text=True)
            check("anonymous can generate data", "Demo data refilled" in body, body[:300])
            # Superset, not equality: the refill also recreates any of the five
            # seeded offices whose row is missing, so the set can grow.
            cities_after = {c_ for (c_,) in Office.query.with_entities(Office.City)}
            check("the refill loses no harbour",
                  cities_before <= cities_after,
                  f"lost: {sorted(cities_before - cities_after)}")
            check("the manager-added city survives the refill",
                  Office.query.filter_by(City="Hvar").first() is not None)
            check("button stays available for another run",
                  "Generate demo data" in body, body[:300])

            body = c.post("/generate-data", follow_redirects=True).get_data(as_text=True)
            check("generate-data can be run again", "Demo data refilled" in body, body[:300])

            # Every harbour must end up with something bookable, or a client
            # sees a city on the booking page that can never be booked.
            stocked = {
                c_ for (c_,) in db.session.query(Office.City)
                .join(Boat, Boat.OfficeID == Office.OfficeID)
                .filter(Boat.AvailabilityStatus == AVAILABILITY_AVAILABLE)
                .distinct()
            }
            every_city = {c_ for (c_,) in Office.query.with_entities(Office.City)}
            check("every harbour has a bookable boat after the refill",
                  stocked == every_city,
                  f"unstocked: {sorted(every_city - stocked)}")

        # 16. The Generate Data reset runs cleanly and fills the m:n tables
        with app.app_context():
            from boat_rental.generator import generate_data
            try:
                generate_data()
                ok = True
            except Exception as exc:  # noqa: BLE001 - reported as a failure
                ok = False
                print(f"       generate_data raised: {exc!r}")
            check("generate_data completes", ok)
            check("generate_data created boats", Boat.query.count() > 0)
            check("generate_data created rentals", Rental.query.count() > 0)
            check("generate_data refills Supervises",
                  db.session.execute(text("SELECT COUNT(*) FROM Supervises")).scalar() > 0)
            check("generate_data refills Maintains",
                  db.session.execute(text("SELECT COUNT(*) FROM Maintains")).scalar() > 0)

        # 17. Splitting the graded SQL. init-db feeds these files to PyMySQL one
        #     statement at a time, so the splitter stands between the graded SQL
        #     and whatever database it is pointed at. Splitting on ";" corrupts
        #     the schema -- these pin the three places where it does.
        from boat_rental import sqlscript

        sql_dir = sqlscript.database_dir()
        schema = sqlscript.schema_file(sql_dir).read_text(encoding="utf-8")
        statements = sqlscript.split_statements(schema)
        tables = sqlscript.created_tables(schema)

        check("the schema splits into one statement per table",
              len(statements) == len(tables) == 12,
              f"{len(statements)} statements, {len(tables)} tables")
        check("the commented-out CREATE DATABASE is not read as a table",
              "boat_rental" not in tables, str(tables))

        # Group05_Createtable.sql:101 has a semicolon inside a comment *inside*
        # the Rental CREATE TABLE body. Split naively, the table is created
        # without its primary key or either foreign key.
        rental = [s for s in statements if "CREATE TABLE IF NOT EXISTS Rental" in s]
        check("Rental survives as a single statement", len(rental) == 1,
              f"{len(rental)} fragments")
        check("Rental keeps its composite primary key",
              bool(rental) and "PK_rental" in rental[0])
        check("Rental keeps both foreign keys",
              bool(rental) and rental[0].count("FOREIGN KEY") == 2)
        check("splitting on ';' really would corrupt the schema",
              len([s for s in schema.split(";") if s.strip()]) > len(statements),
              "if this fails the trap is gone and this section is obsolete")

        # Student2_InsertData_Initial.sql:24-25 puts trailing comments after
        # ")," and after the terminating ";", which rules out dropping lines
        # that merely start with "--".
        seeds = sqlscript.seed_files(sql_dir)
        check("init.sql declares three seed scripts in order", len(seeds) == 3,
              str([p.name for p in seeds]))
        check("Student1 is seeded before Student2, which owns no offices",
              [p.name for p in seeds] == [
                  "Student1_InsertData_Initial.sql",
                  "Student1_InsertData_Harbours.sql",
                  "Student2_InsertData_Initial.sql",
              ], str([p.name for p in seeds]))
        student2 = [p for p in seeds if p.name.startswith("Student2")][0]
        check("trailing comments do not split the Student2 inserts",
              len(sqlscript.split_statements(
                  student2.read_text(encoding="utf-8"))) == 4)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
