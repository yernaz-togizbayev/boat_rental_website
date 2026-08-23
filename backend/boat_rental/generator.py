"""Demo data for the /generate-data button and the "stock this harbour" action.

generate_data() clears and refills everything except Office, then rebuilds the
fleet, the people and the rentals. stock_office() is the non-destructive
version for a single city.

build_boat() is the one place a boat is made physically coherent: length is
drawn per type, and seats, power, weight, cabins and engine layout follow from
it. Bypass it and you get a seven-metre yacht that sleeps five.
"""

import random
from collections import defaultdict
from datetime import datetime, timedelta, date
from decimal import Decimal
from uuid import uuid4

from flask import current_app
from sqlalchemy import text

from boat_rental import db

from boat_rental.models import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_MAINTENANCE,
    CENTS,
    DEFAULT_START_TIME,
    PAYMENT_CANCELLED,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    charter_total,
    Office,
    Client,
    Boat,
    Rental,
    Yacht,
    Motorboat,
    Catamaran,
    Employee,
    Staff,
    Manager
)


SEED_OFFICES = [
    ("O1", "Tourlos Marina", "Greece", "Mykonos", "84600"),
    ("O2", "Quai des Docks", "France", "Nice", "06000"),
    ("O3", "Moll de la Barceloneta, 1", "Spain", "Barcelona", "08039"),
    ("O4", "Old Port of Fira", "Greece", "Santorini", "84700"),
    ("O5", "Obala Stjepana Radića, 2", "Croatia", "Dubrovnik", "20000"),
]


def generate_data():
    """Refill the demo data, keeping the harbours.

    Offices are the one table this does not wipe. A manager can open an office
    in any city, so clearing them would make the only fleet-restocking tool
    also destroy every city anyone had added. The five seeded offices come back
    only if their row is missing, which leaves an edited address alone, and the
    new boats spread over every office that exists rather than those five.

    One transaction: the do_* helpers flush and never commit, or a failure
    halfway through leaves the database wiped and empty. A new table means a
    new line in the delete sequence, in the right place for its foreign keys.
    """
    try:
        db.session.execute(text("DELETE FROM `Maintains`"))
        db.session.execute(text("DELETE FROM `Supervises`"))


        db.session.query(Rental).delete()
        db.session.query(Staff).delete()

        db.session.execute(text("UPDATE `Manager` SET `SupervisorID` = NULL"))

        db.session.query(Manager).delete()
        db.session.query(Employee).delete()

        db.session.query(Yacht).delete()
        db.session.query(Motorboat).delete()
        db.session.query(Catamaran).delete()
        db.session.query(Boat).delete()
        db.session.query(Client).delete()

        existing = {o.OfficeID for o in Office.query.with_entities(Office.OfficeID)}
        for office_id, street, country, city, zip_code in SEED_OFFICES:
            if office_id not in existing:
                db.session.add(Office(
                    OfficeID=office_id, Street=street,
                    Country=country, City=city, ZIP=zip_code,
                ))

        db.session.flush()
        offices = Office.query.all()

        do_employees(offices)
        do_clients()
        do_boats(offices)
        do_rentals()
        do_assignments()

        db.session.commit()

    except Exception:
        db.session.rollback()
        current_app.logger.exception("generate_data failed")
        raise


def do_clients():
    first_names = [
        "Max",
        "Olga",
        "Fabio",
        "Anna",
        "John",
        "Sarah",
        "Mike",
        "Lisa",
        "Tom",
        "Emma",
    ]
    last_names = [
        "Mustermann",
        "Primerova",
        "Exemplario",
        "Schmidt",
        "Doe",
        "Johnson",
        "Brown",
        "Wilson",
    ]
    countries = ["Austria", "Germany", "Italy", "France", "Spain"]
    cities = ["Vienna", "Berlin", "Rome", "Paris", "Madrid"]

    for i in range(10):
        client = Client(
            ClientID=f"C{i + 1}",
            FirstName=random.choice(first_names),
            LastName=random.choice(last_names),
            Street=f"Test Street {i + 1}",
            ZIP=f"{random.randint(10000, 99999)}",
            Country=random.choice(countries),
            City=random.choice(cities),
            Birthdate=date(
                random.randint(1970, 2000),
                random.randint(1, 12),
                random.randint(1, 28),
            ),
            Email=f"client{i}@example.com",
            MobileNumber=f"+{random.randint(1, 74)}-555-{random.randint(100, 999)}-{random.randint(1000, 9999)}",
            CaptainLicenseNumber=f"CAPT-{random.randint(100000, 999999)}",
        )
        db.session.add(client)


BOAT_BUILDERS = {
    "yacht": ["Azimut", "Sunseeker", "Ferretti", "Princess", "Benetti"],
    "motorboat": ["Bayliner", "Sea Ray", "Quicksilver", "Boston Whaler", "Jeanneau"],
    "catamaran": ["Lagoon", "Fountaine Pajot", "Bali", "Leopard"],
}

BOAT_LENGTHS_M = {"motorboat": (5, 12), "catamaran": (11, 18), "yacht": (18, 40)}

