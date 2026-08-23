"""SQLAlchemy models, hand-maintained to match database/Group05_Createtable.sql.

There is no Alembic here. The SQL files create the tables, not db.create_all(),
so a column added to one has to be added to the other by hand or the app breaks
at runtime with no migration error to explain why.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func

from boat_rental import db

# Boat.AvailabilityStatus values. Constants because the SQL seed data and the
# generator both write these exact strings, and comparing against a different
# casing only ever worked by accident under MariaDB's case-insensitive default
# collation.
AVAILABILITY_AVAILABLE = "Available"
AVAILABILITY_MAINTENANCE = "Maintenance"

# Rental.PaymentStatus values. PENDING is only ever written by the generator;
# the app moves a rental from UNPAID to PAID and never back.
PAYMENT_PAID = "PAID"
PAYMENT_UNPAID = "UNPAID"
# A charter that was paid for and then called off. The row survives because it
# is the only record that the client was charged, the same reason TotalAmount
# is frozen at booking instead of recomputed from DailyRate. It stops holding
# the boat because rental_overlap_filter() skips this status.
PAYMENT_CANCELLED = "CANCELLED"

CENTS = Decimal("0.01")

# A charter starts at the morning handover unless a rental says otherwise.
DEFAULT_START_TIME = time(9, 0)

# How far ahead of the ride payment is due, and the shorter window a booking
# gets when it is made too late for that.
ADVANCE_NOTICE = timedelta(hours=24)
LATE_BOOKING_GRACE = timedelta(minutes=15)


def ride_start(rental_date, start_time=None):
    """The instant the charter begins."""
    return datetime.combine(rental_date, start_time or DEFAULT_START_TIME)


def payment_deadline(rental_date, start_time=None, created_at=None):
    """When an unpaid booking stops holding its boat.

    Normally 24 hours before the ride. You cannot give 24 hours' notice that
    has already passed, so a booking made inside that window gets
    LATE_BOOKING_GRACE from when it was made instead: long enough to finish
    paying, short enough that nothing is held overnight for free. Whichever
    deadline is later wins, so booking early never shortens it.
    """
    deadline = ride_start(rental_date, start_time) - ADVANCE_NOTICE
    if created_at is not None and created_at > deadline:
        return created_at + LATE_BOOKING_GRACE
    return deadline


def booked_late(rental_date, start_time=None, created_at=None):
    """True if this booking was made too late for the usual 24 hours' notice."""
    if created_at is None:
        return False
    return created_at > ride_start(rental_date, start_time) - ADVANCE_NOTICE


def hold_expired(rental_date, start_time=None, created_at=None, now=None):
    """True once an unpaid booking no longer holds its boat."""
    return (now or datetime.now()) > payment_deadline(rental_date, start_time, created_at)


def charter_total(daily_rate, days):
    """Rate times nights as a Decimal, or None if the boat has no rate.

    The only place a charter is priced. Multiply a float rate by a day count
    anywhere else and you reintroduce the rounding error DECIMAL(10,2) exists
    to prevent.
    """
    if daily_rate is None or days <= 0:
        return None
    return (Decimal(daily_rate) * days).quantize(CENTS)


class Office(db.Model):
    __tablename__ = "Office"
    OfficeID = db.Column(db.String(50), primary_key=True)
    Street = db.Column(db.String(100), nullable=False)
    Country = db.Column(db.String(50), nullable=False)
    City = db.Column(db.String(50), nullable=False)
    ZIP = db.Column(db.String(10), nullable=False)

    boats = db.relationship("Boat", backref="office", lazy=True)
    employees = db.relationship("Employee", backref="office", lazy=True)


def served_harbours():
    """(city, country) for everywhere the fleet operates, alphabetically.

    There is no City table. A city exists because some Office row names it, so
    this is the only definition of where we operate, and the home page, the
    booking picker, the availability filter and the manager rentals filter all
    have to agree on it. It lives in models rather than routes because forms.py
    needs it too and cannot import routes without a circular import.

    Grouped by city so one with two offices still appears once. The country
    comes from min() rather than a join: everything downstream keys off the
    city, and listing it twice would mean a duplicate option, a duplicate card
    and an ambiguous filter. Two offices in the same city disagreeing about
    their country is a data-entry problem, not something to render.
    """
    return [(city, country) for city, country in
            Office.query.with_entities(Office.City, func.min(Office.Country))
                        .group_by(Office.City).order_by(Office.City)]


def served_cities():
    """Just the city names, for the places that key off them."""
    return [city for city, _country in served_harbours()]


class Client(db.Model):
    __tablename__ = "Client"
    ClientID = db.Column(db.String(50), primary_key=True)
    FirstName = db.Column(db.String(50), nullable=False)
    LastName = db.Column(db.String(50), nullable=False)
    Street = db.Column(db.String(100))
    ZIP = db.Column(db.String(10))
    Country = db.Column(db.String(50))
    City = db.Column(db.String(50))
    Birthdate = db.Column(db.Date, nullable=False)
    Email = db.Column(db.String(100), nullable=False)
    MobileNumber = db.Column(db.String(20))
    CaptainLicenseNumber = db.Column(db.String(50), unique=True)

    rentals = db.relationship("Rental", backref="client", lazy=True)

    @property
    def full_name(self):
        return f"{self.FirstName} {self.LastName}"


