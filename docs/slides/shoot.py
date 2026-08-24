"""Capture screenshots of the running app for the MS2 slides.

Runs inside a Playwright container on the compose network, so it reaches the
app at http://backend:5000 rather than through the host's published port:

    docker run --rm --network boat_rental_webapp_default \
      -v "$PWD/docs/slides:/out" -w /out \
      mcr.microsoft.com/playwright/python:v1.47.0-jammy \
      bash -c "pip install -q playwright==1.47.0 && python shoot.py"

Navigation is done with URLs rather than clicks wherever the route allows it.
The site header is sticky, so Playwright's click retries fight it, and the
booking page accepts its search as GET parameters anyway. Signing in is also a
GET here: /select-client/<id> for a client, and the manager picker is the one
real form we submit, which is done through the DOM to skip the same problem.
"""

import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.getenv("APP_URL", "http://backend:5000")
CLIENT = os.getenv("SHOT_CLIENT", "C1")
MANAGER = os.getenv("SHOT_MANAGER", "E8")      # a manager who supervises somebody
CITY = os.getenv("SHOT_CITY", "Dubrovnik")
START = os.getenv("SHOT_START", "")
END = os.getenv("SHOT_END", "")
# An existing unpaid rental, so checkout can be shown without booking one.
PAY_BOAT = os.getenv("SHOT_PAY_BOAT", "")
PAY_DATE = os.getenv("SHOT_PAY_DATE", "")

WIDTH, HEIGHT = 1440, 900


# Full-page shots are useful for reading; viewport shots fit a 16:9 slide.
FULL = os.getenv("SHOT_FULL", "1") not in ("0", "false", "no")


def shot(page, name, full=True):
    page.wait_for_load_state("networkidle")
    page.screenshot(path=name, full_page=full and FULL)
    size = os.path.getsize(name) // 1024
    print(f"  {name:28} {size:5} KB")


def submit_owning_form(page, field_selector):
    """Submit the form that owns this field, avoiding the sticky header.

    Targeted at the field rather than at "form": the first form on every page
    is the Generate demo data button in the site header, and submitting that
    by accident wipes and regenerates the database.

    Called through the prototype because these forms contain a field named
    "submit" (WTForms names its SubmitField that), and a form control shadows
    the method of the same name.
    """
    page.eval_on_selector(
        field_selector,
        "el => HTMLFormElement.prototype.submit.call(el.form)")
    page.wait_for_load_state("networkidle")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                device_scale_factor=2)

        print("client pages")
        page.goto(f"{BASE}/select-client/{CLIENT}")
        shot(page, "01-home.png", full=False)

        page.goto(f"{BASE}/booking?city={CITY}&start_date={START}&end_date={END}")
        shot(page, "02-booking-results.png")

        if PAY_BOAT and PAY_DATE:
            page.goto(f"{BASE}/rentals/{PAY_BOAT}/{PAY_DATE}/pay")
            shot(page, "03-checkout.png")

        page.goto(f"{BASE}/report")
        shot(page, "04-logbook.png")

        page.goto(f"{BASE}/analytics?city={CITY}&start_date={START}&end_date={END}")
        shot(page, "05-availability.png")

        print("manager pages")
        page.goto(f"{BASE}/manager/login")
        page.select_option('select[name="manager_id"]', MANAGER)
        submit_owning_form(page, 'select[name="manager_id"]')

        page.goto(f"{BASE}/manager/employees/new")
        shot(page, "06-hire-staff.png")

        page.goto(f"{BASE}/manager/assignments/supervision")
        shot(page, "07-supervision.png")

        page.goto(f"{BASE}/manager/nosql")
        shot(page, "08-nosql-console.png")

        page.goto(f"{BASE}/manager/nosql/indexes")
        shot(page, "09-nosql-indexes.png")

        browser.close()


if __name__ == "__main__":
    sys.exit(main())
