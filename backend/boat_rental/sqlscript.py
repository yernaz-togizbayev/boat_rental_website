"""Reading and splitting the graded SQL scripts in ``database/``.

The schema lives in ``database/Group05_Createtable.sql`` and is normally
executed by MariaDB's Docker entrypoint, which is why nothing in the app has
ever needed to create a table. A cloud MySQL has no entrypoint, so ``init-db``
runs the same files through PyMySQL instead -- and PyMySQL sends one statement
per call, so the files have to be split first.

Splitting on ``;`` does not work. Three places in the graded SQL break it, all
of them real:

    Group05_Createtable.sql:2    -- CREATE DATABASE boat_rental;
    Group05_Createtable.sql:101  ...money became owed back and when it was
                                 actually returned; both NULL on
    Student2_InsertData_Initial.sql:24
                                 ('M2', 'S1'),  -- HR supervises S1 (Jane Doe)

The second is the dangerous one: it sits *inside* the ``Rental`` CREATE TABLE
body, so a naive split cuts that statement in half and the table is created
without its primary key or foreign keys. The third rules out the usual shortcut
of dropping lines that start with ``--``, because those comments are trailing.

So this module scans character by character and only treats ``;`` as a
terminator when it is not inside a string, an identifier or a comment. That is
enough for these four files, which contain no ``DELIMITER``, no stored
procedures, no triggers and no semicolons inside quoted strings. A ``DELIMITER``
block would defeat this -- but it would defeat ``sqlparse`` too, which is why
there is no dependency here. Stdlib only, and no imports from ``boat_rental``,
so it stays out of the circular import and can be tested on its own.
"""

import os
import re
from pathlib import Path, PurePosixPath

# The file that marks a directory as the graded SQL directory. Probing for a
# known file rather than the name "database" is what lets init-db be run from
# the repository root, from backend/, or from wherever a cloud host lands.
SCHEMA_FILE = "Group05_Createtable.sql"

# The order of the seed scripts is declared by database/init.sql, which sources
# them by their path inside the MariaDB container. Everything after this prefix
# is the path relative to database/.
CONTAINER_PREFIX = "/docker-entrypoint-initdb.d/"

_SOURCE_LINE = re.compile(r"^\s*SOURCE\s+(.+?)\s*;\s*$", re.IGNORECASE)
_CREATE_TABLE = re.compile(
    r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)", re.IGNORECASE
)


def iter_statements(sql):
    """Yield ``(raw, code)`` for each statement in *sql*.

    ``raw`` is the statement exactly as written, comments included, because
    that is what gets executed. ``code`` is the same statement with comments
    removed, which is what callers should match against -- it is the reason a
    comment mentioning CREATE TABLE cannot be mistaken for one.
    """
    raw, code = [], []
    i, n = 0, len(sql)
    # None means "not in a string"; otherwise the character that closes it.
    quote = None

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if quote is not None:
            raw.append(ch)
            code.append(ch)
            # Backslash escapes apply inside strings but not inside `identifiers`.
            if ch == "\\" and quote != "`" and i + 1 < n:
                raw.append(nxt)
                code.append(nxt)
                i += 2
                continue
            if ch == quote:
                # A doubled quote is a literal quote, not the end of the string.
                if nxt == quote:
                    raw.append(nxt)
                    code.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        # MySQL only treats "--" as a comment when whitespace or EOL follows it,
        # which is why "--" inside an expression stays code.
        if ch == "-" and nxt == "-" and (i + 2 >= n or sql[i + 2] in " \t\r\n"):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            raw.append(sql[i:end])
            i = end
            continue

        if ch == "#":
            end = sql.find("\n", i)
            end = n if end == -1 else end
            raw.append(sql[i:end])
            i = end
            continue

        if ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            end = n if end == -1 else end + 2
            raw.append(sql[i:end])
            i = end
            continue

        if ch in "'\"`":
            quote = ch
            raw.append(ch)
            code.append(ch)
            i += 1
            continue

        if ch == ";":
            statement = "".join(raw).strip()
            if "".join(code).strip():
                yield statement, "".join(code).strip()
            raw, code = [], []
            i += 1
            continue

        raw.append(ch)
        code.append(ch)
        i += 1

    # A trailing statement with no closing semicolon still counts.
    statement = "".join(raw).strip()
    if "".join(code).strip():
        yield statement, "".join(code).strip()


def split_statements(sql):
    """The executable statements in *sql*, comments and all, in file order."""
    return [raw for raw, _ in iter_statements(sql)]


def created_tables(sql):
    """Table names created by *sql*, in the order they are created.

    Matched against the comment-stripped statement and anchored at its start,
    so neither the commented-out ``CREATE DATABASE`` at the top of the schema
    nor any prose mentioning a table can be mistaken for a real one.
    """
    names = []
    for _, code in iter_statements(sql):
        found = _CREATE_TABLE.match(code)
        if found:
            names.append(found.group(1))
    return names


def database_dir():
    """Locate the graded ``database/`` directory.

    Raises rather than returning a guess: seeding nothing while reporting
    success is the worst outcome this command has.
    """
    override = os.getenv("BOAT_RENTAL_SQL_DIR")
    candidates = (
        [Path(override)]
        if override
        else [
            # backend/boat_rental/sqlscript.py -> repository root
            Path(__file__).resolve().parents[2] / "database",
            Path.cwd() / "database",
            Path.cwd().parent / "database",
        ]
    )
    for candidate in candidates:
        if (candidate / SCHEMA_FILE).is_file():
            return candidate.resolve()
    tried = "\n  ".join(str(c) for c in candidates)
    raise RuntimeError(
        f"Could not find the SQL directory (no {SCHEMA_FILE} in any of):\n  {tried}\n"
        "Set BOAT_RENTAL_SQL_DIR to the directory holding the graded SQL."
    )


def schema_file(directory):
    """Path to the CREATE TABLE script."""
    return directory / SCHEMA_FILE


def seed_files(directory):
    """The seed scripts, in the order ``init.sql`` sources them.

    The order is read from init.sql rather than hardcoded here because init.sql
    is the graded declaration of it, and it is load-bearing: Student1 owns the
    shared Office rows and Student2 must not re-insert them. A hardcoded copy
    would drift silently the first time someone reorders that file.
    """
    init = directory / "init.sql"
    if not init.is_file():
        raise RuntimeError(f"No init.sql in {directory}; cannot determine seed order.")

    files = []
    for line in init.read_text(encoding="utf-8").splitlines():
        found = _SOURCE_LINE.match(line)
        if not found:
            continue
        raw = found.group(1).strip().strip("'\"")
        relative = PurePosixPath(raw)
        text = str(relative)
        if text.startswith(CONTAINER_PREFIX):
            text = text[len(CONTAINER_PREFIX):]
        if ".." in PurePosixPath(text).parts:
            raise RuntimeError(f"Refusing to follow a path escaping database/: {raw}")
        path = directory / Path(text)
        if not path.is_file():
            raise RuntimeError(f"init.sql sources {raw}, but {path} does not exist.")
        files.append(path)

    if not files:
        raise RuntimeError(
            f"{init} declared no SOURCE lines, so there is nothing to seed. "
            "That is almost certainly wrong -- check the file."
        )
    return files