class Boat(db.Model):
    __tablename__ = "Boat"
    BoatID = db.Column(db.String(50), primary_key=True)
    OfficeID = db.Column(
        db.String(50), db.ForeignKey("Office.OfficeID"), nullable=False
    )
    Length = db.Column(db.Float)
    Seats = db.Column(db.Integer)
    Manufacturer = db.Column(db.String(50))
    AvailabilityStatus = db.Column(db.String(20))
    Weight = db.Column(db.Float)
    Horsepower = db.Column(db.Integer)
    DailyRate = db.Column(db.Numeric(10, 2))

    rentals = db.relationship("Rental", backref="boat", lazy=True)
    yacht = db.relationship("Yacht", backref="boat_ref", uselist=False)
    motorboat = db.relationship("Motorboat", backref="boat_ref", uselist=False)
    catamaran = db.relationship("Catamaran", backref="boat_ref", uselist=False)

    @property
    def jacuzzi(self):
        """True, False, or None for a boat that cannot have one.

        Yachts and catamarans carry the column; a motorboat has no subclass
        row at all. None keeps "not that kind of boat" distinct from "no
        jacuzzi", which are different answers. It lives here rather than in a
        template because the booking cards and the availability table both
        ask, and they have to agree on which hulls the question applies to.
        """
        row = self.yacht or self.catamaran
        return bool(row.HasJacuzzi) if row is not None else None


class Rental(db.Model):
    __tablename__ = "Rental"
    ClientID = db.Column(
        db.String(50), db.ForeignKey("Client.ClientID"), nullable=False
    )
    BoatID = db.Column(db.String(50), db.ForeignKey("Boat.BoatID"), nullable=False)
    RentalDate = db.Column(db.Date, nullable=False)
    RentalEndDate = db.Column(db.Date)
    PaymentStatus = db.Column(db.String(20))
    TotalAmount = db.Column(db.Numeric(10, 2))
    StartTime = db.Column(db.Time, default=DEFAULT_START_TIME)
    CreatedAt = db.Column(db.DateTime, default=datetime.now)
    # Set only on a paid charter that was called off: when the refund became
    # owed, and when it was handed back. Both NULL otherwise.
    CancelledAt = db.Column(db.DateTime)
    RefundedAt = db.Column(db.DateTime)

    __table_args__ = (db.PrimaryKeyConstraint("ClientID", "BoatID", "RentalDate"),)

    @property
    def rental_days(self):
        if self.RentalEndDate and self.RentalDate:
            return (self.RentalEndDate - self.RentalDate).days
        return 0

    @property
    def starts_at(self):
        return ride_start(self.RentalDate, self.StartTime)

    @property
    def pay_by(self):
        """The instant this booking stops holding its boat."""
        return payment_deadline(self.RentalDate, self.StartTime, self.CreatedAt)

    @property
    def is_late_booking(self):
        """Booked inside the 24-hour window, so held only briefly."""
        return booked_late(self.RentalDate, self.StartTime, self.CreatedAt)

    @property
    def is_cancelled(self):
        """A called-off charter, kept only as the record of a payment."""
        return self.PaymentStatus == PAYMENT_CANCELLED

    @property
    def refund_due(self):
        """Money was taken, the charter was called off, and none of it is back.

        Only a cancellation that kept its row can owe anything. An unpaid
        booking is deleted outright, since nothing was ever taken.
        """
        return self.is_cancelled and self.RefundedAt is None

    def hold_lapsed(self, now=None):
        return (now or datetime.now()) > self.pay_by


class Yacht(db.Model):
    __tablename__ = "Yacht"
    YachtID = db.Column(db.String(50), db.ForeignKey("Boat.BoatID"), primary_key=True)
    YachtName = db.Column(db.String(50))
    HasJacuzzi = db.Column(db.Boolean)


class Motorboat(db.Model):
    __tablename__ = "Motorboat"
    MotorboatID = db.Column(
        db.String(50), db.ForeignKey("Boat.BoatID"), primary_key=True
    )
    EngineType = db.Column(db.String(50))
    FuelType = db.Column(db.String(50))


class Catamaran(db.Model):
    __tablename__ = "Catamaran"
    CatamaranID = db.Column(
        db.String(50), db.ForeignKey("Boat.BoatID"), primary_key=True
    )
    NrOfCabins = db.Column(db.Integer)
    MaxCapacity = db.Column(db.Integer)
    HasJacuzzi = db.Column(db.Boolean)


class Employee(db.Model):
    __tablename__ = "Employee"
    EmployeeID = db.Column(db.String(50), primary_key=True)
    OfficeID = db.Column(
        db.String(50), db.ForeignKey("Office.OfficeID"), nullable=False
    )
    FirstName = db.Column(db.String(50), nullable=False)
    LastName = db.Column(db.String(50), nullable=False)
    Street = db.Column(db.String(100))
    ZIP = db.Column(db.String(10))
    Country = db.Column(db.String(50))
    City = db.Column(db.String(50))
    Birthdate = db.Column(db.Date, nullable=False)
    Email = db.Column(db.String(100), nullable=False)
    MobileNumber = db.Column(db.String(20))
    SelfInsuranceNr = db.Column(db.String(20), nullable=False, unique=True)
    Salary = db.Column(db.Numeric(10, 2), nullable=False)


class Staff(db.Model):
    __tablename__ = "Staff"
    StaffID = db.Column(
        db.String(50), db.ForeignKey("Employee.EmployeeID"), primary_key=True
    )
    WorkShift = db.Column(db.String(50), nullable=False)
    IsOnDuty = db.Column(db.Boolean, nullable=False)


class Manager(db.Model):
    __tablename__ = "Manager"
    ManagerID = db.Column(
        db.String(50), db.ForeignKey("Employee.EmployeeID"), primary_key=True
    )
    Department = db.Column(db.String(50))
    ManagementLevel = db.Column(db.String(50))
    SupervisorID = db.Column(db.String(50), db.ForeignKey("Manager.ManagerID"))
