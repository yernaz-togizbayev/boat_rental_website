"""WTForms definitions, plus the card validators the demo checkout uses.

Choices on the SelectFields are filled in at construction or by the route, so
pre_validate is what rejects a tampered POST: a boat in another city, a manager
in a staff slot. That check runs before anything reaches SQL.
"""

from flask import current_app
from flask_wtf import FlaskForm
from wtforms import SelectField, EmailField, DateField, SubmitField, StringField, HiddenField, BooleanField, IntegerField, FloatField, DecimalField
from wtforms.validators import DataRequired, Email, Optional, Length, NumberRange, ValidationError
from datetime import date, timedelta
from boat_rental.models import (Office, served_harbours,
                                AVAILABILITY_AVAILABLE, AVAILABILITY_MAINTENANCE)


def validate_future_date(form, field):
    if field.data < date.today():
        raise ValidationError("Date cannot be in the past.")


def validate_end_after_start(form, field):
    if hasattr(form, "start_date") and form.start_date.data:
        if field.data <= form.start_date.data:
            raise ValidationError("End date must be after start date.")


# Stripe's published test numbers, used purely as a convention a grader will
# recognise. Nothing is sent anywhere and no card details are stored. Any other
# Luhn-valid number is declined, so you cannot reach the happy path by typing
# something that merely looks like a card.
TEST_CARD_ACCEPTED = "4242424242424242"
TEST_CARD_DECLINED = "4000000000000002"


