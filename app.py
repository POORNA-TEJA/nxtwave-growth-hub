from flask import Flask, render_template, request, redirect, url_for, session, Response
import sqlite3
import random
import string
import os
import csv
import io
from functools import wraps
from urllib.parse import quote
from datetime import datetime, timedelta

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database.db")

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "local-development-secret-key"
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

ADMIN_USERNAME = os.environ.get(
    "ADMIN_USERNAME",
    "admin"
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "admin123"
)

CAMPAIGN_TARGET = 500
ADMIN_SESSION_MINUTES = 30

failed_logins = {}

MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCK_MINUTES = 10


# ==========================================================
# DATABASE
# ==========================================================

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


# ==========================================================
# SECURITY
# ==========================================================

@app.before_request
def protect_admin_session():

    if request.endpoint == "static":
        return

    if session.get("admin_logged_in"):

        last_activity = session.get(
            "admin_last_activity"
        )

        now = datetime.utcnow()

        if last_activity:

            try:

                previous = datetime.fromisoformat(
                    last_activity
                )

                if (
                    now - previous
                    > timedelta(
                        minutes=ADMIN_SESSION_MINUTES
                    )
                ):

                    session.clear()

                    if request.path.startswith("/admin"):

                        return redirect(
                            url_for(
                                "admin_login",
                                expired=1
                            )
                        )

            except ValueError:

                session.clear()

        session["admin_last_activity"] = now.isoformat()


@app.after_request
def add_security_headers(response):

    response.headers[
        "X-Content-Type-Options"
    ] = "nosniff"

    response.headers[
        "X-Frame-Options"
    ] = "DENY"

    response.headers[
        "Referrer-Policy"
    ] = "strict-origin-when-cross-origin"

    response.headers[
        "Permissions-Policy"
    ] = (
        "camera=(), "
        "microphone=(), "
        "geolocation=()"
    )

    return response


def admin_required(function):

    @wraps(function)
    def decorated_function(*args, **kwargs):

        if not session.get(
            "admin_logged_in"
        ):

            return redirect(
                url_for("admin_login")
            )

        return function(
            *args,
            **kwargs
        )

    return decorated_function


# ==========================================================
# REFERRAL CODE
# ==========================================================

def generate_referral_code(name):

    prefix = "".join(
        character
        for character in name.upper()
        if character.isalnum()
    )[:4]

    prefix = (
        prefix + "XXXX"
    )[:4]

    suffix = "".join(
        random.choices(
            string.digits,
            k=4
        )
    )

    return prefix + suffix


def get_unique_referral_code(name):

    connection = get_db_connection()

    while True:

        code = generate_referral_code(
            name
        )

        exists = connection.execute(
            """
            SELECT 1
            FROM registrations
            WHERE referral_code = ?
            """,
            (code,)
        ).fetchone()

        if not exists:

            connection.close()

            return code


# ==========================================================
# GROWTH ANALYTICS ENGINE
# ==========================================================

