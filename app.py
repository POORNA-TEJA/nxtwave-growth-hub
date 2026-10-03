from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import random
import string
import os
from functools import wraps
from urllib.parse import quote


app = Flask(__name__)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database.db")


app.secret_key = os.environ.get(
    "SECRET_KEY",
    "local-development-secret-key"
)


ADMIN_USERNAME = os.environ.get(
    "ADMIN_USERNAME",
    "admin"
)


ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "admin123"
)


def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def create_database():
    connection = get_db_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            college TEXT NOT NULL,
            phone TEXT NOT NULL,
            referral_code TEXT NOT NULL UNIQUE,
            referred_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    connection.close()


create_database()


def generate_referral_code(name):
    prefix = ''.join(
        character
        for character in name.upper()
        if character.isalnum()
    )[:4]

    suffix = ''.join(
        random.choices(string.digits, k=4)
    )

    return prefix + suffix


def get_unique_referral_code(name):
    connection = get_db_connection()

    while True:
        code = generate_referral_code(name)

        existing = connection.execute(
            """
            SELECT 1
            FROM registrations
            WHERE referral_code = ?
            """,
            (code,)
        ).fetchone()

        if not existing:
            connection.close()
            return code


def admin_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))

        return function(*args, **kwargs)

    return decorated_function


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        college = request.form["college"].strip()
        phone = request.form["phone"].strip()

        referred_by = request.form.get(
            "referred_by",
            ""
        ).strip().upper()

        connection = get_db_connection()

        existing_student = connection.execute(
            """
            SELECT *
            FROM registrations
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        if existing_student:
            connection.close()

            return render_template(
                "register.html",
                error="This email is already registered.",
                referred_by=referred_by
            )

        if referred_by:

            valid_referral = connection.execute(
                """
                SELECT 1
                FROM registrations
                WHERE referral_code = ?
                """,
                (referred_by,)
            ).fetchone()

            if not valid_referral:
                connection.close()

                return render_template(
                    "register.html",
                    error="Invalid referral code. Please check the code and try again.",
                    referred_by=referred_by
                )

        connection.close()

        referral_code = get_unique_referral_code(name)

        connection = get_db_connection()

        connection.execute(
            """
            INSERT INTO registrations
            (
                name,
                email,
                college,
                phone,
                referral_code,
                referred_by
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                email,
                college,
                phone,
                referral_code,
                referred_by if referred_by else None
            )
        )

        connection.commit()
        connection.close()

        return redirect(
            url_for(
                "success",
                referral_code=referral_code,
                name=name
            )
        )

    referred_by = request.args.get(
        "ref",
        ""
    ).strip().upper()

    return render_template(
        "register.html",
        referred_by=referred_by
    )


@app.route("/success")
def success():

    referral_code = request.args.get(
        "referral_code"
    )

    name = request.args.get("name")

    referral_link = url_for(
        "register",
        ref=referral_code,
        _external=True
    )

    dashboard_link = url_for(
        "dashboard",
        referral_code=referral_code,
        _external=True
    )

    return render_template(
        "success.html",
        referral_code=referral_code,
        name=name,
        referral_link=referral_link,
        dashboard_link=dashboard_link
    )


@app.route("/dashboard/<referral_code>")
def dashboard(referral_code):

    referral_code = referral_code.upper()

    connection = get_db_connection()

    student = connection.execute(
        """
        SELECT *
        FROM registrations
        WHERE referral_code = ?
        """,
        (referral_code,)
    ).fetchone()

    if not student:
        connection.close()

        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Referral Code Not Found</title>
        </head>
        <body style="font-family: Arial; padding: 50px;">
            <h1>Referral code not found</h1>
            <p>Please check your referral code and try again.</p>
            <a href="/">Back to Workshop</a>
        </body>
        </html>
        """

    referral_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        WHERE referred_by = ?
        """,
        (referral_code,)
    ).fetchone()[0]

    total_registrations = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        """
    ).fetchone()[0]

    rank = connection.execute(
        """
        SELECT COUNT(*) + 1
        FROM (
            SELECT
                referral_code,
                COUNT(*) AS referral_count
            FROM registrations
            WHERE referred_by IS NOT NULL
            GROUP BY referral_code
            HAVING COUNT(*) > ?
        )
        """,
        (referral_count,)
    ).fetchone()[0]

    connection.close()

    target = 500

    progress = min(
        (total_registrations / target) * 100,
        100
    )

    milestones = [
        1,
        3,
        5,
        10,
        20,
        50
    ]

    next_milestone = None

    for milestone in milestones:
        if referral_count < milestone:
            next_milestone = milestone
            break

    if next_milestone is None:
        next_milestone = referral_count

    previous_milestone = 0

    for milestone in milestones:
        if referral_count >= milestone:
            previous_milestone = milestone

    if next_milestone > previous_milestone:

        milestone_progress = (
            (
                referral_count - previous_milestone
            )
            /
            (
                next_milestone - previous_milestone
            )
        ) * 100

    else:

        milestone_progress = 100

    milestone_progress = min(
        max(milestone_progress, 0),
        100
    )

    remaining_referrals = max(
        next_milestone - referral_count,
        0
    )

    if referral_count == 0:

        referral_message = (
            "Share your link with friends "
            "to start growing your referrals."
        )

    elif remaining_referrals == 0:

        referral_message = (
            "You reached your latest milestone! "
            "Keep sharing to reach the next level."
        )

    else:

        referral_message = (
            f"{remaining_referrals} more "
            "registration"
            + (
                "s"
                if remaining_referrals != 1
                else ""
            )
            + " to reach your next milestone."
        )

    referral_link = url_for(
        "register",
        ref=referral_code,
        _external=True
    )

    whatsapp_message = (
        "Join me for the free AI workshop "
        "\"Build Your First AI Project in 60 Minutes\"! "
        "Register here: "
        + referral_link
    )

    whatsapp_link = (
        "https://wa.me/?text="
        + quote(whatsapp_message)
    )

    return render_template(
        "dashboard.html",
        student=student,
        referral_count=referral_count,
        total_registrations=total_registrations,
        rank=rank,
        progress=round(progress, 1),
        target=target,
        referral_link=referral_link,
        whatsapp_link=whatsapp_link,
        next_milestone=next_milestone,
        milestone_progress=round(
            milestone_progress,
            1
        ),
        remaining_referrals=remaining_referrals,
        referral_message=referral_message
    )


@app.route("/leaderboard")
def leaderboard():

    connection = get_db_connection()

    leaders = connection.execute(
        """
        SELECT
            r.name,
            r.college,
            r.referral_code,
            COUNT(referred.id) AS referral_count
        FROM registrations r
        LEFT JOIN registrations referred
            ON referred.referred_by = r.referral_code
        GROUP BY
            r.id,
            r.name,
            r.college,
            r.referral_code
        ORDER BY
            referral_count DESC,
            r.created_at ASC
        LIMIT 10
        """
    ).fetchall()

    total_registrations = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        """
    ).fetchone()[0]

    connection.close()

    target = 500

    progress = min(
        (total_registrations / target) * 100,
        100
    )

    return render_template(
        "leaderboard.html",
        leaders=leaders,
        total_registrations=total_registrations,
        target=target,
        progress=round(progress, 1)
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if session.get("admin_logged_in"):
        return redirect(url_for("admin"))

    error = None

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        if (
            username == ADMIN_USERNAME
            and password == ADMIN_PASSWORD
        ):

            session["admin_logged_in"] = True

            return redirect(
                url_for("admin")
            )

        error = "Invalid username or password."

    return render_template(
        "admin_login.html",
        error=error
    )


@app.route("/admin/logout")
def admin_logout():

    session.pop(
        "admin_logged_in",
        None
    )

    return redirect(
        url_for("admin_login")
    )


@app.route("/admin")
@admin_required
def admin():

    connection = get_db_connection()

    total_registrations = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        """
    ).fetchone()[0]

    referral_registrations = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        WHERE referred_by IS NOT NULL
        AND referred_by != ''
        """
    ).fetchone()[0]

    direct_registrations = (
        total_registrations -
        referral_registrations
    )

    total_referrers = connection.execute(
        """
        SELECT COUNT(DISTINCT referred_by)
        FROM registrations
        WHERE referred_by IS NOT NULL
        AND referred_by != ''
        """
    ).fetchone()[0]

    top_referrers = connection.execute(
        """
        SELECT
            r.name,
            r.college,
            r.referral_code,
            COUNT(referred.id) AS referral_count
        FROM registrations r
        LEFT JOIN registrations referred
            ON referred.referred_by = r.referral_code
        GROUP BY
            r.id,
            r.name,
            r.college,
            r.referral_code
        ORDER BY
            referral_count DESC,
            r.created_at ASC
        LIMIT 10
        """
    ).fetchall()

    recent_registrations = connection.execute(
        """
        SELECT
            name,
            college,
            email,
            referral_code,
            referred_by,
            created_at
        FROM registrations
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()

    connection.close()

    target = 500

    progress = min(
        (total_registrations / target) * 100,
        100
    )

    return render_template(
        "admin.html",
        total_registrations=total_registrations,
        referral_registrations=referral_registrations,
        direct_registrations=direct_registrations,
        total_referrers=total_referrers,
        target=target,
        progress=round(progress, 1),
        top_referrers=top_referrers,
        recent_registrations=recent_registrations
    )


if __name__ == "__main__":
    app.run(debug=True)