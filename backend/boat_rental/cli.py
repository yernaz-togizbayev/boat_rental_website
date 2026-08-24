"""``flask init-db`` -- build the schema and seed it from the graded SQL.

Locally the database is built by MariaDB's Docker entrypoint, which runs
everything in ``database/`` on first boot. That is why nothing else in this app
creates a table, and it is exactly what a managed cloud MySQL does not do: a
fresh cloud database stays empty and the app dies on its first query.

This command is the missing entrypoint. It runs the *same graded SQL* against
whatever ``DATABASE_URL`` points at, rather than reimplementing the schema --
``db.create_all()`` deliberately does not appear here. ``models.py`` is a
hand-maintained mirror of ``Group05_Createtable.sql``, and making the app able
to create its own tables is precisely the drift that would let the two
diverge unnoticed.

Usage, from ``backend/``::

    flask --app app init-db                 # create, then seed if empty
    flask --app app init-db --reset --yes   # drop everything and rebuild
    flask --app app init-db --dry-run       # say what it would do

Against a cloud database, set DATABASE_URL in the environment first; a variable
already set in the shell beats the repo ``.env``, because ``load_dotenv()`` does
not override.
"""

import click
from sqlalchemy import text

from boat_rental import app, db, sqlscript


def _run_script(path, label):
    """Execute one SQL file in a single transaction. Returns the statement count.

    One transaction per *file* rather than per run: if a seed fails, the files
    that already succeeded stay committed and the error names the file that
    broke, which is the difference between a two-second fix and a bisect.
    """
    statements = sqlscript.split_statements(path.read_text(encoding="utf-8"))
    with db.engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
    click.echo(f"  {label:<44} {len(statements):>3} statements")
    return len(statements)


def _office_count():
    """Rows in Office, or None if the table is not there yet."""
    try:
        with db.engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM Office")).scalar()
    except Exception:  # noqa: BLE001 - a missing table is the expected case
        return None


@app.cli.command("init-db")
@click.option("--reset", is_flag=True,
              help="Drop every table the schema creates, then rebuild and reseed.")
@click.option("--yes", is_flag=True, help="Skip the --reset confirmation.")
@click.option("--dry-run", is_flag=True, help="Report the plan; change nothing.")
def init_db(reset, yes, dry_run):
    """Create the schema from the graded SQL and seed it."""
    sql_dir = sqlscript.database_dir()
    schema_path = sqlscript.schema_file(sql_dir)
    seeds = sqlscript.seed_files(sql_dir)

    # Echoed before anything is touched: the seed order is load-bearing (Student1
    # owns the shared Office rows) and it is read out of init.sql, so showing it
    # is how a human confirms it without reading code.
    click.echo(f"SQL directory: {sql_dir}")
    click.echo(f"Target:        {db.engine.url.render_as_string(hide_password=True)}")
    click.echo(f"Schema:        {schema_path.name}")
    click.echo("Seed order:    " + " -> ".join(p.name for p in seeds))

    existing = _office_count()
    click.echo(
        "Current state: "
        + ("no Office table yet" if existing is None
           else f"{existing} office row(s) present")
    )

    if dry_run:
        click.echo("\n--dry-run: nothing was executed.")
        return

    if reset:
        tables = sqlscript.created_tables(schema_path.read_text(encoding="utf-8"))
        if not yes:
            click.confirm(
                f"\nDrop and rebuild {len(tables)} tables? All data is lost.",
                abort=True,
            )
        click.echo("\nDropping:")
        mysql = db.engine.dialect.name == "mysql"
        with db.engine.begin() as conn:
            # The subclass and join tables reference their parents, so drop in
            # reverse creation order. FK checks go off anyway because the order
            # alone cannot satisfy Manager.SupervisorID, which points at itself.
            if mysql:
                conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in reversed(tables):
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
            if mysql:
                conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        click.echo(f"  {len(tables)} tables dropped")
        existing = 0

    click.echo("\nSchema:")
    _run_script(schema_path, schema_path.name)

    # Every seed is a plain INSERT, so a second run would die on duplicate keys.
    # Office is the right thing to test: it is the first table the first seed
    # writes, and generate_data() deliberately keeps offices, so a database that
    # has only been demo-refilled still correctly reads as already seeded.
    if existing:
        click.echo(
            f"\nSeeds:\n  skipped -- {existing} office row(s) already present."
            "\n  Use --reset to drop everything and reseed."
        )
        return

    click.echo("\nSeeds:")
    total = sum(_run_script(path, path.name) for path in seeds)
    click.echo(f"\nDone. {total} seed statements applied.")
