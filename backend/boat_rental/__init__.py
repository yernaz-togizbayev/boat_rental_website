from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf

import os

# Load the repo-root .env so running Python outside Docker picks up the same
# settings Compose injects. Existing env vars win, so the container (which has
# no .env file) is unaffected. The try/except is there so the app still starts
# on an image built before python-dotenv was added to requirements.txt.
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv())
except ImportError:  # pragma: no cover
    pass


app = Flask(__name__, template_folder="../templates", static_folder="../static")
csrf = CSRFProtect(app)

app.secret_key = os.getenv("SECRET_KEY", "dev")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL") or (
    f"mysql+pymysql://{os.getenv('DB_USER', 'user')}:"
    f"{os.getenv('DB_PASSWORD', 'pass')}@"
    f"{os.getenv('DB_HOST', 'db')}:"
    f"{os.getenv('DB_PORT', '3306')}/"
    f"{os.getenv('DB_NAME', 'boatdb')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Session cookie flags. HttpOnly is already Flask's default; spelling it out
# keeps all three in one place. SameSite=Lax keeps the cookie off cross-site
# requests, backing up CSRFProtect. Secure stays off by default because we
# serve plain HTTP on localhost, where the browser would never send the cookie
# at all. Set SESSION_COOKIE_SECURE=1 when running behind TLS.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "") in ("1", "true", "True")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
}
db = SQLAlchemy(app)

@app.context_processor
def inject_csrf():
    return dict(csrf_token=generate_csrf)


@app.context_processor
def inject_unsplash():
    """Unsplash attribution link, used by the footer on every page.

    The import sits inside the function because images.py is a sibling
    module, and importing it at the top would recreate the circular import
    that keeps `routes` pinned to the bottom of this file.
    """
    from boat_rental.images import UNSPLASH_HOME
    return dict(unsplash_home=UNSPLASH_HOME)


@app.template_filter("money")
def format_money(amount):
    """Euro with thousands separators, or an em dash when there is no price.

    DailyRate is nullable and a manager can leave it blank, so "no price" is a
    real state rather than an error. Five templates render money, and this is
    how they all agree on what a missing one looks like.
    """
    if amount is None:
        return "—"
    return f"€{amount:,.2f}"

from boat_rental import routes  # noqa: E402, F401