def calculate_campaign_analytics(
    total,
    referral,
    direct,
    active_referrers,
    first_date
):

    if total:

        referral_share = (
            referral
            / total
            * 100
        )

        direct_share = (
            direct
            / total
            * 100
        )

        active_referrer_rate = (
            active_referrers
            / total
            * 100
        )

    else:

        referral_share = 0
        direct_share = 0
        active_referrer_rate = 0

    if active_referrers:

        avg_referrals = (
            referral
            / active_referrers
        )

    else:

        avg_referrals = 0

    remaining = max(
        CAMPAIGN_TARGET - total,
        0
    )

    if CAMPAIGN_TARGET:

        progress = min(
            (
                total
                / CAMPAIGN_TARGET
                * 100
            ),
            100
        )

    else:

        progress = 0

    days_running = 1

    if first_date:

        try:

            parsed = datetime.strptime(
                str(first_date)[:19],
                "%Y-%m-%d %H:%M:%S"
            )

            days_running = max(
                (
                    datetime.utcnow()
                    - parsed
                ).total_seconds()
                / 86400,
                1 / 24
            )

        except ValueError:

            days_running = 1

    daily_average = (
        total
        / days_running
        if total
        else 0
    )

    if daily_average > 0:

        estimated_days = (
            remaining
            / daily_average
        )

    else:

        estimated_days = None

    if total == 0:

        status = "Waiting for Launch"

        recommendation = (
            "Start by attracting the first "
            "registrations and activating your "
            "first referrers."
        )

        score = 0

    elif (
        referral_share >= 60
        and active_referrer_rate >= 20
    ):

        status = "Strong Growth"

        recommendation = (
            "Referral growth is strong. Focus "
            "on activating more students while "
            "keeping milestones and the "
            "leaderboard visible."
        )

        score = 90

    elif referral_share >= 40:

        status = "Healthy Growth"

        recommendation = (
            "The referral channel has traction. "
            "Push repeat sharing and promote "
            "the next referral milestone."
        )

        score = 75

    elif active_referrers > 0:

        status = "Building Momentum"

        recommendation = (
            "You have active referrers, but "
            "referral contribution can grow. "
            "Promote WhatsApp sharing and the "
            "3-referral milestone."
        )

        score = 60

    else:

        status = "Early Growth"

        recommendation = (
            "Convert registered students into "
            "first-time referrers. Make the "
            "referral link and first milestone "
            "highly visible."
        )

        score = 35

    return {

        "referral_share": round(
            referral_share,
            1
        ),

        "direct_share": round(
            direct_share,
            1
        ),

        "active_referrer_rate": round(
            active_referrer_rate,
            1
        ),

        "avg_referrals": round(
            avg_referrals,
            1
        ),

        "remaining": remaining,

        "progress": round(
            progress,
            1
        ),

        "daily_average": round(
            daily_average,
            1
        ),

        "estimated_days": (
            round(
                estimated_days,
                1
            )
            if estimated_days is not None
            else None
        ),

        "status": status,

        "recommendation": recommendation,

        "score": score,

        "days_running": round(
            days_running,
            1
        )
    }


# ==========================================================
# HOME
# ==========================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


