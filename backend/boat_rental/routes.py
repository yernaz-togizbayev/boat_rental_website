"""Every route in the app, client and manager alike.

Two rules worth knowing before editing:

Client routes take ClientID from the session, never from the URL. That is what
makes it impossible to address someone else's rental, rather than a check that
could be forgotten. Manager routes take all three key components, because
acting on another client's booking is the point.

Availability is decided in one place. rental_overlap_filter() answers "do these
dates clash" for the booking page, the availability report and the re-check
inside attempt_booking(), so what looks bookable and what the form accepts
cannot drift apart.
"""

from flask import flash, redirect, request, render_template, session, url_for
from datetime import datetime, date, timedelta
from sqlalchemy import and_, func, select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from functools import wraps
from uuid import uuid4

from boat_rental.forms import BoatSelectionForm, BookingSearchForm, ManagerLoginForm, EmployeeHireForm, EmployeeEditForm, ConfirmDeleteForm, OfficeForm, BoatForm, ClientRegistrationForm, SupervisesForm, MaintainsForm, PaymentForm
from boat_rental.forms import (TEST_CARD_ACCEPTED, TEST_CARD_DECLINED, card_digits,
                               grouped_card)
from boat_rental import app, db, format_money
from boat_rental import assignments, images, nosql
from boat_rental.assignments import (
    detach_boat_links,
    detach_manager_links,
    detach_staff_links,
)
from boat_rental.generator import generate_data, stock_office
from boat_rental.models import (
    AVAILABILITY_AVAILABLE,
    DEFAULT_START_TIME,
    PAYMENT_CANCELLED,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    charter_total,
    served_cities,
    served_harbours,
    Office,
    Client,
    Boat,
    Rental,
    Employee,
    Staff,
    Manager,
    Yacht,
    Motorboat,
    Catamaran
)

# Role session helpers
EXCLUSIVE_SESSION_KEYS = ("manager", "client")

def sign_in(role, payload):
    # clear any other role
    for key in EXCLUSIVE_SESSION_KEYS:
        if key != role:
            session.pop(key, None)

    match role:
        case "manager":
            session["manager"] = payload

        case "client":
            session["client"] = payload

        case _:
            raise ValueError(f"Unknown role: {role}")

def sign_out():
    """Drop both role keys, leaving the rest of the session intact.

    Not session.clear(): signing out is about the role, and clearing the lot
    takes the rest of the session with it, including the flash queue. The
    "Logged out" message would disappear along with everything else.
    """
    for key in EXCLUSIVE_SESSION_KEYS:
        session.pop(key, None)



def manager_required(f):
    @wraps(f)
    def _wrapped(*args, **kwargs):
        match True:
            case _ if 'manager' in session:
                return f(*args, **kwargs)
            
            case _ if 'client' in session:
                flash("Please sign in as a manager to access this page.", "error")
                return redirect(url_for("manager_login"))
            
            case _:
                return redirect(url_for("manager_login"))
            
    return _wrapped

@app.route("/", methods=["GET", "POST"])
def index():
    return redirect(url_for("login"))


@app.route("/login")
def login():
    clients = Client.query.all()
    return render_template("login.html", clients=clients)


@app.route("/register", methods=["GET", "POST"])
def register_client():
    """Sign up a new client and sign them straight in.

    Auto-login is safe here precisely because auth is passwordless: /login is a
    picker over every client, so the next click would grant the same access
    anyway. Without it a new user has to find their own name in an unsorted
    list that grows with every registration.

    Arriving here already signed in ends that session instead of turning you
    away. The link lives on /login, the identity picker, so being there at all
    means you are changing who you are. Nothing is lost: the old account still
    exists, one click away on the page you came from. The flash says so,
    because a session ending quietly is worse than one ending.
    """
    if "client" in session:
        who = session["client"].get("FirstName", "that account")
        sign_out()
        flash(f"Signed out of {who}'s account so you can create a new one.", "info")

    form = ClientRegistrationForm()
    if form.validate_on_submit():
        client_id = f"C{uuid4().hex[:8]}"
        try:
            client = Client(
                ClientID=client_id,
                FirstName=form.first_name.data.strip(),
                LastName=form.last_name.data.strip(),
                Street=form.street.data or None,
                ZIP=form.zip.data or None,
                Country=form.country.data or None,
                City=form.city.data or None,
                Birthdate=form.birthdate.data,
                Email=form.email.data,
                MobileNumber=form.mobile.data or None,
                # CaptainLicenseNumber is UNIQUE, and the DB allows many NULLs
                # but not many empty strings, so a blank field has to become
                # None or the second licence-less signup collides.
                CaptainLicenseNumber=(form.captain_license.data or "").strip() or None,
            )
            db.session.add(client)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Unique constraint failed (Captain licence number must be unique).", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")
        else:
            sign_in('client',
                    {
                        "ClientID":  client.ClientID,
                        "FirstName": client.FirstName,
                        "LastName":  client.LastName,
                        "FullName":  client.full_name,
                        "Email":     client.Email,
                    }
                )
            flash(f"Welcome, {client.FirstName}! Your account is ready.", "success")
            return redirect(url_for("home"))

    return render_template("register.html", form=form)


@app.route("/logout")
def logout():
    sign_out()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.route("/select-client/<client_id>")
def select_client(client_id):
    client = Client.query.get(client_id)

    if not client:
        flash(f"Unknown client {client_id}.", "error")
        return redirect(url_for("login"))

    sign_in('client',
            {
                "ClientID":  client.ClientID,
                "FirstName": client.FirstName,
                "LastName":  client.LastName,
                "FullName":  client.full_name,
                "Email":     client.Email,
            }
        )
    return redirect(url_for("home"))


@app.route("/home")
def home():
    if "manager" in session:
        return redirect(url_for("list_employees"))

    if "client" not in session:
        return redirect(url_for("login"))

    return render_template("home.html", client=session["client"],
                           hero_slides=images.hero_slides(),
                           ports=served_cities())


@app.route("/booking", methods=["GET", "POST"])
def booking():
    if "client" not in session:
        return redirect(url_for("login"))

    search_form = BookingSearchForm()
    booking_form = None
    available_boats = []
    display_boats = []
    search_params = {}
    rental_days = 0

    if request.method == "POST" and request.form.get("book"):
        search_params = parse_search_params(
            request.form, "rental_date", "rental_end_date"
        )
        booked_boat = attempt_booking(search_params) if search_params else False
        if booked_boat:
            return redirect(url_for(
                "pay_rental",
                boat_id=booked_boat,
                rental_date=search_params["start_date"].isoformat(),
            ))
    elif search_form.validate_on_submit():
        search_params = {
            "city": search_form.city.data,
            "start_date": search_form.start_date.data,
            "end_date": search_form.end_date.data,
        }
    elif request.method == "GET" and request.args.get("city"):
        search_params = parse_search_params(request.args)
        if search_params:
            search_form.city.data = search_params["city"]
            search_form.start_date.data = search_params["start_date"]
            search_form.end_date.data = search_params["end_date"]

    if search_params:
        display_boats = get_boats_with_status(**search_params)
        available_boats = [(b, o) for b, o, status in display_boats
                           if status == "available"]
        booking_form = build_booking_form(search_params, available_boats)
        rental_days = (search_params["end_date"] - search_params["start_date"]).days

    # Pairs, so a card can say which country its harbour is in. The image
    # lookup and stocked_cities still key off the bare city name.
    harbours = served_harbours()
    cities = [city for city, _country in harbours]
    city_images, boat_type_images = images.city_and_boat_images(cities)
    # Conditions deliberately match get_available_boats(): maintenance-only and
    # unpriced both count as unstocked, because neither can be booked. If these
    # two drift apart, a harbour card invites a click that finds nothing.
    stocked_cities = {
        c for (c,) in db.session.query(Office.City)
        .join(Boat, Boat.OfficeID == Office.OfficeID)
        .filter(Boat.AvailabilityStatus == AVAILABILITY_AVAILABLE)
        .filter(Boat.DailyRate.isnot(None))
        .distinct()
    }

    return render_template(
        "booking.html",
        search_form=search_form,
        booking_form=booking_form,
        available_boats=available_boats,
        display_boats=display_boats,
        search_params=search_params,
        rental_days=rental_days,
        harbours=harbours,
        stocked_cities=stocked_cities,
        city_images=city_images,
        boat_type_images=boat_type_images,
    )