YACHT_NAMES = [
    "Serenity", "Blue Horizon", "Aurora", "Sirocco", "Meridian", "Halcyon",
    "Vela", "Corallia", "Odyssey", "Nautilus", "Thalassa", "Zephyr",
]


def boat_figures(kind, length):
    """(seats, horsepower, weight, daily_rate) suiting this type and length.

    The rate is per metre per day, which is roughly how charter pricing works:
    a 6 m motorboat lands near EUR 350 a day and a 40 m yacht near EUR 5,000.
    """
    if kind == "motorboat":
        seats = round(length * 0.8) + random.randint(0, 2)
        horsepower = int(length * random.uniform(25, 45))
        rate_per_metre = random.uniform(40, 70)
    elif kind == "catamaran":
        seats = round(length * 0.55) + random.randint(0, 2)
        horsepower = int(length * random.uniform(6, 12))
        rate_per_metre = random.uniform(55, 90)
    else:
        seats = round(length * 0.35) + random.randint(0, 2)
        horsepower = int(length * random.uniform(45, 80))
        rate_per_metre = random.uniform(90, 160)

    weight = round(length ** 3 * random.uniform(0.55, 0.95), 1)
    # Rounded to the nearest 10. Nobody quotes a charter at EUR 1,337.42.
    daily_rate = Decimal(round(length * rate_per_metre, -1)).quantize(CENTS)
    return max(2, seats), horsepower, weight, daily_rate


def build_boat(boat_id, office_id, availability=AVAILABILITY_AVAILABLE):
    """One coherent boat plus its subclass row. Adds both; does not commit."""
    kind = random.choice(list(BOAT_BUILDERS))
    low, high = BOAT_LENGTHS_M[kind]
    length = round(random.uniform(low, high), 1)
    seats, horsepower, weight, daily_rate = boat_figures(kind, length)

    boat = Boat(
        BoatID=boat_id,
        OfficeID=office_id,
        Length=length,
        Seats=seats,
        Manufacturer=random.choice(BOAT_BUILDERS[kind]),
        AvailabilityStatus=availability,
        Weight=weight,
        Horsepower=horsepower,
        DailyRate=daily_rate,
    )
    db.session.add(boat)

    if kind == "yacht":
        concrete_boat = Yacht(
            YachtID=boat_id,
            YachtName=random.choice(YACHT_NAMES),
            HasJacuzzi=length >= 24 and random.random() < 0.6,
        )
    elif kind == "motorboat":
        concrete_boat = Motorboat(
            MotorboatID=boat_id,
            EngineType="Outboard" if length < 9 else "Inboard",
            FuelType=random.choice(["Diesel", "Petrol"]),
        )
    else:
        concrete_boat = Catamaran(
            CatamaranID=boat_id,
            NrOfCabins=max(2, round(length / 3.5)),
            MaxCapacity=seats + random.randint(4, 10),
            # The yacht rule scaled to a hull half the size: deck space for
            # one starts near the top of the 11-18 m range, and plenty of big
            # catamarans still go without.
            HasJacuzzi=length >= 14 and random.random() < 0.4,
        )

    db.session.add(concrete_boat)
    return boat


def stock_office(office_id, count=6):
    """Give one office a small fleet, deleting nothing.

    The non-destructive counterpart to generate_data(). Reach for this when a
    single city needs boats.
    """
    return [build_boat(f"B{uuid4().hex[:8]}", office_id) for _ in range(count)]


MAINTENANCE_SHARE = 0.12


def do_boats(offices):
    """200 boats spread over every office, each guaranteed one bookable boat.

    The first pass round the offices is forced available, so no harbour ends up
    listed but empty or maintenance-only. A client can see those and cannot
    book in them.
    """
    for i in range(200):
        first_round = i < len(offices)
        under_maintenance = not first_round and random.random() < MAINTENANCE_SHARE
        build_boat(
            f"B{i + 1}",
            offices[i % len(offices)].OfficeID,
            AVAILABILITY_MAINTENANCE if under_maintenance else AVAILABILITY_AVAILABLE,
        )


