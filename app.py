"""
Research Management System - Flask REST API
Database: MySQL 9.7
Schema: researchmanagement

Install:
    pip install flask flask-cors pymysql python-dotenv

Set environment variables (recommended):
    DB_HOST=127.0.0.1
    DB_PORT=3306
    DB_USER=root
    DB_PASSWORD=your_mysql_password
    DB_NAME=researchmanagement

Run:
    python app.py

API:
    GET    /api/health
    GET    /api/tables
    GET    /api/table/<table>
    POST   /api/table/<table>
    DELETE /api/table/<table>/<id_column>/<id_value>
    GET    /api/viva-query
"""

import os
from decimal import Decimal
from datetime import date, datetime

import pymysql
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "researchmanagement"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": True,
}

TABLES = {
    "department": "Dept_ID",
    "researcher": "Researcher_ID",
    "project": "Project_ID",
    "investigator": "Investigator_ID",
    "funding_agency": "Agency_ID",
    "research_grant": "Grant_ID",
    "milestone": "Milestone_ID",
    "expenditure": "Expenditure_ID",
    "publication": "Publication_ID",
    "patent": "Patent_ID",
    "report": "Report_ID",
}


def get_connection():
    return pymysql.connect(**DB_CONFIG)


def json_safe(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def rows_to_json(rows):
    return [
        {key: json_safe(value) for key, value in row.items()}
        for row in rows
    ]


def table_exists(table):
    return table in TABLES


def get_columns(table):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY,
                       COLUMN_DEFAULT, EXTRA
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                ORDER BY ORDINAL_POSITION
                """,
                (DB_CONFIG["database"], table),
            )
            return cur.fetchall()
    finally:
        conn.close()


@app.get("/api/health")
def health():
    try:
        conn = get_connection()
        conn.close()
        return jsonify({
            "status": "ok",
            "database": DB_CONFIG["database"],
            "message": "MySQL Connected",
        })
    except Exception as exc:
        return jsonify({
            "status": "standby",
            "database": DB_CONFIG["database"],
            "message": str(exc),
        }), 503


@app.get("/api/tables")
def tables():
    return jsonify({
        "tables": [
            {"name": table, "primary_key": pk}
            for table, pk in TABLES.items()
        ]
    })


@app.get("/api/table/<table>")
def get_table(table):
    if not table_exists(table):
        return jsonify({"error": "Unknown table"}), 404

    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # Table names are selected only from the whitelist above.
            cur.execute(f"SELECT * FROM `{table}`")
            rows = cur.fetchall()
        conn.close()
        return jsonify(rows_to_json(rows))
    except pymysql.MySQLError as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/table/<table>/meta")
def table_meta(table):
    if not table_exists(table):
        return jsonify({"error": "Unknown table"}), 404

    try:
        columns = get_columns(table)
        return jsonify(rows_to_json(columns))
    except pymysql.MySQLError as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/table/<table>")
def insert_record(table):
    if not table_exists(table):
        return jsonify({"error": "Unknown table"}), 404

    payload = request.get_json(silent=True) or {}
    if not payload:
        return jsonify({"error": "JSON body is required"}), 400

    try:
        columns = {c["COLUMN_NAME"] for c in get_columns(table)}
        clean = {k: v for k, v in payload.items() if k in columns}

        if not clean:
            return jsonify({"error": "No valid table columns supplied"}), 400

        names = list(clean.keys())
        placeholders = ", ".join(["%s"] * len(names))
        col_sql = ", ".join(f"`{c}`" for c in names)
        values = [clean[c] for c in names]

        sql = f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})"

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                new_id = cur.lastrowid
        finally:
            conn.close()

        return jsonify({
            "success": True,
            "message": f"Record inserted into {table}",
            "inserted_id": new_id,
        }), 201

    except pymysql.err.IntegrityError as exc:
        code = exc.args[0] if exc.args else None
        if code == 1451:
            message = (
                "Referential Integrity Protection: this record is referenced "
                "by another table and cannot be deleted/changed."
            )
        elif code == 1452:
            message = (
                "Foreign Key Error: the supplied parent record does not exist."
            )
        elif code == 1062:
            message = "Duplicate Primary Key or UNIQUE value."
        elif code == 1048:
            message = "A required NOT NULL value is missing."
        else:
            message = str(exc)
        return jsonify({"error": message, "mysql_code": code}), 409

    except pymysql.MySQLError as exc:
        return jsonify({"error": str(exc)}), 500


@app.delete("/api/table/<table>/<id_column>/<id_value>")
def delete_record(table, id_column, id_value):
    if not table_exists(table):
        return jsonify({"error": "Unknown table"}), 404

    if TABLES[table] != id_column:
        return jsonify({"error": "Invalid primary-key column"}), 400

    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM `{table}` WHERE `{id_column}`=%s",
                    (id_value,),
                )
                affected = cur.rowcount
        finally:
            conn.close()

        if affected == 0:
            return jsonify({"error": "Record not found"}), 404

        return jsonify({
            "success": True,
            "message": f"Record deleted from {table}",
        })

    except pymysql.err.IntegrityError as exc:
        code = exc.args[0] if exc.args else None
        if code == 1451:
            return jsonify({
                "error": (
                    "Referential Integrity Protection: Record cannot be "
                    "deleted because it is referenced by another table. "
                    "Delete or reassign dependent records first."
                ),
                "mysql_code": 1451,
            }), 409
        return jsonify({"error": str(exc), "mysql_code": code}), 409

    except pymysql.MySQLError as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/viva-query")
def viva_query():
    """
    Presentation-II query:
    Show researchers working on the project
    'AI Based Healthcare Analysis' and the total count.
    """
    sql = """
        SELECT
            R.Researcher_ID,
            R.Name,
            R.Email,
            R.Phone,
            I.Role,
            P.Title AS Project_Title,
            (
                SELECT COUNT(*)
                FROM investigator inv
                JOIN project pr
                  ON inv.Project_ID = pr.Project_ID
                WHERE pr.Title = 'AI Based Healthcare Analysis'
            ) AS Number_of_Researchers
        FROM investigator I
        JOIN researcher R
          ON I.Researcher_ID = R.Researcher_ID
        JOIN project P
          ON I.Project_ID = P.Project_ID
        WHERE P.Title = 'AI Based Healthcare Analysis';
    """

    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        conn.close()

        return jsonify({
            "sql": " ".join(sql.split()),
            "rows": rows_to_json(rows),
        })
    except pymysql.MySQLError as exc:
        return jsonify({"error": str(exc)}), 500


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Endpoint not found"}), 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