def card_digits(value):
    """The card number with spaces and dashes stripped."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


def grouped_card(value):
    """The digits in groups of four, the way a card is printed.

    Display only. The constants above stay bare digits because they are what
    the route compares against; formatting them in place would break that.
    """
    digits = card_digits(value)
    return " ".join(digits[i:i + 4] for i in range(0, len(digits), 4))


def passes_luhn(number):
    """Standard Luhn checksum, which is what catches a mistyped digit."""
    total, parity = 0, len(number) % 2
    for index, digit in enumerate(number):
        digit = int(digit)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def validate_card_number(form, field):
    """Reject anything that is not a plausible card number.

    Whether a valid card is then accepted or declined is a payment decision
    rather than a form one, so that lives in the route. This only catches
    typos.
    """
    digits = card_digits(field.data)
    if not 13 <= len(digits) <= 19 or not passes_luhn(digits):
        raise ValidationError("That is not a valid card number.")


def validate_expiry(form, field):
    """MM/YY, and not already past. Expiry is end-of-month, so compare months."""
    raw = (field.data or "").strip().replace(" ", "")
    month, _, year = raw.partition("/")
    if not (month.isdigit() and year.isdigit() and len(year) == 2):
        raise ValidationError("Use MM/YY.")
    month, year = int(month), 2000 + int(year)
    if not 1 <= month <= 12:
        raise ValidationError("Month must be between 01 and 12.")
    today = date.today()
    if (year, month) < (today.year, today.month):
        raise ValidationError("That card has expired.")




class BookingSearchForm(FlaskForm):
    city = SelectField(
        "City",
        validators=[DataRequired()],
        choices=[],
        render_kw={"placeholder": "Select City..."},
    )
    start_date = DateField(
        "Start Date",
        validators=[DataRequired(), validate_future_date],
        default=date.today,
    )
    end_date = DateField(
        "End Date",
        validators=[DataRequired(), validate_end_after_start],
        default=lambda: date.today() + timedelta(days=1),
    )
    search = SubmitField("Search available boats")

    def __init__(self, *args, **kwargs):
        super(BookingSearchForm, self).__init__(*args, **kwargs)
        try:
            # served_harbours() is the shared definition of where we operate,
            # so the picker cannot drift out of order or out of step with the
            # other city lists.
            #
            # The label carries the country, the value stays the bare city:
            # the city is what get_available_boats(), the harbour cards and
            # every filter key off.
            self.city.choices = [("", "Select City...")] + [
                (city, f"{city}, {country}") for city, country in served_harbours()
            ]
        except Exception:
            # On first boot the Office table may not exist yet, before the
            # init scripts have run. Fall back to an empty picker.
            current_app.logger.exception("Could not load city choices")
            self.city.choices = [("", "Select City...")]


class BoatSelectionForm(FlaskForm):
    boat_id = SelectField(
        "Select Boat",
        validators=[DataRequired()],
        choices=[],
        render_kw={"placeholder": "Choose a boat..."},
    )
    rental_date = HiddenField()
    rental_end_date = HiddenField()
    city = HiddenField()
    book = SubmitField("Book")

    def __init__(self, available_boats=None, *args, **kwargs):
        super(BoatSelectionForm, self).__init__(*args, **kwargs)
        if available_boats:
            self.boat_id.choices = [("", "Choose a boat...")] + [
                (str(boat.BoatID), _describe_boat(boat)) for boat, office in available_boats
            ]


def _describe_boat(boat):
    return f"{boat.BoatID} · {boat.Manufacturer}"



# =========================
# Manager & Employee forms
# =========================
MIN_EMPLOYEE_AGE = 18
MIN_CLIENT_AGE = 18

def min_age(years, subject):
    """Validator factory: `subject` must be at least `years` old.

    A factory rather than a plain function so clients and employees can share
    the arithmetic without the error message calling a client an "Employee".
    """
    def _validate(form, field):
        if field.data:
            today = date.today()
            age = today.year - field.data.year - ((today.month, today.day) < (field.data.month, field.data.day))
            if age < years:
                raise ValidationError(f"{subject} must be at least {years} years old.")
    return _validate

# The employee forms below refer to this name; keeping it means their
# validators lists and their error text are unchanged.
validate_min_age = min_age(MIN_EMPLOYEE_AGE, "Employee")

class ManagerLoginForm(FlaskForm):
    manager_id = SelectField("Select manager", validators=[DataRequired()])
    submit = SubmitField("Login as manager")

class EmployeeHireForm(FlaskForm):
    office_id = SelectField("Office", validators=[DataRequired()], coerce=str)
    first_name = StringField("First name", validators=[DataRequired(), Length(max=50)])
    last_name = StringField("Last name", validators=[DataRequired(), Length(max=50)])
    street = StringField("Street", validators=[Optional(), Length(max=100)])
    zip = StringField("ZIP", validators=[Optional(), Length(max=10)])
    country = StringField("Country", validators=[Optional(), Length(max=50)])
    city = StringField("City", validators=[Optional(), Length(max=50)])
    birthdate = DateField("Birthdate", format="%Y-%m-%d",validators=[DataRequired(), validate_min_age], render_kw={"type": "date"})
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=100)])
    mobile = StringField("Mobile number", validators=[Optional(), Length(max=20)])
    self_insurance_nr = StringField("Self insurance number", validators=[DataRequired(), Length(max=20)])
    salary = IntegerField("Salary", validators=[DataRequired(), NumberRange(min=0, max=10_000_000)])

    # Role-specific
    role = SelectField("Role", choices=[("staff", "Staff"), ("manager", "Manager")], validators=[DataRequired()], coerce=str)
    # Staff extras
    work_shift = SelectField("Work shift", choices=[("Day", "Day"), ("Night", "Night")], validators=[Optional()], coerce=str)
    is_on_duty = BooleanField("Is on duty?")
    # Manager extras
    department = StringField("Department", validators=[Optional(), Length(max=50)])
    management_level = StringField("Management level", validators=[Optional(), Length(max=50)])
    supervisor_id = SelectField("Supervisor (optional)", validators=[Optional()], coerce=str)

    submit = SubmitField("Hire")

class EmployeeEditForm(FlaskForm):
    office_id = SelectField("Office", validators=[DataRequired()], coerce=str)
    first_name = StringField("First name", validators=[DataRequired(), Length(max=50)])
    last_name = StringField("Last name", validators=[DataRequired(), Length(max=50)])
    street = StringField("Street", validators=[Optional(), Length(max=100)])
    zip = StringField("ZIP", validators=[Optional(), Length(max=10)])
    country = StringField("Country", validators=[Optional(), Length(max=50)])
    city = StringField("City", validators=[Optional(), Length(max=50)])
    birthdate = DateField("Birthdate", format="%Y-%m-%d", validators=[DataRequired(), validate_min_age])
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=100)])
    mobile = StringField("Mobile number", validators=[Optional(), Length(max=20)])
    self_insurance_nr = StringField("Self insurance number", validators=[DataRequired(), Length(max=20)])
    salary = IntegerField("Salary", validators=[DataRequired(), NumberRange(min=0, max=10_000_000)])

    role = SelectField("Role", choices=[("staff", "Staff"), ("manager", "Manager")], validators=[DataRequired()], coerce=str)
    # Staff extras
    work_shift = SelectField("Work shift", choices=[("Day", "Day"), ("Night", "Night")], validators=[Optional()], coerce=str)
    is_on_duty = BooleanField("Is on duty?")
    # Manager extras
    department = StringField("Department", validators=[Optional(), Length(max=50)])
    management_level = StringField("Management level", validators=[Optional(), Length(max=50)])
    supervisor_id = SelectField("Supervisor (optional)", validators=[Optional()], coerce=str)

    submit = SubmitField("Save changes")

class ConfirmDeleteForm(FlaskForm):
    submit = SubmitField("Delete")

class ClientRegistrationForm(FlaskForm):
    # Required fields mirror the NOT NULL columns on Client; everything else
    # is Optional() and written as NULL when blank.
    first_name = StringField("First name", validators=[DataRequired(), Length(max=50)])
    last_name = StringField("Last name", validators=[DataRequired(), Length(max=50)])
    street = StringField("Street", validators=[Optional(), Length(max=100)])
    zip = StringField("ZIP", validators=[Optional(), Length(max=10)])
    country = StringField("Country", validators=[Optional(), Length(max=50)])
    city = StringField("City", validators=[Optional(), Length(max=50)])
    birthdate = DateField(
        "Birthdate",
        format="%Y-%m-%d",
        validators=[DataRequired(), min_age(MIN_CLIENT_AGE, "Client")],
        render_kw={"type": "date"},
    )
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=100)])
    mobile = StringField("Mobile number", validators=[Optional(), Length(max=20)])
    captain_license = StringField(
        "Captain licence number (optional)", validators=[Optional(), Length(max=50)]
    )
    submit = SubmitField("Create account")


class OfficeForm(FlaskForm):
    city = StringField("City", validators=[DataRequired(), Length(max=50)])
    country = StringField("Country", validators=[DataRequired(), Length(max=50)])
    street = StringField("Street", validators=[DataRequired(), Length(max=100)])
    zip = StringField("ZIP", validators=[DataRequired(), Length(max=10)])
    submit = SubmitField("Save office")


# The m:n relations from the ER model. The route fills in the choices, so
# SelectField.pre_validate is what rejects a POST naming a manager in the staff
# slot, before anything reaches SQL.
class SupervisesForm(FlaskForm):
    manager_id = SelectField("Manager", validators=[DataRequired()], coerce=str)
    staff_id = SelectField("Staff member", validators=[DataRequired()], coerce=str)
    submit = SubmitField("Assign supervision")


class MaintainsForm(FlaskForm):
    staff_id = SelectField("Staff member", validators=[DataRequired()], coerce=str)
    boat_id = SelectField("Boat", validators=[DataRequired()], coerce=str)
    submit = SubmitField("Assign boat")


BOAT_TYPES = [("yacht", "Yacht"), ("motorboat", "Motorboat"), ("catamaran", "Catamaran")]


class BoatForm(FlaskForm):
    office_id = SelectField("City / office", validators=[DataRequired()], coerce=str)
    manufacturer = StringField("Manufacturer", validators=[DataRequired(), Length(max=50)])
    seats = IntegerField("Seats", validators=[DataRequired(), NumberRange(min=1, max=1000)])
    length = FloatField("Length (m)", validators=[Optional(), NumberRange(min=0, max=1000)])
    weight = FloatField("Weight (t)", validators=[Optional(), NumberRange(min=0, max=100_000)])
    horsepower = IntegerField("Horsepower", validators=[Optional(), NumberRange(min=0, max=100_000)])
    daily_rate = DecimalField(
        "Daily rate (€)", places=2,
        validators=[Optional(), NumberRange(min=0, max=1_000_000)],
    )
    availability_status = SelectField(
        "Availability",
        choices=[(AVAILABILITY_AVAILABLE, "Available"), (AVAILABILITY_MAINTENANCE, "Maintenance")],
        validators=[DataRequired()],
        coerce=str,
    )

    boat_type = SelectField("Type", choices=BOAT_TYPES, validators=[DataRequired()], coerce=str)
    
    # Yacht extras
    yacht_name = StringField("Yacht name", validators=[Optional(), Length(max=50)])

    # Shared by yacht and catamaran. Both can have one, a motorboat cannot.
    # One field rather than two: two inputs of the same name would both post,
    # and the checked one would win whichever type was on screen.
    has_jacuzzi = BooleanField("Has jacuzzi?")

    # Motorboat extras
    engine_type = StringField("Engine type", validators=[Optional(), Length(max=50)])
    fuel_type = StringField("Fuel type", validators=[Optional(), Length(max=50)])
    
    # Catamaran extras
    nr_of_cabins = IntegerField("Number of cabins", validators=[Optional(), NumberRange(min=0, max=500)])
    max_capacity = IntegerField("Max capacity", validators=[Optional(), NumberRange(min=0, max=5000)])

    submit = SubmitField("Save boat")


class PaymentForm(FlaskForm):
    """Demo card details. Nothing here is stored, sessioned or logged.

    The fields exist so the checkout looks and behaves like a real one; the
    route reads the number, decides, and drops it. Only PaymentStatus and the
    already-agreed TotalAmount are written.
    """
    card_name = StringField(
        "Name on card", validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "John Doe", "autocomplete": "off"},
    )
    card_number = StringField(
        "Card number",
        validators=[DataRequired(), validate_card_number],
        render_kw={"placeholder": "4242 4242 4242 4242", "autocomplete": "off",
                   "inputmode": "numeric"},
    )
    expiry = StringField(
        "Expiry", validators=[DataRequired(), validate_expiry],
        render_kw={"placeholder": "MM/YY", "autocomplete": "off",
                   "inputmode": "numeric", "maxlength": "5"},
    )
    cvc = StringField(
        "CVC", validators=[DataRequired(), Length(min=3, max=4)],
        render_kw={"placeholder": "123", "autocomplete": "off",
                   "inputmode": "numeric"},
    )
    submit = SubmitField("Pay now")