# ==========================================================
# REGISTRATION
# ==========================================================

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    referral_from_url = request.args.get(
        "ref",
        ""
    ).strip().upper()

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        college = request.form.get(
            "college",
            ""
        ).strip()

        phone = request.form.get(
            "phone",
            ""
        ).strip()

        referred_by = request.form.get(
            "referred_by",
            referral_from_url
        ).strip().upper()

        if not name or not email or not college or not phone:

            return render_template(
                "register.html",
                error="Please fill in all fields.",
                referred_by=referred_by
            )

        connection = get_db_connection()

        existing = connection.execute(
            """
            SELECT 1
            FROM registrations
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        if existing:

            connection.close()

            return render_template(
                "register.html",
                error="This email is already registered.",
                referred_by=referred_by
            )

        if referred_by:

            valid = connection.execute(
                """
                SELECT 1
                FROM registrations
                WHERE referral_code = ?
                """,
                (referred_by,)
            ).fetchone()

            if not valid:

                connection.close()

                return render_template(
                    "register.html",
                    error=(
                        "Invalid referral code. "
                        "Please check the code "
                        "and try again."
                    ),
                    referred_by=referred_by
                )

        connection.close()

        referral_code = get_unique_referral_code(
            name
        )

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
                referred_by or None
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

    return render_template(
        "register.html",
        referred_by=referral_from_url
    )


# ==========================================================
# SUCCESS
# ==========================================================

@app.route("/success")
def success():

    referral_code = request.args.get(
        "referral_code",
        ""
    )

    name = request.args.get(
        "name",
        ""
    )

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


# ==========================================================
# STUDENT DASHBOARD
# ==========================================================

@app.route(
    "/dashboard/<referral_code>"
)
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

        return (
            "Referral code not found.",
            404
        )

    referral_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        WHERE referred_by = ?
        """,
        (referral_code,)
    ).fetchone()[0]

    total = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        """
    ).fetchone()[0]

    rank = connection.execute(
        """
        SELECT COUNT(*) + 1
        FROM
        (
            SELECT
                r.referral_code,
                COUNT(referred.id)
                AS referral_count
            FROM registrations r
            LEFT JOIN registrations referred
                ON referred.referred_by =
                   r.referral_code
            GROUP BY
                r.id,
                r.referral_code
            HAVING
                COUNT(referred.id) > ?
        )
        """,
        (referral_count,)
    ).fetchone()[0]

    connection.close()

    progress = min(
        total
        / CAMPAIGN_TARGET
        * 100,
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

    next_milestone = next(
        (
            m
            for m in milestones
            if referral_count < m
        ),
        None
    )

    previous_milestone = max(
        [
            m
            for m in milestones
            if referral_count >= m
        ],
        default=0
    )

    if next_milestone:

        span = (
            next_milestone
            - previous_milestone
        )

        milestone_progress = (
            (
                referral_count
                - previous_milestone
            )
            / span
            * 100
        ) if span else 0

        remaining = (
            next_milestone
            - referral_count
        )

    else:

        milestone_progress = 100
        remaining = 0

    if referral_count == 0:

        referral_message = (
            "Share your link with friends "
            "to start growing your referrals."
        )

    elif remaining:

        referral_message = (
            f"{remaining} more registration"
            f"{'s' if remaining != 1 else ''} "
            "to reach your next milestone."
        )

    else:

        referral_message = (
            "You reached your latest milestone! "
            "Keep sharing to reach the next level."
        )

    referral_link = url_for(
        "register",
        ref=referral_code,
        _external=True
    )

    whatsapp_message = (
        'Join me for the free AI workshop '
        '"Build Your First AI Project in 60 Minutes"! '
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
        total_registrations=total,
        rank=rank,
        progress=round(
            progress,
            1
        ),
        target=CAMPAIGN_TARGET,
        referral_link=referral_link,
        whatsapp_link=whatsapp_link,
        next_milestone=next_milestone,
        milestone_progress=round(
            min(
                max(
                    milestone_progress,
                    0
                ),
                100
            ),
            1
        ),
        remaining_referrals=remaining,
        referral_message=referral_message
    )


# ==========================================================
# LEADERBOARD
# ==========================================================

@app.route("/leaderboard")
def leaderboard():

    connection = get_db_connection()

    leaders = connection.execute(
        """
        SELECT
            r.name,
            r.college,
            r.referral_code,
            COUNT(referred.id)
            AS referral_count
        FROM registrations r
        LEFT JOIN registrations referred
            ON referred.referred_by =
               r.referral_code
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

    total = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        """
    ).fetchone()[0]

    connection.close()

    progress = min(
        total
        / CAMPAIGN_TARGET
        * 100,
        100
    )

    return render_template(
        "leaderboard.html",
        leaderboard=leaders,
        total_registrations=total,
        campaign_target=CAMPAIGN_TARGET,
        campaign_progress=round(
            progress,
            1
        )
    )


# ==========================================================
# ADMIN LOGIN
# ==========================================================

@app.route(
    "/admin/login",
    methods=["GET", "POST"]
)
def admin_login():

    if session.get(
        "admin_logged_in"
    ):

        return redirect(
            url_for("admin")
        )

    error = None
    message = None

    client_key = (
        request.remote_addr
        or "unknown"
    )

    now = datetime.utcnow()

    record = failed_logins.get(
        client_key
    )

    if (
        record
        and now - record["first_attempt"]
        > timedelta(
            minutes=LOGIN_LOCK_MINUTES
        )
    ):

        failed_logins.pop(
            client_key,
            None
        )

        record = None

    if request.args.get(
        "expired"
    ):

        message = (
            "Your admin session expired. "
            "Please sign in again."
        )

    if request.method == "POST":

        if (
            record
            and record["attempts"]
            >= MAX_LOGIN_ATTEMPTS
        ):

            error = (
                "Too many failed attempts. "
                "Please try again after "
                "10 minutes."
            )

        else:

            username = request.form.get(
                "username",
                ""
            ).strip()

            password = request.form.get(
                "password",
                ""
            )

            if (
                username
                == ADMIN_USERNAME
                and password
                == ADMIN_PASSWORD
            ):

                session.clear()

                session.permanent = True

                session[
                    "admin_logged_in"
                ] = True

                session[
                    "admin_last_activity"
                ] = datetime.utcnow().isoformat()

                failed_logins.pop(
                    client_key,
                    None
                )

                return redirect(
                    url_for("admin")
                )

            if not record:

                record = {
                    "attempts": 0,
                    "first_attempt": now
                }

            record["attempts"] += 1

            failed_logins[
                client_key
            ] = record

            error = (
                "Invalid username or password."
            )

    return render_template(
        "admin_login.html",
        error=error,
        message=message
    )


# ==========================================================
# ADMIN LOGOUT
# ==========================================================

@app.route("/admin/logout")
def admin_logout():

    session.clear()

    return redirect(
        url_for("admin_login")
    )


# ==========================================================
# ADMIN DASHBOARD
# ==========================================================

@app.route("/admin")
@admin_required
def admin():

    connection = get_db_connection()

    total = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        """
    ).fetchone()[0]

    referral = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        WHERE referred_by IS NOT NULL
        AND referred_by != ''
        """
    ).fetchone()[0]

    direct = (
        total
        - referral
    )

    active_referrers = connection.execute(
        """
        SELECT COUNT(*)
        FROM registrations
        WHERE referral_code IN
        (
            SELECT DISTINCT referred_by
            FROM registrations
            WHERE referred_by IS NOT NULL
            AND referred_by != ''
        )
        """
    ).fetchone()[0]

    first_date = connection.execute(
        """
        SELECT MIN(created_at)
        FROM registrations
        """
    ).fetchone()[0]

    top_referrers = connection.execute(
        """
        SELECT
            r.name,
            r.college,
            r.referral_code,
            COUNT(referred.id)
            AS referral_count
        FROM registrations r
        LEFT JOIN registrations referred
            ON referred.referred_by =
               r.referral_code
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

    analytics = calculate_campaign_analytics(
        total,
        referral,
        direct,
        active_referrers,
        first_date
    )

    return render_template(
        "admin.html",

        total_registrations=total,

        referral_registrations=referral,

        direct_registrations=direct,

        active_referrers=active_referrers,

        campaign_target=CAMPAIGN_TARGET,

        campaign_progress=analytics[
            "progress"
        ],

        referral_share=analytics[
            "referral_share"
        ],

        direct_share=analytics[
            "direct_share"
        ],

        active_referrer_rate=analytics[
            "active_referrer_rate"
        ],

        average_referrals_per_referrer=analytics[
            "avg_referrals"
        ],

        registrations_remaining=analytics[
            "remaining"
        ],

        daily_average=analytics[
            "daily_average"
        ],

        estimated_days=analytics[
            "estimated_days"
        ],

        growth_status=analytics[
            "status"
        ],

        growth_recommendation=analytics[
            "recommendation"
        ],

        growth_score=analytics[
            "score"
        ],

        days_running=analytics[
            "days_running"
        ],

        top_referrers=top_referrers,

        recent_registrations=recent_registrations
    )


# ==========================================================
# CSV EXPORT
# ==========================================================

@app.route("/admin/export")
@admin_required
def export_registrations():

    connection = get_db_connection()

    rows = connection.execute(
        """
        SELECT
            id,
            name,
            email,
            college,
            phone,
            referral_code,
            referred_by,
            created_at
        FROM registrations
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    output = io.StringIO()

    writer = csv.writer(
        output
    )

    writer.writerow(
        [
            "ID",
            "Name",
            "Email",
            "College",
            "Phone",
            "Referral Code",
            "Referred By",
            "Created At"
        ]
    )

    for row in rows:

        writer.writerow(
            list(row)
        )

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; "
                "filename=campaign_registrations.csv"
        }
    )


# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )