"""MongoDB side of the application: migration, the two reports, and indexes.

The relational schema is normalised across ten tables. Answering either use
case there means joining four or five of them. The document model instead
stores each use case's answer in one shape:

    offices   one document per harbour, with its boats embedded, and each
              boat's rentals embedded inside it. Student 1's report is then a
              single-collection query.
    managers  one document per manager, with the staff they supervise
              embedded. Student 2's report becomes a primary-key lookup.
    clients   one document per client, with their own rentals embedded, which
              is the client logbook.

Rentals are therefore written twice, under the boat and under the client. That
is a deliberate trade: both reads are single-document, and the cost is that a
booking has to be written in two places. Nothing in the app writes to Mongo
during normal use, so that cost falls entirely on the migration.

Migration is one-way. MariaDB stays the system of record; this rebuilds the
collections from it and never writes back.
"""

import os

from pymongo import ASCENDING, MongoClient

from boat_rental.models import (
    Boat,
    Client,
    Employee,
    Manager,
    Office,
    Rental,
    Staff,
)
from boat_rental import db

# The three collections the migration owns. Listed here so clearing them and
# reporting on them cannot drift apart.
COLLECTIONS = ("offices", "managers", "clients")

_client = None


def get_db():
    """The Mongo database handle, opened once per process.

    MongoClient is lazy: it does not connect until a command runs, so building
    it at import time would hide a dead server until the first query.
    """
    global _client
    if _client is None:
        _client = MongoClient(
            os.getenv("MONGO_URI", "mongodb://mongo:27017/"),
            serverSelectionTimeoutMS=3000,
        )
    return _client[os.getenv("MONGO_DB", "boatdb")]


def available() -> bool:
    """Whether Mongo is reachable, so a page can say so instead of erroring."""
    try:
        get_db().command("ping")
        return True
    except Exception:
        return False


def _boat_type(boat):
    if boat.yacht:
        return "yacht"
    if boat.motorboat:
        return "motorboat"
    if boat.catamaran:
        return "catamaran"
    return "boat"


def migrate():
    """Rebuild every collection from the relational data. Returns the counts.

    Clears first, so running it twice leaves the same result rather than
    doubling anything. Reads through the existing SQLAlchemy models, which
    keeps one definition of what a boat or a rental is.
    """
    mongo = get_db()
    for name in COLLECTIONS:
        mongo[name].delete_many({})

    rentals_by_boat = {}
    rentals_by_client = {}
    for rental in Rental.query.all():
        entry = {
            "clientId": rental.ClientID,
            "boatId": rental.BoatID,
            "rentalDate": rental.RentalDate.isoformat(),
            "rentalEndDate": (rental.RentalEndDate.isoformat()
                              if rental.RentalEndDate else None),
            "paymentStatus": rental.PaymentStatus,
            # Decimal is not a BSON type. Stored as a string for the same
            # reason the column is DECIMAL: a float cannot hold cents exactly.
            "totalAmount": (str(rental.TotalAmount)
                            if rental.TotalAmount is not None else None),
        }
        rentals_by_boat.setdefault(rental.BoatID, []).append(entry)
        rentals_by_client.setdefault(rental.ClientID, []).append(entry)

    boats_by_office = {}
    for boat in Boat.query.all():
        boats_by_office.setdefault(boat.OfficeID, []).append({
            "boatId": boat.BoatID,
            "manufacturer": boat.Manufacturer,
            "type": _boat_type(boat),
            "length": boat.Length,
            "seats": boat.Seats,
            "availabilityStatus": boat.AvailabilityStatus,
            "dailyRate": str(boat.DailyRate) if boat.DailyRate is not None else None,
            "jacuzzi": boat.jacuzzi,
            "rentals": rentals_by_boat.get(boat.BoatID, []),
        })

    offices = [{
        "_id": office.OfficeID,
        "city": office.City,
        "country": office.Country,
        "street": office.Street,
        "zip": office.ZIP,
        "boats": boats_by_office.get(office.OfficeID, []),
    } for office in Office.query.all()]

    # Employee carries the shared columns; Staff and Manager are the IS-A
    # children. The document model folds the parent into each child, because
    # nothing asks for "an employee" without knowing which kind it is.
    employees = {e.EmployeeID: e for e in Employee.query.all()}
    offices_by_id = {o.OfficeID: o for o in Office.query.all()}

    def person(emp_id):
        emp = employees.get(emp_id)
        if emp is None:
            return None
        office = offices_by_id.get(emp.OfficeID)
        return {
            "employeeId": emp.EmployeeID,
            "name": {"first": emp.FirstName, "last": emp.LastName},
            "email": emp.Email,
            "office": {"officeId": emp.OfficeID,
                       "city": office.City if office else None,
                       "country": office.Country if office else None},
        }

    supervised = {}
    for manager_id, staff_id in db.session.execute(
            db.text("SELECT `ManagerID`, `StaffID` FROM `Supervises`")):
        supervised.setdefault(manager_id, []).append(staff_id)

    staff_rows = {s.StaffID: s for s in Staff.query.all()}
    managers = []
    for manager in Manager.query.all():
        base = person(manager.ManagerID)
        if base is None:
            continue
        team = []
        for staff_id in supervised.get(manager.ManagerID, []):
            member, row = person(staff_id), staff_rows.get(staff_id)
            if member and row:
                member["workShift"] = row.WorkShift
                member["isOnDuty"] = bool(row.IsOnDuty)
                team.append(member)
        managers.append({
            "_id": manager.ManagerID,
            **base,
            "department": manager.Department,
            "managementLevel": manager.ManagementLevel,
            "supervisorId": manager.SupervisorID,
            "supervisedStaff": team,
        })

    clients = [{
        "_id": c.ClientID,
        "name": {"first": c.FirstName, "last": c.LastName},
        "email": c.Email,
        "city": c.City,
        "country": c.Country,
        "captainLicenseNumber": c.CaptainLicenseNumber,
        "rentals": rentals_by_client.get(c.ClientID, []),
    } for c in Client.query.all()]

    if offices:
        mongo.offices.insert_many(offices)
    if managers:
        mongo.managers.insert_many(managers)
    if clients:
        mongo.clients.insert_many(clients)

    ensure_indexes()
    return {"offices": len(offices), "managers": len(managers),
            "clients": len(clients),
            "boats": sum(len(o["boats"]) for o in offices),
            "rentals": sum(len(v) for v in rentals_by_boat.values())}


def ensure_indexes():
    """The indexes the two reports need. Idempotent.

    offices.city serves Student 1's report, which selects a harbour by name.
    managers.supervisedStaff.employeeId serves the reverse of Student 2's report:
    the report itself looks a manager up by _id, which Mongo already indexes,
    but "who supervises this staff member" would otherwise scan every manager.
    """
    mongo = get_db()
    mongo.offices.create_index([("city", ASCENDING)], name="city_idx")
    mongo.managers.create_index([("supervisedStaff.employeeId", ASCENDING)],
                                name="supervised_staff_idx")


def drop_indexes():
    """Remove the two indexes, so their effect can be measured without them."""
    mongo = get_db()
    for collection, name in (("offices", "city_idx"),
                             ("managers", "supervised_staff_idx")):
        try:
            mongo[collection].drop_index(name)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Student 1: boats free in a harbour over a date range
# ---------------------------------------------------------------------------