@app.route("/report")
def report():
    if "client" not in session:
        return redirect(url_for("login"))

    rentals = (
        db.session.query(Rental)
        .filter(Rental.ClientID == session.get("client").get("ClientID"))
        # The harbour is two hops away on Office. Eager-loaded so a long
        # logbook costs one query instead of one per row, and left as an outer
        # join so a rental still appears even if its boat or office has gone.
        .options(joinedload(Rental.boat).joinedload(Boat.office))
        .order_by(Rental.RentalDate.desc())
        .all()
    )
    return render_template(
        "report.html",
        rentals=rentals,
        delete_form=ConfirmDeleteForm(),
        today=date.today(),
    )


@app.route("/rentals/<boat_id>/<rental_date>/cancel", methods=["POST"])
def cancel_rental(boat_id, rental_date):
    """Cancel one of the signed-in client's own future rentals.

    The composite PK is (ClientID, BoatID, RentalDate), but only two of those
    three are in the URL. ClientID comes from the session, so a client cannot
    address another client's row at all and there is no ownership check here
    to get wrong.
    """
    if "client" not in session:
        return redirect(url_for("login"))

    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid cancellation request.", "error")
        return redirect(url_for("report"))

    try:
        start = datetime.strptime(rental_date, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid rental date.", "error")
        return redirect(url_for("report"))

    rental = Rental.query.filter_by(
        ClientID=session["client"]["ClientID"], BoatID=boat_id, RentalDate=start
    ).first()

    if rental is None:
        flash("That rental is not on your list.", "error")
        return redirect(url_for("report"))

    if rental.RentalDate <= date.today():
        flash("This rental has already started — contact the office to end it early.", "error")
        return redirect(url_for("report"))

    try:
        kept = release_rental(rental)
        db.session.commit()
        if kept:
            flash(
                f"Rental of boat {boat_id} cancelled. It stays on your logbook as "
                "CANCELLED because it was paid for — the office will arrange the "
                "refund.",
                "success",
            )
        else:
            flash(f"Rental of boat {boat_id} cancelled.", "success")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to cancel rental %s/%s", boat_id, rental_date)
        flash("Cancellation failed.", "error")

    return redirect(url_for("report"))


@app.route("/rentals/<boat_id>/<rental_date>/pay", methods=["GET", "POST"])
def pay_rental(boat_id, rental_date):
    """
    Demo checkout for one unpaid charter.
    """
    if "client" not in session:
        return redirect(url_for("login"))

    try:
        start = datetime.strptime(rental_date, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid rental date.", "error")
        return redirect(url_for("report"))

    rental = Rental.query.filter_by(
        ClientID=session["client"]["ClientID"], BoatID=boat_id, RentalDate=start
    ).first()

    if rental is None:
        flash("That rental is not on your list.", "error")
        return redirect(url_for("report"))

    if rental.PaymentStatus == PAYMENT_PAID:
        flash("That charter is already paid for.", "warning")
        return redirect(url_for("report"))

    # A hold is dead once its deadline passes, whether or not a search has got
    # round to sweeping it. CreatedAt is what makes this safe: a booking just
    # made inside the 24-hour window still has its grace period, so refusing
    # here cannot lock someone out of a charter they are in the middle of
    # paying for.
    if rental.hold_lapsed():
        db.session.delete(rental)
        db.session.commit()
        flash(
            f"Your reservation of boat {boat_id} expired — it was only held until "
            f"{rental.pay_by.strftime('%d %b, %H:%M')}. "
            "The boat is back on the market.",
            "error",
        )
        return redirect(url_for("report"))

    boat = Boat.query.get(boat_id)
    form = PaymentForm()

    if form.validate_on_submit():
        digits = card_digits(form.card_number.data)
        if digits != TEST_CARD_ACCEPTED:
            flash("Your card was declined. Try the demo card 4242 4242 4242 4242.",
                  "error")
            return redirect(url_for("pay_rental", boat_id=boat_id,
                                    rental_date=rental_date))
        try:
            rental.PaymentStatus = PAYMENT_PAID
            db.session.commit()
        except Exception:
            db.session.rollback()
            # No form data in the log line: it holds a card number.
            app.logger.exception("Failed to record payment for rental %s/%s",
                                 boat_id, rental_date)
            flash("Payment could not be recorded. Please try again.", "error")
            return redirect(url_for("pay_rental", boat_id=boat_id,
                                    rental_date=rental_date))

        flash(f"Payment received. Boat {boat_id} is confirmed — enjoy the charter!",
              "success")
        return redirect(url_for("report"))

    return render_template(
        "checkout.html",
        form=form,
        rental=rental,
        boat=boat,
        office=Office.query.get(boat.OfficeID) if boat else None,
        test_card=grouped_card(TEST_CARD_ACCEPTED),
        declined_card=grouped_card(TEST_CARD_DECLINED),
        must_pay_now=rental.is_late_booking,
        deadline=rental.pay_by,
        seconds_left=max(0, int((rental.pay_by - datetime.now()).total_seconds())),
    )


@app.route("/analytics")
def analytics():
    if "client" not in session:
        return redirect(url_for("login"))

    # served_cities() rather than every Office row: two offices in one city
    # would otherwise list it twice, and it is the shared definition of where
    # we operate, already alphabetical.
    harbours = served_harbours()
    cities = [city for city, _country in harbours]

    # No harbour until one is asked for, so the page opens as a question
    # rather than as a report on a city the reader never chose.
    #
    # The field can be typed into as well as picked from, so an unrecognised
    # city can arrive and has to be named as a typo. "0 free in Atlantis"
    # would read as a fleet problem instead. Matched case-insensitively,
    # because a field you type into invites "dubrovnik".
    requested = (request.args.get("city") or "").strip()
    by_fold = {city.casefold(): city for city in cities}
    filtered_city = by_fold.get(requested.casefold())
    if requested and filtered_city is None:
        flash(f"We don't have a harbour in {requested}. Pick one from the list.",
              "warning")

    default_start = date.today()
    default_end = default_start + timedelta(days=7)
    start_date = parse_date_arg("start_date", default_start)
    end_date = parse_date_arg("end_date", default_end)

    if end_date <= start_date:
        flash("End date must be after start date — showing the default range.", "warning")
        start_date, end_date = default_start, default_end

    # Both of these are questions about a named harbour, so neither is asked
    # until there is one. Called with no city they would report zero, which is
    # a different claim from "you have not chosen yet".
    available_boats = (get_available_boats(filtered_city, start_date, end_date)
                       if filtered_city else [])
    rentals_in_period = (rentals_in_city(filtered_city, start_date, end_date)
                         if filtered_city else 0)

    total_boats = Boat.query.count()
    available_count = Boat.query.filter_by(
        AvailabilityStatus=AVAILABILITY_AVAILABLE
    ).count()
    period_days = (end_date - start_date).days

    return render_template(
        "analytics.html",
        boats=available_boats,
        current_filter=filtered_city,
        start_date=start_date,
        end_date=end_date,
        period_days=period_days,
        total_boats=total_boats,
        available_count=available_count,
        rentals_in_period=rentals_in_period,
        cities=cities,
        harbours=harbours,
    )


@app.route("/credits")
def credits():
    """Photographer attribution for the Unsplash photos on the site.

    Not behind a login: the credit has to be reachable from the footer of
    every page, including the ones an anonymous visitor sees.
    """
    return render_template("credits.html", credits=images.photo_credits())


@app.route("/generate-data", methods=["POST"])
def reset_data():
    """Refill the demo data. Open to anonymous visitors, and repeatable.

    Not role-guarded on purpose: a fresh checkout has to be fillable before
    anyone can sign in. It is safe to press twice because generate_data()
    keeps the offices, so a second run refills the fleet rather than costing
    you every city a manager has added.
    """
    try:
        generate_data()
    except Exception:
        app.logger.exception("Data generation failed")
        flash("Could not generate the demo data.", "error")
        return redirect(url_for("login"))

    # generate_data() deletes the clients and managers a signed-in visitor
    # would be pointing at, so drop the role keys.
    sign_out()
    flash("Demo data refilled. Your harbours were kept. Please sign in.", "success")
    return redirect(url_for("login"))


def attempt_booking(params):
    """Validate a booking POST against live availability and store the rental.

    Returns True only if the rental was written. Everything the browser sends
    is untrusted here: boat_id is a <select> and both dates are hidden fields,
    all of which can be edited before submission. So rather than trusting the
    list of boats the client was originally shown, this re-derives what is
    bookable right now and validates the choice against that.
    """
    if params["start_date"] < date.today():
        flash("Rental start date cannot be in the past.", "error")
        return False

    available_boats = get_available_boats(**params)
    form = BoatSelectionForm(available_boats=available_boats, formdata=request.form)
    if not form.validate_on_submit():
        flash("That boat is not available for those dates. Please search again.", "error")
        return False

    boat_id = form.boat_id.data

    if rental_conflicts(boat_id, params["start_date"], params["end_date"]):
        flash(f"Boat {boat_id} was just booked by someone else.", "error")
        return False

    boat = next((b for b, _office in available_boats if b.BoatID == boat_id), None)
    days = (params["end_date"] - params["start_date"]).days
    total = charter_total(boat.DailyRate if boat else None, days)

    client_id = session["client"]["ClientID"]

    # A cancelled charter keeps its row, and the primary key is (ClientID,
    # BoatID, RentalDate), so the same client rebooking the same boat for the
    # same start date collides with it. Take the row over rather than refuse
    # the booking. Every field is rewritten, CreatedAt included, so the result
    # is a new booking and not a revived old one. Only a CANCELLED row can be
    # reused; a live one still means "already booked".
    previous = Rental.query.filter_by(
        ClientID=client_id, BoatID=boat_id, RentalDate=params["start_date"]
    ).first()
    if previous is not None and not previous.is_cancelled:
        flash("You have already booked this boat for that start date.", "error")
        return False

    try:
        rental = previous or Rental(
            ClientID=client_id, BoatID=boat_id, RentalDate=params["start_date"]
        )
        rental.RentalEndDate = params["end_date"]
        rental.PaymentStatus = PAYMENT_UNPAID
        rental.TotalAmount = total
        rental.StartTime = DEFAULT_START_TIME
        # Set explicitly rather than left to the column default. The payment
        # deadline is measured from it, so it is part of the booking rather
        # than bookkeeping, and a reused row has no default to fall back on:
        # it would keep the old booking's timestamp.
        rental.CreatedAt = datetime.now()
        if previous is None:
            db.session.add(rental)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("You have already booked this boat for that start date.", "error")
        return False
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to store rental for boat %s", boat_id)
        flash("Could not complete the booking. Please try again.", "error")
        return False

    flash(f"Boat {boat_id} reserved. Pay to confirm your charter.", "success")
    return boat_id


def parse_date_arg(name, fallback):
    """Read a YYYY-MM-DD query-string argument, falling back on bad input."""
    raw = request.args.get(name)
    if not raw:
        return fallback
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        flash(f"Ignoring invalid {name.replace('_', ' ')}: {raw!r}.", "warning")
        return fallback


def parse_search_params(source, start_key="start_date", end_key="end_date"):
    """Extract city/start/end from a form or query-string mapping.

    Returns {} and flashes on anything malformed, so callers never have to
    handle a ValueError from strptime.
    """
    city = (source.get("city") or "").strip()
    try:
        start_date = datetime.strptime(source.get(start_key, ""), "%Y-%m-%d").date()
        end_date = datetime.strptime(source.get(end_key, ""), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        flash("Invalid search parameters.", "error")
        return {}

    if not city or end_date <= start_date:
        flash("Invalid search parameters.", "error")
        return {}

    return {"city": city, "start_date": start_date, "end_date": end_date}


def build_booking_form(search_params, available_boats):
    """Booking form pre-filled with the searched dates, or None if nothing is free."""
    if not available_boats:
        return None

    form = BoatSelectionForm(available_boats=available_boats, formdata=None)
    form.city.data = search_params["city"]
    form.rental_date.data = search_params["start_date"].strftime("%Y-%m-%d")
    form.rental_end_date.data = search_params["end_date"].strftime("%Y-%m-%d")
    return form


def rental_overlap_filter(start_date, end_date):
    """Rentals overlapping the half-open interval [start_date, end_date).

    A rental whose RentalEndDate is NULL is open-ended and blocks the boat from
    its start date onwards.
    """
    return and_(
        Rental.RentalDate < end_date,
        or_(Rental.RentalEndDate.is_(None), Rental.RentalEndDate > start_date),
        # A cancelled charter keeps its row as the record of a payment but
        # holds nothing, and without this the boat stays blocked forever.
        # The NULL branch is spelled out because PaymentStatus is nullable:
        # for a NULL status `<> 'CANCELLED'` evaluates to NULL rather than
        # true, so every rental with no status would quietly stop blocking
        # its boat.
        or_(Rental.PaymentStatus.is_(None),
            Rental.PaymentStatus != PAYMENT_CANCELLED),
    )


def release_rental(rental):
    """Call off a booking, keeping the row only if money changed hands.

    An UNPAID booking is only a hold. Nothing was taken, so the row goes and
    the boat is free again. A PAID one is the only record that the client was
    charged, and deleting it erases that, for the same reason TotalAmount is
    frozen at booking instead of recomputed from DailyRate. It gets marked
    CANCELLED and stops holding the boat, because rental_overlap_filter()
    skips that status.

    Both cancellation paths call this. They differ over what may be cancelled,
    which is intended, but what cancelling does to a paid charter should not
    depend on who clicked. Does not commit.

    Returns True if the row was kept as a record.
    """
    if rental.PaymentStatus == PAYMENT_PAID:
        rental.PaymentStatus = PAYMENT_CANCELLED
        # Stamped here rather than worked out later. This is the moment the
        # refund became owed and nothing else in the row records it.
        rental.CancelledAt = datetime.now()
        return True
    db.session.delete(rental)
    return False


def rentals_in_city(city, start_date, end_date):
    """Count rentals in `city` overlapping [start_date, end_date).

    Scoped through Boat -> Office because Rental has no city of its own. This
    used to be an unjoined count over every Rental, which put a fleet-wide
    number on /analytics next to figures that all respect the city filter.
    """
    return (
        db.session.query(Rental)
        .join(Boat, Rental.BoatID == Boat.BoatID)
        .join(Office, Boat.OfficeID == Office.OfficeID)
        .filter(Office.City == city)
        .filter(rental_overlap_filter(start_date, end_date))
        .count()
    )


def rental_conflicts(boat_id, start_date, end_date):
    return db.session.query(
        db.session.query(Rental)
        .filter(Rental.BoatID == boat_id)
        .filter(rental_overlap_filter(start_date, end_date))
        .exists()
    ).scalar()


def release_expired_holds():
    """Delete unpaid bookings whose payment deadline has passed.

    An unpaid booking is a hold rather than a charter, and it keeps the boat
    only until the day before the trip. That is why a same-day booking has to
    be paid there and then: its deadline has already gone.

    This runs from get_available_boats() instead of a scheduler, because the
    app has nowhere to run one. Availability is read on every search, which is
    the moment the answer has to be honest. PAID rentals are never touched.

    Scoped to holds that still block a boat: starting today, or under way and
    not yet finished. An unpaid charter whose window is entirely in the past
    blocks nothing, so it is history rather than a hold, and deleting it would
    rewrite the record the same way cancel_rental_as_manager() refuses to for
    finished charters.
    """
    today = date.today()
    now = datetime.now()
    # SQL narrows it to holds that could still matter; the deadline itself is
    # per-row (it depends on StartTime and CreatedAt) so it is decided here.
    # The window reaches a day back because a hold for tomorrow can lapse today.
    candidates = Rental.query.filter(
        Rental.PaymentStatus != PAYMENT_PAID,
        # A cancelled charter is not an unpaid hold, it is a receipt. It also
        # satisfies "not PAID", so without this the sweep would delete the one
        # row this whole status exists to preserve.
        or_(Rental.PaymentStatus.is_(None),
            Rental.PaymentStatus != PAYMENT_CANCELLED),
        Rental.RentalDate <= today + timedelta(days=1),
        or_(Rental.RentalEndDate.is_(None), Rental.RentalEndDate > today),
    ).all()
    expired = [r for r in candidates if r.hold_lapsed(now)]
    if not expired:
        return 0

    try:
        for rental in expired:
            db.session.delete(rental)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to release expired holds")
        return 0

    app.logger.info("Released %d unpaid hold(s)", len(expired))
    return len(expired)


def get_boats_with_status(city, start_date, end_date):
    """[(boat, office, status)] for the whole rentable fleet in a city.

    status is "available", "booked" or "maintenance". The booking page lists
    all three rather than only what is free: a page showing two boats reads as
    a company with two boats, and a taken one tells you the harbour is worth
    trying on other dates.

    Bookability is decided by the same rental_overlap_filter() that
    get_available_boats() uses, so the greyed-out set and the bookable set
    cannot disagree. Unpriced boats are left out entirely, since an
    incomplete record is not something a client should be choosing from.
    """
    release_expired_holds()

    taken = {
        boat_id for (boat_id,) in db.session.query(Rental.BoatID)
        .filter(rental_overlap_filter(start_date, end_date))
    }
    rows = (
        db.session.query(Boat, Office)
        .select_from(Boat)
        .join(Office, Boat.OfficeID == Office.OfficeID)
        .filter(Office.City == city)
        .filter(Boat.DailyRate.isnot(None))
        .order_by(Boat.Manufacturer, Boat.BoatID)
        .all()
    )

    result = []
    for boat, office in rows:
        if boat.AvailabilityStatus != AVAILABILITY_AVAILABLE:
            status = "maintenance"
        elif boat.BoatID in taken:
            status = "booked"
        else:
            status = "available"
        result.append((boat, office, status))
    return result


def get_available_boats(city, start_date, end_date):
    """Boats in this city that can actually be chartered for these dates.

    DailyRate is nullable and a manager may leave it blank, but a boat with
    no price is not ready to rent: it reaches checkout with a NULL total that
    the demo card happily "pays". Filtering here rather than in a template
    covers all three callers, the booking page, the availability page and
    attempt_booking()'s re-derivation, so a hand-crafted POST is refused too.
    """
    release_expired_holds()

    conflicting_rentals = select(Rental.BoatID).filter(
        rental_overlap_filter(start_date, end_date)
    )

    return (
        db.session.query(Boat, Office)
        .select_from(Boat)
        .join(Office, Boat.OfficeID == Office.OfficeID)
        .filter(Office.City == city)
        .filter(Boat.AvailabilityStatus == AVAILABILITY_AVAILABLE)
        .filter(Boat.DailyRate.isnot(None))
        .filter(~Boat.BoatID.in_(conflicting_rentals))
        # Both callers render the boat's type, and the yacht row also carries
        # the jacuzzi. Left lazy that is an extra query per boat and up to
        # three for one with no subclass row, on a page that lists a whole
        # harbour.
        .options(joinedload(Boat.yacht), joinedload(Boat.motorboat),
                 joinedload(Boat.catamaran))
        .order_by(Boat.Manufacturer, Boat.BoatID)
        .all()
    )


@app.route("/manager/login", methods=["GET", "POST"])
def manager_login():
    managers = db.session.query(Manager, Employee).join(Employee, Manager.ManagerID == Employee.EmployeeID).order_by(Employee.LastName).all()
    form = ManagerLoginForm()
    form.manager_id.choices = [(m.ManagerID, f"{e.FirstName} {e.LastName} ({e.EmployeeID})") for m, e in managers]

    if form.validate_on_submit():
        selected = next(((m, e) for m, e in managers if m.ManagerID == form.manager_id.data), None)
        if selected:
            m, e = selected
            sign_in('manager',
                    {
                        'ManagerID':  m.ManagerID,
                        'EmployeeID': e.EmployeeID,
                        'FirstName':  e.FirstName,
                        'LastName':   e.LastName,
                        'FullName':   f"{e.FirstName} {e.LastName}"
                    }
            )
            flash(f"Logged in as manager {e.FirstName} {e.LastName}.", "success")
            return redirect(url_for("list_employees"))
        flash("Invalid manager selected.", "error")
    return render_template("manager_login.html", form=form)

@app.route("/manager/logout")
def manager_logout():
    session.pop('manager', None)
    flash("Logged out as manager.", "success")
    return redirect(url_for("manager_login"))

@app.route("/manager/employees")
@manager_required
def list_employees():
    employees = db.session.query(Employee).order_by(Employee.LastName, Employee.FirstName).all()
    staff_ids = {s.StaffID for s in Staff.query.all()}
    manager_ids = {m.ManagerID for m in Manager.query.all()}
    roles = {e.EmployeeID: ("Manager" if e.EmployeeID in manager_ids else "Staff" if e.EmployeeID in staff_ids else "-")
             for e in employees}
    return render_template("employees_list.html", employees=employees, roles=roles, delete_form=ConfirmDeleteForm())

@app.route("/manager/employees/new", methods=["GET", "POST"])
@manager_required
def hire_employee():
    form = EmployeeHireForm()
    form.office_id.choices = [(o.OfficeID, f"{o.City} ({o.OfficeID})") for o in Office.query.order_by(Office.City).all()]
    mgrs = db.session.query(Manager, Employee).join(Employee, Manager.ManagerID == Employee.EmployeeID).order_by(Employee.LastName).all()
    form.supervisor_id.choices = [("", "— None —")] + [(m.ManagerID, f"{e.FirstName} {e.LastName}") for m, e in mgrs]

    if form.validate_on_submit():
        emp_id = f"E{uuid4().hex[:8]}"
        try:
            employee = Employee(
                EmployeeID=emp_id,
                OfficeID=form.office_id.data,
                FirstName=form.first_name.data,
                LastName=form.last_name.data,
                Street=form.street.data or None,
                ZIP=form.zip.data or None,
                Country=form.country.data or None,
                City=form.city.data or None,
                Birthdate=form.birthdate.data,
                Email=form.email.data,
                MobileNumber=form.mobile.data or None,
                SelfInsuranceNr=form.self_insurance_nr.data,
                Salary=form.salary.data
            )
            db.session.add(employee)
            db.session.flush()

            if form.role.data == "staff":
                db.session.add(Staff(StaffID=emp_id, WorkShift=form.work_shift.data or "Day", IsOnDuty=bool(form.is_on_duty.data)))
            else:
                db.session.add(Manager(ManagerID=emp_id, Department=form.department.data or None, ManagementLevel=form.management_level.data or None, SupervisorID=form.supervisor_id.data or None))

            db.session.commit()
            flash(f"Employee {employee.FirstName} {employee.LastName} hired.", "success")
            return redirect(url_for("list_employees"))
        except IntegrityError:
            db.session.rollback()
            flash("Unique constraint failed (Self insurance number must be unique).", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")
    return render_template("employee_new.html", form=form)

@app.route("/manager/employees/<emp_id>/edit", methods=["GET", "POST"])
@manager_required
def edit_employee(emp_id):
    employee = Employee.query.get_or_404(emp_id)
    is_staff = Staff.query.get(emp_id) is not None
    is_manager = Manager.query.get(emp_id) is not None

    # obj= is deliberately not passed: the model attributes are FirstName /
    # LastName while the form fields are first_name / last_name, so it matched
    # nothing. The GET block below populates the form explicitly.
    form = EmployeeEditForm()
    form.office_id.choices = [(o.OfficeID, f"{o.City} ({o.OfficeID})") for o in Office.query.order_by(Office.City).all()]
    mgrs = db.session.query(Manager, Employee).join(Employee, Manager.ManagerID == Employee.EmployeeID).filter(Manager.ManagerID != emp_id).order_by(Employee.LastName).all()
    form.supervisor_id.choices = [("", "— None —")] + [(m.ManagerID, f"{e.FirstName} {e.LastName}") for m, e in mgrs]

    if request.method == "GET":
        # base employee fields
        form.office_id.data = employee.OfficeID
        form.first_name.data = employee.FirstName
        form.last_name.data = employee.LastName
        form.street.data = employee.Street or ""
        form.zip.data = employee.ZIP or ""
        form.country.data = employee.Country or ""
        form.city.data = employee.City or ""
        form.birthdate.data = employee.Birthdate
        form.email.data = employee.Email
        form.mobile.data = employee.MobileNumber or ""
        form.self_insurance_nr.data = employee.SelfInsuranceNr
        form.salary.data = employee.Salary

        # role-specific fields
        form.role.data = "manager" if is_manager else "staff" if is_staff else ""
        if is_staff:
            s = Staff.query.get(emp_id)
            form.work_shift.data = s.WorkShift
            form.is_on_duty.data = s.IsOnDuty
        if is_manager:
            m = Manager.query.get(emp_id)
            form.department.data = m.Department or ""
            form.management_level.data = m.ManagementLevel or ""
            form.supervisor_id.data = m.SupervisorID or ""

    if form.validate_on_submit():
        if (
            is_manager
            and form.role.data == "staff"
            and emp_id == session.get("manager", {}).get("EmployeeID")
        ):
            flash("You cannot demote the manager you are signed in as.", "error")
            return render_template("employee_edit.html", form=form, employee=employee)

        try:
            employee.OfficeID = form.office_id.data
            employee.FirstName = form.first_name.data
            employee.LastName = form.last_name.data
            employee.Street = form.street.data or None
            employee.ZIP = form.zip.data or None
            employee.Country = form.country.data or None
            employee.City = form.city.data or None
            employee.Birthdate = form.birthdate.data
            employee.Email = form.email.data
            employee.MobileNumber = form.mobile.data or None
            employee.SelfInsuranceNr = form.self_insurance_nr.data
            employee.Salary = form.salary.data

            if form.role.data == "staff":
                if is_manager:
                    detach_manager_links(emp_id)
                    Manager.query.filter_by(ManagerID=emp_id).delete()
                if is_staff:
                    s = Staff.query.get(emp_id)
                    s.WorkShift = form.work_shift.data or "Day"
                    s.IsOnDuty = bool(form.is_on_duty.data)
                else:
                    db.session.add(Staff(StaffID=emp_id, WorkShift=form.work_shift.data or "Day", IsOnDuty=bool(form.is_on_duty.data)))
            else:
                if is_staff:
                    detach_staff_links(emp_id)
                    Staff.query.filter_by(StaffID=emp_id).delete()
                if is_manager:
                    m = Manager.query.get(emp_id)
                    m.Department = form.department.data or None
                    m.ManagementLevel = form.management_level.data or None
                    m.SupervisorID = form.supervisor_id.data or None
                else:
                    db.session.add(Manager(ManagerID=emp_id, Department=form.department.data or None, ManagementLevel=form.management_level.data or None, SupervisorID=form.supervisor_id.data or None))

            db.session.commit()
            flash("Employee updated.", "success")
            return redirect(url_for("list_employees"))
        except IntegrityError:
            db.session.rollback()
            flash("Unique constraint failed (Self insurance number must be unique).", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")

    return render_template("employee_edit.html", form=form, employee=employee)

@app.route("/manager/employees/<emp_id>/delete", methods=["POST"])
@manager_required
def delete_employee(emp_id):
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid delete request.", "error")
        return redirect(url_for("list_employees"))

    if emp_id == session.get("manager", {}).get("EmployeeID"):
        flash("You cannot delete the employee you are signed in as.", "error")
        return redirect(url_for("list_employees"))

    employee = Employee.query.get_or_404(emp_id)

    try:
        detach_staff_links(emp_id)
        detach_manager_links(emp_id)
        Staff.query.filter_by(StaffID=emp_id).delete()
        Manager.query.filter_by(ManagerID=emp_id).delete()
        db.session.delete(employee)
        db.session.commit()
        flash("Employee deleted.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Cannot delete this employee — other records still reference them.", "error")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete employee %s", emp_id)
        flash("Delete failed.", "error")

    return redirect(url_for("list_employees"))


# detach_manager_links / detach_staff_links / detach_boat_links now live in
# boat_rental/assignments.py, next to the rest of the Supervises / Maintains SQL.



@app.route("/manager/offices")
@manager_required
def list_offices():
    offices = Office.query.order_by(Office.City).all()
    boat_counts = dict(
        db.session.query(Boat.OfficeID, func.count(Boat.BoatID))
        .group_by(Boat.OfficeID)
        .all()
    )
    employee_counts = dict(
        db.session.query(Employee.OfficeID, func.count(Employee.EmployeeID))
        .group_by(Employee.OfficeID)
        .all()
    )
    return render_template(
        "offices_list.html",
        offices=offices,
        boat_counts=boat_counts,
        employee_counts=employee_counts,
        delete_form=ConfirmDeleteForm(),
    )


@app.route("/manager/offices/new", methods=["GET", "POST"])
@manager_required
def new_office():
    form = OfficeForm()
    if form.validate_on_submit():
        try:
            office = Office(
                OfficeID=f"O{uuid4().hex[:8]}",
                City=form.city.data.strip(),
                Country=form.country.data.strip(),
                Street=form.street.data.strip(),
                ZIP=form.zip.data.strip(),
            )
            db.session.add(office)
            db.session.commit()
            flash(f"{office.City} added. Add boats there to make it bookable.", "success")
            return redirect(url_for("list_offices"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")
    return render_template("office_form.html", form=form, office=None)


@app.route("/manager/offices/<office_id>/edit", methods=["GET", "POST"])
@manager_required
def edit_office(office_id):
    office = Office.query.get_or_404(office_id)
    form = OfficeForm()

    if request.method == "GET":
        form.city.data = office.City
        form.country.data = office.Country
        form.street.data = office.Street
        form.zip.data = office.ZIP

    if form.validate_on_submit():
        try:
            office.City = form.city.data.strip()
            office.Country = form.country.data.strip()
            office.Street = form.street.data.strip()
            office.ZIP = form.zip.data.strip()
            db.session.commit()
            flash("Office updated.", "success")
            return redirect(url_for("list_offices"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")

    return render_template("office_form.html", form=form, office=office)


@app.route("/manager/offices/<office_id>/delete", methods=["POST"])
@manager_required
def delete_office(office_id):
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid delete request.", "error")
        return redirect(url_for("list_offices"))

    office = Office.query.get_or_404(office_id)

    boats = Boat.query.filter_by(OfficeID=office_id).count()
    employees = Employee.query.filter_by(OfficeID=office_id).count()
    if boats or employees:
        flash(
            f"Cannot delete {office.City}: {boats} boat(s) and {employees} employee(s) "
            "are still assigned to it. Move or delete those first.",
            "error",
        )
        return redirect(url_for("list_offices"))

    try:
        db.session.delete(office)
        db.session.commit()
        flash(f"{office.City} deleted.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Cannot delete this office — other records still reference it.", "error")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete office %s", office_id)
        flash("Delete failed.", "error")

    return redirect(url_for("list_offices"))


@app.route("/manager/offices/<office_id>/stock", methods=["POST"])
@manager_required
def stock_office_fleet(office_id):
    """
    Give an empty harbour a starter fleet without touching anything else.
    """
    office = Office.query.get(office_id)
    if not office:
        flash(f"Unknown office {office_id}.", "error")
        return redirect(url_for("list_offices"))

    if Boat.query.filter_by(OfficeID=office_id).count():
        flash(f"{office.City} already has boats. Use “Add boat” for one more.",
              "warning")
        return redirect(url_for("list_offices"))

    try:
        boats = stock_office(office_id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to stock office %s", office_id)
        flash(f"Could not add boats to {office.City}.", "error")
    else:
        flash(f"Added {len(boats)} boats to {office.City}. It is now bookable.",
              "success")

    return redirect(url_for("list_offices"))


BOAT_SUBCLASSES = {
    "yacht":     (Yacht,     "YachtID"),
    "motorboat": (Motorboat, "MotorboatID"),
    "catamaran": (Catamaran, "CatamaranID"),
}


def boat_type_of(boat_id):
    """Return the subclass key for a boat, or None if it has no subclass row."""
    for key, (model, pk) in BOAT_SUBCLASSES.items():
        if db.session.query(model).filter(getattr(model, pk) == boat_id).first():
            return key
    return None


def write_boat_subclass(boat_id, form, previous_type=None):
    """Create or update the Yacht/Motorboat/Catamaran row for a boat.

    Mirrors the Staff/Manager transition in edit_employee(): switching type
    deletes the old subclass row before inserting the new one.
    """
    new_type = form.boat_type.data
    if previous_type and previous_type != new_type:
        model, pk = BOAT_SUBCLASSES[previous_type]
        db.session.query(model).filter(getattr(model, pk) == boat_id).delete()
        previous_type = None

    match new_type:
        case "yacht":
            row = Yacht.query.get(boat_id) if previous_type else None
            if row is None:
                row = Yacht(YachtID=boat_id)
                db.session.add(row)
            row.YachtName = form.yacht_name.data or None
            row.HasJacuzzi = bool(form.has_jacuzzi.data)
        case "motorboat":
            row = Motorboat.query.get(boat_id) if previous_type else None
            if row is None:
                row = Motorboat(MotorboatID=boat_id)
                db.session.add(row)
            row.EngineType = form.engine_type.data or None
            row.FuelType = form.fuel_type.data or None
        case "catamaran":
            row = Catamaran.query.get(boat_id) if previous_type else None
            if row is None:
                row = Catamaran(CatamaranID=boat_id)
                db.session.add(row)
            row.NrOfCabins = form.nr_of_cabins.data
            row.MaxCapacity = form.max_capacity.data
            row.HasJacuzzi = bool(form.has_jacuzzi.data)
        case _:
            raise ValueError(f"Unknown boat type: {new_type}")


def office_choices():
    return [
        (o.OfficeID, f"{o.City}, {o.Country}")
        for o in Office.query.order_by(Office.City).all()
    ]


def staff_choices():
    rows = (
        db.session.query(Staff, Employee)
        .join(Employee, Staff.StaffID == Employee.EmployeeID)
        .order_by(Employee.LastName, Employee.FirstName)
        .all()
    )
    return [(s.StaffID, f"{e.FirstName} {e.LastName} ({e.EmployeeID})") for s, e in rows]


def manager_choices():
    rows = (
        db.session.query(Manager, Employee)
        .join(Employee, Manager.ManagerID == Employee.EmployeeID)
        .order_by(Employee.LastName, Employee.FirstName)
        .all()
    )
    return [(m.ManagerID, f"{e.FirstName} {e.LastName} ({e.EmployeeID})") for m, e in rows]


def boat_choices():
    rows = (
        db.session.query(Boat, Office)
        .join(Office, Boat.OfficeID == Office.OfficeID)
        .order_by(Office.City, Boat.BoatID)
        .all()
    )
    return [(b.BoatID, f"{b.BoatID} — {b.Manufacturer} ({o.City})") for b, o in rows]


@app.route("/manager/boats")
@manager_required
def list_boats():
    boats = (
        db.session.query(Boat, Office)
        .join(Office, Boat.OfficeID == Office.OfficeID)
        .order_by(Office.City, Boat.BoatID)
        .all()
    )
    types = {boat.BoatID: boat_type_of(boat.BoatID) for boat, _ in boats}
    return render_template(
        "boats_list.html", boats=boats, types=types, delete_form=ConfirmDeleteForm()
    )


@app.route("/manager/boats/new", methods=["GET", "POST"])
@manager_required
def new_boat():
    form = BoatForm()
    form.office_id.choices = office_choices()

    if not form.office_id.choices:
        flash("Add a city first — a boat has to belong to one.", "warning")
        return redirect(url_for("new_office"))

    if request.method == "GET" and request.args.get("office_id"):
        form.office_id.data = request.args["office_id"]

    if form.validate_on_submit():
        boat_id = f"B{uuid4().hex[:8]}"
        try:
            db.session.add(
                Boat(
                    BoatID=boat_id,
                    OfficeID=form.office_id.data,
                    Manufacturer=form.manufacturer.data.strip(),
                    Seats=form.seats.data,
                    Length=form.length.data,
                    Weight=form.weight.data,
                    Horsepower=form.horsepower.data,
                    DailyRate=form.daily_rate.data,
                    AvailabilityStatus=form.availability_status.data,
                )
            )
            db.session.flush()
            write_boat_subclass(boat_id, form)
            db.session.commit()
            flash(f"Boat {boat_id} added.", "success")
            return redirect(url_for("list_boats"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")

    return render_template("boat_form.html", form=form, boat=None)


@app.route("/manager/boats/<boat_id>/edit", methods=["GET", "POST"])
@manager_required
def edit_boat(boat_id):
    boat = Boat.query.get_or_404(boat_id)
    previous_type = boat_type_of(boat_id)

    form = BoatForm()
    form.office_id.choices = office_choices()

    if request.method == "GET":
        form.office_id.data = boat.OfficeID
        form.manufacturer.data = boat.Manufacturer
        form.seats.data = boat.Seats
        form.length.data = boat.Length
        form.weight.data = boat.Weight
        form.horsepower.data = boat.Horsepower
        form.daily_rate.data = boat.DailyRate
        form.availability_status.data = boat.AvailabilityStatus
        form.boat_type.data = previous_type or "motorboat"

        match previous_type:
            case "yacht":
                y = Yacht.query.get(boat_id)
                form.yacht_name.data = y.YachtName or ""
                form.has_jacuzzi.data = bool(y.HasJacuzzi)
            case "motorboat":
                m = Motorboat.query.get(boat_id)
                form.engine_type.data = m.EngineType or ""
                form.fuel_type.data = m.FuelType or ""
            case "catamaran":
                c = Catamaran.query.get(boat_id)
                form.nr_of_cabins.data = c.NrOfCabins
                form.max_capacity.data = c.MaxCapacity
                form.has_jacuzzi.data = bool(c.HasJacuzzi)

    if form.validate_on_submit():
        try:
            boat.OfficeID = form.office_id.data
            boat.Manufacturer = form.manufacturer.data.strip()
            boat.Seats = form.seats.data
            boat.Length = form.length.data
            boat.Weight = form.weight.data
            boat.Horsepower = form.horsepower.data
            boat.DailyRate = form.daily_rate.data
            boat.AvailabilityStatus = form.availability_status.data
            write_boat_subclass(boat_id, form, previous_type)
            db.session.commit()
            flash("Boat updated.", "success")
            return redirect(url_for("list_boats"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")

    return render_template("boat_form.html", form=form, boat=boat)


@app.route("/manager/boats/<boat_id>/delete", methods=["POST"])
@manager_required
def delete_boat(boat_id):
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid delete request.", "error")
        return redirect(url_for("list_boats"))

    boat = Boat.query.get_or_404(boat_id)

    rentals = Rental.query.filter_by(BoatID=boat_id).count()
    if rentals:
        flash(
            f"Cannot delete {boat_id}: it has {rentals} rental(s) on record. "
            "Set it to Maintenance instead to take it out of service.",
            "error",
        )
        return redirect(url_for("list_boats"))

    try:
        detach_boat_links(boat_id)
        for model, pk in BOAT_SUBCLASSES.values():
            db.session.query(model).filter(getattr(model, pk) == boat_id).delete()
        db.session.delete(boat)
        db.session.commit()
        flash(f"Boat {boat_id} deleted.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Cannot delete this boat — other records still reference it.", "error")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete boat %s", boat_id)
        flash("Delete failed.", "error")

    return redirect(url_for("list_boats"))


@app.route("/manager/rentals")
@manager_required
def list_rentals():
    city = request.args.get("city") or ""

    query = (
        db.session.query(Rental, Client, Boat, Office)
        .join(Client, Rental.ClientID == Client.ClientID)
        .join(Boat, Rental.BoatID == Boat.BoatID)
        .join(Office, Boat.OfficeID == Office.OfficeID)
    )
    if city:
        query = query.filter(Office.City == city)

    rows = query.order_by(Rental.RentalDate.desc(), Office.City).all()
    cities = served_cities()

    return render_template(
        "rentals_list.html",
        rows=rows,
        cities=cities,
        current_filter=city,
        today=date.today(),
        delete_form=ConfirmDeleteForm(),
    )


@app.route("/manager/rentals/<client_id>/<boat_id>/<rental_date>/delete", methods=["POST"])
@manager_required
def cancel_rental_as_manager(client_id, boat_id, rental_date):
    """
    Call off any client's charter.
    """
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid cancellation request.", "error")
        return redirect(url_for("list_rentals"))

    try:
        start = datetime.strptime(rental_date, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid rental date.", "error")
        return redirect(url_for("list_rentals"))

    rental = Rental.query.filter_by(
        ClientID=client_id, BoatID=boat_id, RentalDate=start
    ).first()

    if rental is None:
        flash("That rental no longer exists.", "warning")
        return redirect(url_for("list_rentals"))

    if rental.RentalEndDate and rental.RentalEndDate < date.today():
        flash(
            f"{boat_id} finished on {rental.RentalEndDate:%d %b %Y}. Completed charters "
            "are kept as a record and cannot be removed here.",
            "error",
        )
        return redirect(url_for("list_rentals"))

    try:
        kept = release_rental(rental)
        db.session.commit()
        flash(
            f"Cancelled {boat_id} for {client_id} on {start:%d %b %Y}."
            + (" It was paid for, so it stays on record as CANCELLED for the refund."
               if kept else ""),
            "success",
        )
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to cancel rental %s/%s/%s", client_id, boat_id, rental_date)
        flash("Cancellation failed.", "error")

    return redirect(url_for("list_rentals", city=request.form.get("city") or None))


@app.route("/manager/rentals/<client_id>/<boat_id>/<rental_date>/refund", methods=["POST"])
@manager_required
def refund_rental(client_id, boat_id, rental_date):
    """Record that the refund for a cancelled charter has been paid back.

    Manager-only, and takes all three key components because it acts on
    someone else's booking, the same shape as cancel_rental_as_manager().
    There is no client-side version: a client marking their own refund issued
    would be writing the office's side of the ledger.

    It only stamps RefundedAt. The money moves outside this app, so this
    records that it happened rather than doing it.
    """
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid refund request.", "error")
        return redirect(url_for("list_rentals"))

    try:
        start = datetime.strptime(rental_date, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid rental date.", "error")
        return redirect(url_for("list_rentals"))

    rental = Rental.query.filter_by(
        ClientID=client_id, BoatID=boat_id, RentalDate=start
    ).first()

    if rental is None:
        flash("That rental no longer exists.", "warning")
        return redirect(url_for("list_rentals"))

    # Guarded both ways: nothing is owed on a charter that was not cancelled,
    # and marking one refunded twice would hide that it was paid back once.
    if not rental.is_cancelled:
        flash(f"{boat_id} was not cancelled, so there is nothing to refund.", "error")
        return redirect(url_for("list_rentals"))

    if rental.RefundedAt is not None:
        flash(f"That refund was already recorded on {rental.RefundedAt:%d %b %Y}.",
              "warning")
        return redirect(url_for("list_rentals"))

    try:
        rental.RefundedAt = datetime.now()
        db.session.commit()
        flash(f"Refund of {format_money(rental.TotalAmount)} recorded for {client_id}.",
              "success")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to record refund %s/%s/%s",
                             client_id, boat_id, rental_date)
        flash("Could not record the refund.", "error")

    return redirect(url_for("list_rentals", city=request.form.get("city") or None))



# =========================
# NoSQL (MongoDB)
# =========================

@app.route("/manager/nosql")
@manager_required
def nosql_console():
    """The MongoDB side: migrate, run both reports, and see the index stats.

    Manager-only because it exposes the migration, which replaces every
    collection. The two reports are the same questions the relational pages
    answer, asked of the document model instead, so the answers can be
    compared side by side.
    """
    if not nosql.available():
        return render_template("nosql.html", online=False)

    cities = served_cities()
    city = request.args.get("city") or (cities[0] if cities else "")
    start_date = parse_date_arg("start_date", date.today())
    end_date = parse_date_arg("end_date", date.today() + timedelta(days=7))
    if end_date <= start_date:
        end_date = start_date + timedelta(days=7)

    mongo = nosql.get_db()
    counts = {name: mongo[name].estimated_document_count()
              for name in nosql.COLLECTIONS}
    migrated = any(counts.values())

    boats = report = manager_id = None
    managers = []
    if migrated:
        boats = nosql.available_boats(city, start_date, end_date)
        managers = [(d["_id"], f"{d['name']['first']} {d['name']['last']}")
                    for d in mongo.managers.find({}, {"name": 1}).sort("_id")]
        manager_id = request.args.get("manager_id") or (managers[0][0] if managers else None)
        report = nosql.supervised_staff(manager_id) if manager_id else None

    return render_template(
        "nosql.html",
        online=True,
        migrated=migrated,
        counts=counts,
        cities=cities,
        current_city=city,
        start_date=start_date,
        end_date=end_date,
        boats=boats,
        managers=managers,
        manager_id=manager_id,
        report=report,
        confirm_form=ConfirmDeleteForm(),
    )


@app.route("/manager/nosql/migrate", methods=["POST"])
@manager_required
def nosql_migrate():
    """Clear the collections and rebuild them from MariaDB.

    One-way by design: the relational database stays the system of record, and
    nothing here writes back to it.
    """
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid migration request.", "error")
        return redirect(url_for("nosql_console"))

    try:
        counts = nosql.migrate()
    except Exception:
        app.logger.exception("NoSQL migration failed")
        flash("Migration failed. Is the mongo container running?", "error")
    else:
        flash("Migrated {offices} offices, {boats} boats, {rentals} rentals, "
              "{managers} managers and {clients} clients into MongoDB."
              .format(**counts), "success")
    return redirect(url_for("nosql_console"))


@app.route("/manager/nosql/indexes")
@manager_required
def nosql_indexes():
    """The same two queries explained with and without their index."""
    if not nosql.available():
        return render_template("nosql_indexes.html", online=False)

    cities = served_cities()
    city = cities[0] if cities else ""
    start = date.today()
    end = start + timedelta(days=7)
    pipeline = nosql.available_boats_pipeline(city, start, end)

    mongo = nosql.get_db()
    sample = mongo.managers.find_one({"supervisedStaff.0": {"$exists": True}})
    staff_id = sample["supervisedStaff"][0]["employeeId"] if sample else None
    reverse = {"supervisedStaff.employeeId": staff_id}

    measured = [
        ("offices", "city_idx", f"Boats free in {city}", pipeline,
         [nosql.explain_stats("offices", pipeline, False),
          nosql.explain_stats("offices", pipeline, True)]),
        ("managers", "supervised_staff_idx",
         f"Which manager supervises {staff_id}", reverse,
         [nosql.explain_stats("managers", reverse, False),
          nosql.explain_stats("managers", reverse, True)]),
    ]
    nosql.ensure_indexes()
    return render_template("nosql_indexes.html", online=True, measured=measured,
                           city=city, staff_id=staff_id)

# =========================
# Assignments (the two m:n relations)
# =========================
# Supervises and Maintains are filled by the generator but were never readable
# or editable in the app, so both relationships from the ER model dead-ended in
# the seed data. All the SQL lives in boat_rental/assignments.py.

@app.route("/manager/assignments/supervision", methods=["GET", "POST"])
@manager_required
def supervision_assignments():
    form = SupervisesForm()
    form.manager_id.choices = manager_choices()
    form.staff_id.choices = staff_choices()

    if not form.staff_id.choices:
        # Render the list anyway. Redirecting would make the page unreachable
        # while the rows already on it are still worth reading.
        flash("Hire a staff member first — only staff can be supervised.", "warning")
    elif form.validate_on_submit():
        try:
            assignments.add_supervises(form.manager_id.data, form.staff_id.data)
            db.session.commit()
            flash("Supervision assigned.", "success")
            return redirect(url_for("supervision_assignments"))
        except IntegrityError:
            db.session.rollback()
            flash("That manager already supervises that staff member.", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")

    return render_template(
        "assignments_supervision.html",
        form=form,
        rows=assignments.list_supervises(),
        delete_form=ConfirmDeleteForm(),
    )


@app.route("/manager/assignments/supervision/<manager_id>/<staff_id>/delete", methods=["POST"])
@manager_required
def unassign_supervision(manager_id, staff_id):
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid delete request.", "error")
        return redirect(url_for("supervision_assignments"))

    try:
        removed = assignments.remove_supervises(manager_id, staff_id)
        db.session.commit()
        if removed:
            flash("Supervision removed.", "success")
        else:
            flash("That assignment no longer exists.", "warning")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to unassign supervision %s/%s", manager_id, staff_id)
        flash("Delete failed.", "error")

    return redirect(url_for("supervision_assignments"))


@app.route("/manager/assignments/maintenance", methods=["GET", "POST"])
@manager_required
def maintenance_assignments():
    form = MaintainsForm()
    form.staff_id.choices = staff_choices()
    form.boat_id.choices = boat_choices()

    if not (form.staff_id.choices and form.boat_id.choices):
        flash("Maintenance needs at least one staff member and one boat.", "warning")
    elif form.validate_on_submit():
        try:
            assignments.add_maintains(form.staff_id.data, form.boat_id.data)
            db.session.commit()
            flash("Boat assigned for maintenance.", "success")
            return redirect(url_for("maintenance_assignments"))
        except IntegrityError:
            db.session.rollback()
            flash("That staff member already maintains that boat.", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")

    return render_template(
        "assignments_maintenance.html",
        form=form,
        rows=assignments.list_maintains(),
        delete_form=ConfirmDeleteForm(),
    )


@app.route("/manager/assignments/maintenance/<staff_id>/<boat_id>/delete", methods=["POST"])
@manager_required
def unassign_maintenance(staff_id, boat_id):
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash("Invalid delete request.", "error")
        return redirect(url_for("maintenance_assignments"))

    try:
        removed = assignments.remove_maintains(staff_id, boat_id)
        db.session.commit()
        if removed:
            flash("Maintenance assignment removed.", "success")
        else:
            flash("That assignment no longer exists.", "warning")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to unassign maintenance %s/%s", staff_id, boat_id)
        flash("Delete failed.", "error")

    return redirect(url_for("maintenance_assignments"))
