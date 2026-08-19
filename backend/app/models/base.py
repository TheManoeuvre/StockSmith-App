import enum

from sqlalchemy import Enum as _SAEnum
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def portable_enum(enum_cls: type[enum.Enum], name: str) -> _SAEnum:
    """A dialect-portable enum column type.

    native_enum=False renders a plain VARCHAR on every dialect (Postgres included), rather
    than Postgres's native CREATE TYPE ... AS ENUM — so the same schema works unmodified on
    both SQLite and Postgres.

    No CHECK constraint is emitted: SQLAlchemy 2.0 defaults Enum.create_constraint to False,
    and this deliberately does not turn it back on. Two things follow, and both have already
    caught someone out. The column is sized to the longest member, so **adding a longer value
    needs a migration widening the VARCHAR** — SQLite ignores the length and Postgres does
    not, which is exactly the shape of bug that passes every test and fails in production.
    And validate_strings=True means the *application* rejects unknown values, so a database
    written by a newer version raises LookupError on read rather than being quietly tolerated;
    a downgrade has to rewrite the offending rows before narrowing the column back.
    """
    return _SAEnum(enum_cls, name=name, native_enum=False, validate_strings=True)