def do_rentals():
    db.session.flush()
    clients = Client.query.all()
    boats = Boat.query.filter_by(AvailabilityStatus=AVAILABILITY_AVAILABLE).all()

    if not clients or not boats:
        current_app.logger.warning(
            "Skipping rental generation: %d clients, %d available boats",
            len(clients),
            len(boats),
        )
        return


    now = datetime.now()
    booked_windows = defaultdict(list)
    for _ in range(400):
        client_id = random.choice(clients).ClientID
        boat = random.choice(boats)
        rental_date = date.today() + timedelta(days=random.randint(-30, 30))
        end_date = rental_date + timedelta(days=random.randint(1, 10))

        if any(rental_date < seen_end and end_date > seen_start
               for seen_start, seen_end in booked_windows[boat.BoatID]):
            continue
        booked_windows[boat.BoatID].append((rental_date, end_date))

        status = random.choices(
            [PAYMENT_PAID, PAYMENT_UNPAID, "PENDING", PAYMENT_CANCELLED],
            weights=[72, 12, 8, 8],
        )[0]
        created_at = (datetime.combine(rental_date, DEFAULT_START_TIME)
                      - timedelta(days=random.randint(2, 40)))

        # A cancelled charter carries the refund trail, and the stamps have to
        # come in order: booked, then called off, then paid back, none of it in
        # the future. A booking for next month can have been created tomorrow,
        # so for these the creation date is pulled into the past first or the
        # cancellation would predate the booking.
        cancelled_at = refunded_at = None
        if status == PAYMENT_CANCELLED:
            created_at = min(created_at, now - timedelta(days=2))
            cancelled_at = min(created_at + timedelta(hours=random.randint(6, 240)), now)
            # Leave some unrefunded. An outstanding refund is the state the
            # manager page exists to act on.
            if random.random() < 0.6:
                refunded_at = min(cancelled_at + timedelta(hours=random.randint(12, 168)),
                                  now)

        db.session.add(
            Rental(
                ClientID=client_id,
                BoatID=boat.BoatID,
                RentalDate=rental_date,
                RentalEndDate=end_date,
                PaymentStatus=status,
                TotalAmount=charter_total(boat.DailyRate, (end_date - rental_date).days),
                StartTime=DEFAULT_START_TIME,
                CreatedAt=created_at,
                CancelledAt=cancelled_at,
                RefundedAt=refunded_at,
            )
        )


def do_employees(offices):
    first_names = [
        "Alex",
        "Jamie",
        "Taylor",
        "Jordan", 
        "Casey",
        "Riley",
        "Sam",
        "Morgan",
        "Avery",
        "Quinn"
    ]

    last_names = [
        "Smith",
        "Johnson",
        "Brown",
        "Davis",
        "Miller",
        "Wilson",
        "Moore",
        "Taylor", 
        "Anderson",
        "Thomas"
    
    ]
    managers = []

    # staff
    for i in range(6):
        fn, ln = random.choice(first_names), random.choice(last_names)
        emp = Employee(
            EmployeeID=f"E{i+1}",
            OfficeID=random.choice(offices).OfficeID,
            FirstName=fn, LastName=ln,
            Street="Main Street 1", ZIP="1000", Country="Country", City="City",
            Birthdate=date(1990, random.randint(1, 12), random.randint(1, 28)),
            Email=f"{fn.lower()}.{ln.lower()}@example.com",
            MobileNumber=f"+1-555-{random.randint(100,999)}-{random.randint(1000,9999)}",
            SelfInsuranceNr=f"INS-{random.randint(1000000, 9999999)}",
            Salary=random.randint(32000, 65000)
        )
        db.session.add(emp)
        db.session.add(Staff(StaffID=emp.EmployeeID, WorkShift=random.choice(["Day","Night"]), IsOnDuty=random.choice([True, False])))

    # managers
    for i in range(3):
        fn, ln = random.choice(first_names), random.choice(last_names)
        idx = i + 7
        emp = Employee(
            EmployeeID=f"E{idx}",
            OfficeID=random.choice(offices).OfficeID,
            FirstName=fn, LastName=ln,
            Street="Harbor Road 2", ZIP="2000", Country="Country", City="City",
            Birthdate=date(1985, random.randint(1, 12), random.randint(1, 28)),
            Email=f"{fn.lower()}.{ln.lower()}@example.com",
            MobileNumber=f"+1-555-{random.randint(100,999)}-{random.randint(1000,9999)}",
            SelfInsuranceNr=f"INS-{random.randint(1000000, 9999999)}",
            Salary=random.randint(70000, 140000)
        )
        db.session.add(emp)
        m = Manager(ManagerID=emp.EmployeeID, Department=random.choice(["Sales","Ops","HR"]), ManagementLevel=random.choice(["L1","L2","L3"]), SupervisorID=None)
        db.session.add(m)
        managers.append(m)

    # simple supervisor chain
    if len(managers) >= 2:
        sup_id = managers[0].ManagerID
        for m in managers[1:]:
            m.SupervisorID = sup_id

    db.session.flush()


def do_assignments():
    """Populate the two m:n relations from the ER model.

    generate_data() wipes Supervises and Maintains but nothing used to refill
    them, so they stayed permanently empty after any reset. Neither table has
    a SQLAlchemy model, hence the raw SQL.
    """
    db.session.flush()
    staff_ids = [s.StaffID for s in Staff.query.all()]
    manager_ids = [m.ManagerID for m in Manager.query.all()]
    boat_ids = [b.BoatID for b in Boat.query.all()]

    if not (staff_ids and manager_ids and boat_ids):
        return

    for staff_id in staff_ids:
        db.session.execute(
            text(
                "INSERT INTO `Supervises` (`ManagerID`, `StaffID`) VALUES (:m, :s)"
            ),
            {"m": random.choice(manager_ids), "s": staff_id},
        )
        # random.sample yields distinct boats and each staff id is visited
        # once, so (StaffID, BoatID) pairs are unique by construction.
        for boat_id in random.sample(boat_ids, min(3, len(boat_ids))):
            db.session.execute(
                text("INSERT INTO `Maintains` (`StaffID`, `BoatID`) VALUES (:s, :b)"),
                {"s": staff_id, "b": boat_id},
            )
