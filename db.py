"""SQLite baza: guruhlar holati, o'quvchi biriktirishlari, kelmaganlar, xatoliklar."""
import sqlite3

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS groups(
    id INTEGER PRIMARY KEY, name TEXT, office TEXT, assignee TEXT, status TEXT,
    begin TEXT, end TEXT, teacher_ids TEXT, current_teachers TEXT, teacher_names TEXT,
    schedule TEXT, students_count INTEGER, description TEXT,
    teacher_changes INTEGER DEFAULT 0, present INTEGER DEFAULT 1, updated_at TEXT);
CREATE TABLE IF NOT EXISTS links(
    edunit_id INTEGER, student_id INTEGER, student_name TEXT, begin TEXT, end TEXT,
    status TEXT, first_seen TEXT, PRIMARY KEY(edunit_id, student_id));
CREATE TABLE IF NOT EXISTS absences(
    edunit_id INTEGER, student_id INTEGER, day TEXT, student_name TEXT, group_name TEXT,
    office TEXT, assignee TEXT, lesson_time TEXT, pass INTEGER, description TEXT,
    first_seen TEXT, alerted INTEGER DEFAULT 0, PRIMARY KEY(edunit_id, student_id, day));
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, office TEXT, assignee TEXT,
    group_id INTEGER, group_name TEXT, details TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS jobs(name TEXT, day TEXT, PRIMARY KEY(name, day));
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    conn.commit()