def available_boats_pipeline(city, start_date, end_date):
    """Aggregation for Student 1's report, as a list so it can be printed.

    Same rule as the SQL: the boat must be in this harbour, not under
    maintenance, priced, and free of any rental overlapping the window. A
    cancelled rental does not count as an overlap, matching
    rental_overlap_filter() on the relational side.
    """
    start, end = start_date.isoformat(), end_date.isoformat()
    return [
        {"$match": {"city": city}},
        {"$unwind": "$boats"},
        {"$match": {"boats.availabilityStatus": "Available",
                    "boats.dailyRate": {"$ne": None}}},
        {"$addFields": {
            "clashes": {"$filter": {
                "input": "$boats.rentals",
                "as": "r",
                "cond": {"$and": [
                    {"$lt": ["$$r.rentalDate", end]},
                    {"$or": [{"$eq": ["$$r.rentalEndDate", None]},
                             {"$gt": ["$$r.rentalEndDate", start]}]},
                    {"$ne": ["$$r.paymentStatus", "CANCELLED"]},
                ]},
            }},
        }},
        {"$match": {"clashes": {"$size": 0}}},
        {"$project": {"_id": 0, "boatId": "$boats.boatId",
                      "manufacturer": "$boats.manufacturer",
                      "type": "$boats.type", "seats": "$boats.seats",
                      "length": "$boats.length", "jacuzzi": "$boats.jacuzzi",
                      "dailyRate": "$boats.dailyRate",
                      "city": "$city", "country": "$country"}},
        {"$sort": {"manufacturer": ASCENDING, "boatId": ASCENDING}},
    ]


def available_boats(city, start_date, end_date):
    return list(get_db().offices.aggregate(
        available_boats_pipeline(city, start_date, end_date)))


# ---------------------------------------------------------------------------
# Student 2: the staff a manager supervises
# ---------------------------------------------------------------------------

def supervised_staff_filter(manager_id):
    """Filter for Student 2's report, as a dict so it can be printed."""
    return {"_id": manager_id}


def supervised_staff(manager_id):
    """One document, already carrying the whole team.

    The relational version joins Supervises, Staff, Employee twice, Manager
    and Office. Here the answer is the document itself.
    """
    doc = get_db().managers.find_one(supervised_staff_filter(manager_id))
    if doc is None:
        return None
    return {
        "managerId": doc["_id"],
        "manager": f"{doc['name']['first']} {doc['name']['last']}",
        "department": doc.get("department"),
        "office": doc.get("office", {}),
        "staff": doc.get("supervisedStaff", []),
    }


def manager_of(staff_id):
    """Which manager supervises this staff member.

    The reverse of the report, and the query the second index exists for.
    """
    doc = get_db().managers.find_one({"supervisedStaff.employeeId": staff_id},
                                     {"name": 1})
    return doc


# ---------------------------------------------------------------------------
# Index statistics for the report
# ---------------------------------------------------------------------------

def explain_stats(collection, command, with_index):
    """(docsExamined, plan, indexName) for one query, as explain() reports it.

    `command` is either an aggregation pipeline or a find filter. Used by the
    NoSQL page to show the same query with and without its index.
    """
    mongo = get_db()
    if not with_index:
        drop_indexes()
    else:
        ensure_indexes()

    if isinstance(command, list):
        plan = mongo.command("explain", {"aggregate": collection,
                                         "pipeline": command, "cursor": {}},
                             verbosity="executionStats")
    else:
        plan = mongo.command("explain", {"find": collection, "filter": command},
                             verbosity="executionStats")

    stats = plan.get("executionStats") or {}
    stage = stats.get("executionStages") or {}
    # An aggregation nests its cursor stats one level down.
    if not stats and "stages" in plan:
        cursor = plan["stages"][0].get("$cursor", {})
        stats = cursor.get("executionStats", {})
        stage = stats.get("executionStages", {})

    def find_stage(node, wanted):
        if not isinstance(node, dict):
            return None
        if node.get("stage") == wanted:
            return node
        for key in ("inputStage", "executionStages"):
            hit = find_stage(node.get(key), wanted)
            if hit:
                return hit
        return None

    ixscan = find_stage(stage, "IXSCAN")
    return {
        "docsExamined": stats.get("totalDocsExamined"),
        "keysExamined": stats.get("totalKeysExamined"),
        "returned": stats.get("nReturned"),
        "millis": stats.get("executionTimeMillis"),
        "stage": (stage or {}).get("stage"),
        "indexName": (ixscan or {}).get("indexName"),
    }
