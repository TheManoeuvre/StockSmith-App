import enum

from sqlalchemy import Enum as _SAEnum
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def portable_enum(enum_cls: type[enum.Enum], name: str) -> _SAEnum:
    """A dialect-portable enum column type.

    native_enum=False renders VARCHAR + CHECK(...) on every dialect (Postgres included),
    rather than Postgres's native CREATE TYPE ... AS ENUM — so the same schema works
    unmodified on both SQLite and Postgres.
    """
    return _SAEnum(enum_cls, name=name, native_enum=False, validate_strings=True)
