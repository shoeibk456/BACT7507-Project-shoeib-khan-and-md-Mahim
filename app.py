#   (PART 1: setup, helpers, errors, login)

# ---------- 1. IMPORTS  ----------
import os                      # It will work with files and folders
import re                      # It will check text patterns (like an email format)
import uuid                    # It will make a random unique name for uploaded pictures
from datetime import date, timedelta   
from functools import wraps    # needed for the login check

import mysql.connector         
import requests                # ask other websites for data (Google Books)
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

# from database import execute, fetch_all, fetch_one, get_db_connection   # our own database.py file


# ---------- 2. CREATE THE APP ----------
app = Flask(__name__)                              
app.secret_key = "libconnect-demo-secret-key"        # secret word Flask uses to protect the login session
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024   

# ---------- 3. SETTINGS  ----------
LOAN_DAYS = 14                      # a book can be kept for 14 days
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "covers")   # where cover pictures are saved
ALLOWED_IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp"}                  # picture types we accept
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"             # Google Books address
GOOGLE_API_KEY = "AIzaSyAru_AEBdfDaDK2oI5bqyzNr_C-_uwB-Bg"  #secret api key                 
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")   # shape of a real email
PHONE_PATTERN = re.compile(r"^[0-9 +()\-]{6,20}$")          


# ---------- 4. HELPER FUNCTIONS  ----------

#  1: show a date nicely.
@app.template_filter("dmy")
def format_date(value):
    if value:                              # if there is a date
        return value.strftime("%d/%m/%Y")  # write it as day/month/year
    return "-"                             # if there is no date, show a dash


# It decide which dashboard a person should see
def home_for_role(role):
    if role == "admin":
        return url_for("adm_home")         # admin dashboard
    return url_for("stu_home")             # everyone else = student dashboard


#  login check. We write  @login_required("admin")  above a page
# to say "only an admin may open this page".
def login_required(required_role):
    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(*args, **kwargs):
            # not logged in -> go to the login page
            if "account_id" not in session:
                flash("Please log in first.", "error")
                return redirect(url_for("login_page"))
            # logged in but the wrong role -> send them to their own dashboard
            if session.get("role") != required_role:
                flash("You do not have permission to open that page.", "error")
                return redirect(home_for_role(session.get("role")))
            # everything is fine -> open the page
            return view_function(*args, **kwargs)
        return wrapped_view
    return decorator


#  mark late books as 'Overdue'
def refresh_overdue():
    # every loan that is still 'Borrowed' but due before today becomes 'Overdue'
    execute("UPDATE loan_records SET loan_status = 'Overdue' "
            "WHERE loan_status = 'Borrowed' AND due_date < %s", (date.today(),))


#  check a picture file is allowed
def allowed_image(filename):
    if "." not in filename:                          # no dot = no file type = not allowed
        return False
    extension = filename.rsplit(".", 1)[1].lower()   # the text after the last dot, in small letters
    return extension in ALLOWED_IMAGE_TYPES          # True only for png, jpg, jpeg, gif, webp


#  turn text from a form into a whole number.
# "errors" is a list; if something is wrong we add a message to it.
def to_int(text, name, errors, minimum=None, maximum=None, required=False):
    text = (text or "").strip()          # remove spaces; if there is no text, use ""

    if text == "":                       # nothing typed
        if required:
            errors.append(name + " is required.")
        return None

    try:
        number = int(text)               # try to change the text into a number
    except ValueError:                   # for example the user typed "abc"
        errors.append(name + " must be a whole number.")
        return None

    if minimum is not None and number < minimum:
        errors.append(name + " cannot be less than " + str(minimum) + ".")
    if maximum is not None and number > maximum:
        errors.append(name + " cannot be more than " + str(maximum) + ".")

    return number


#  search for books (used by both the admin and the student)
def search_books(term, subject):
    like = "%" + term + "%"       # % means "anything"

    # a blank search (%s = '') means "do not filter", so all books show
    return fetch_all("""
        SELECT b.catalog_id, b.title, b.author, b.isbn, b.total_copies,
               b.available_copies, b.cover_image, b.ebook_available,
               s.subject_name
        FROM book_catalog b
        LEFT JOIN subjects s ON b.subject_id = s.subject_id
        WHERE (%s = '' OR b.title LIKE %s OR b.author LIKE %s OR b.isbn LIKE %s)
          AND (%s = '' OR s.subject_name = %s)
        ORDER BY b.title
    """, (term, like, like, like, subject, subject))


# ---------- 5. ERROR PAGES ----------

# MySQL problem 
@app.errorhandler(mysql.connector.Error)
def handle_database_error(error):
    return render_template(
        "error.html",
        title="Database problem",
        message="LibConnect could not talk to MySQL. Check that MySQL is running, "
                "that libconnect_schema.sql has been run, and that the password "
                "in database.py is correct.",
        detail=str(error)), 500


# someone uploaded a file bigger than 2 MB
@app.errorhandler(413)
def handle_file_too_large(error):
    return render_template("error.html", title="File too large",
                           message="That file is bigger than 2 MB. Please choose a smaller image.",
                           detail=""), 413


# someone opened a page that does not exist
@app.errorhandler(404)
def handle_not_found(error):
    return render_template("error.html", title="Page not found",
                           message="That page does not exist.", detail=""), 404


# ---------- 6. FEATURE 1: LOGIN AND LOGOUT  ----------

@app.route("/", methods=["GET", "POST"])
def login_page():
    # already logged in? go straight to the right dashboard
    if request.method == "GET" and "account_id" in session:
        return redirect(home_for_role(session["role"]))

    # the user pressed the Login button
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()   # email or student ID
        password = request.form.get("password", "")

        # step 1: are both boxes filled in?
        if identifier == "" or password == "":
            flash("Please enter your email (or student ID) and your password.", "error")
            return render_template("portal_login.html")

        # step 2: find this person in the database
        account = fetch_one("SELECT * FROM accounts WHERE email = %s OR student_id = %s",
                            (identifier, identifier))

        # step 3: check the password. We compare with the saved hash (scrambled
        # password), never plain text. One message for "wrong email" and "wrong
        # password" on purpose, so a hacker cannot find out which emails exist.
        if account is None or not check_password_hash(account["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("portal_login.html")

        # step 4: inactive accounts cannot log in
        if account["status"] != "Active":
            flash("This account is inactive. Please contact the library.", "error")
            return render_template("portal_login.html")

        # step 5: success! remember who this is in the session
        session.clear()
        session["account_id"] = account["account_id"]
        session["full_name"] = account["full_name"]
        session["role"] = account["role"]
        return redirect(home_for_role(account["role"]))

    # first time opening the page: just show the login form
    return render_template("portal_login.html")


@app.route("/logout")
def logout():
    session.clear()                      # forget the user
    flash("You have been logged out.", "success")
    return redirect(url_for("login_page"))