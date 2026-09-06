import logging
import sqlite3

import pytest

from manictime_pipeline.config import Profile


def create_source(path):
    path.mkdir(parents=True)
    core = sqlite3.connect(path / "ManicTimeCore.db")
    core.execute("CREATE TABLE Document (Id INTEGER PRIMARY KEY, Content TEXT)")
    core.execute("INSERT INTO Document VALUES (1, 'source')")
    core.commit()
    core.close()
    db = sqlite3.connect(path / "ManicTimeReports.db")
    db.executescript("""
    CREATE TABLE Ar_Timeline (ReportId INT NOT NULL PRIMARY KEY, TimelineKey TEXT,
                              SchemaName TEXT);
    CREATE TABLE Ar_Group (ReportId INT NOT NULL, GroupId INT NOT NULL, Name TEXT,
                           CommonId INT, Icon16 BLOB, PRIMARY KEY (ReportId, GroupId));
    CREATE TABLE Ar_CommonGroup (CommonId INT NOT NULL PRIMARY KEY, Name TEXT, Icon32 BLOB);
    CREATE TABLE Ar_Activity (
      ReportId INT NOT NULL, ActivityId INT NOT NULL, Name TEXT NOT NULL, GroupId INT,
      StartLocalTime DATETIME NOT NULL, StartUtcTime DATETIME NOT NULL,
      EndLocalTime DATETIME NOT NULL, EndUtcTime DATETIME NOT NULL,
      CurrentChangeSequence INT, Other TEXT, PRIMARY KEY (ReportId, ActivityId));
    CREATE TABLE Ar_ApplicationByDay (ReportId INT, TotalSeconds INT);
    CREATE TABLE Ar_GroupList (ReportId INT, GroupListId INT, Name TEXT,
                               PRIMARY KEY (ReportId, GroupListId));
    CREATE TABLE Ar_GroupListItem (ReportId INT, GroupListId INT, GroupId INT,
                                   PRIMARY KEY (ReportId, GroupListId, GroupId));
    INSERT INTO Ar_Timeline VALUES (3, 'applications-key', 'ManicTime/Applications');
    INSERT INTO Ar_Timeline VALUES (4, 'documents-key', 'ManicTime/Documents');
    INSERT INTO Ar_Group VALUES (3, 1, 'Browser', 1, X'0080FF');
    INSERT INTO Ar_CommonGroup VALUES (1, 'Browser', X'89ABCDEF');
    """)
    for report, aid, start, name in [
        (3, 1, "2026-01-10", '日本語 "title",\nsecond line'),
        (3, 2, "2026-02-10", r"\N"),
        (4, 1, "2026-02-11", "=SUM(A1:A2)"),
    ]:
        db.execute(
            "INSERT INTO Ar_Activity VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                report,
                aid,
                name,
                1 if report == 3 else None,
                start + " 10:00:00",
                start + " 01:00:00",
                start + " 10:01:00",
                start + " 01:01:00",
                1,
                None,
            ),
        )
    db.commit()
    db.close()


@pytest.fixture
def profile(tmp_path):
    source = tmp_path / "source"
    create_source(source)
    return Profile(
        "test",
        "Test PC",
        source,
        tmp_path / "raw",
        tmp_path / "processed" / "Test PC",
        tmp_path / "state" / "test",
        10,
        100,  # These extraction fixtures intentionally exercise deletions; guard tests set 10.
    )


@pytest.fixture(autouse=True)
def restore_logging_after_cli_tests():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers = handlers
    root.setLevel(level)
