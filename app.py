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
GOOGLE_API_KEY = ""  #secret api key                 
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




#----------------------------------------------------
# Mahim task part 2
#----------------------------------------------------



# ============================================================
# PART 2: STUDENT PAGES
# ============================================================

@app.route("/student/home")
@login_required("student")          
def stu_home():
    refresh_overdue()               
    me = session["account_id"]      
    today = date.today()

    

    # books this student has now (still Borrowed or already Overdue)
    row = fetch_one("SELECT COUNT(*) AS n FROM loan_records WHERE account_id = %s "
                    "AND loan_status IN ('Borrowed','Overdue')", (me,))
    borrowed = row["n"]

    # books that are due in the next 3 days
    three_days_later = today + timedelta(days=3)
    row = fetch_one("SELECT COUNT(*) AS n FROM loan_records WHERE account_id = %s "
                    "AND loan_status = 'Borrowed' AND due_date BETWEEN %s AND %s",
                    (me, today, three_days_later))
    due_soon = row["n"]

    # books that are already late
    row = fetch_one("SELECT COUNT(*) AS n FROM loan_records WHERE account_id = %s "
                    "AND loan_status = 'Overdue'", (me,))
    overdue = row["n"]

    # total copies on the shelf in the whole library 
    row = fetch_one("SELECT COALESCE(SUM(available_copies), 0) AS n FROM book_catalog")
    available = int(row["n"])

    
    stats = {"borrowed": borrowed, "due_soon": due_soon,
             "overdue": overdue, "available": available}

    # the 5 loans that are due first (shown in the "Current loans" box)
    current_loans = fetch_all("""
        SELECT l.due_date, l.loan_status, b.title, b.catalog_id
        FROM loan_records l
        JOIN book_catalog b ON l.catalog_id = b.catalog_id
        WHERE l.account_id = %s AND l.loan_status IN ('Borrowed','Overdue')
        ORDER BY l.due_date
        LIMIT 5""", (me,))

    # the 4 newest books in the library (shown in "Recently added")
    recent_books = fetch_all("SELECT catalog_id, title, author, cover_image FROM book_catalog "
                             "ORDER BY added_on DESC, catalog_id DESC LIMIT 4")

    # send everything to the HTML page ('active' highlights the sidebar link)
    return render_template("stu_home.html", active="home", stats=stats,
                           current_loans=current_loans, recent_books=recent_books)


@app.route("/student/catalog")
@login_required("student")
def stu_catalog():
    # read what the student typed in the search box and chose in the category list
    term = request.args.get("q", "").strip()
    subject = request.args.get("subject", "").strip()

    books = search_books(term, subject)     # helper function from Part 1
    subjects = fetch_all("SELECT subject_name FROM subjects ORDER BY subject_name")

    return render_template("stu_catalog.html", active="catalog", books=books,
                           subjects=subjects, term=term, subject=subject)


@app.route("/student/catalog/<int:catalog_id>")
@login_required("student")
def stu_book_view(catalog_id):
    

    # get the book and its category name
    book = fetch_one("""
        SELECT b.*, s.subject_name
        FROM book_catalog b
        LEFT JOIN subjects s ON b.subject_id = s.subject_id
        WHERE b.catalog_id = %s""", (catalog_id,))

    # if there is no such book, go back to the catalogue
    if book is None:
        flash("That book does not exist.", "error")
        return redirect(url_for("stu_catalog"))

    # does this student already have this book? (so we can hide the Borrow button)
    row = fetch_one("SELECT COUNT(*) AS n FROM loan_records WHERE account_id = %s "
                    "AND catalog_id = %s AND loan_status IN ('Borrowed','Overdue')",
                    (session["account_id"], catalog_id))
    already = row["n"] > 0              # True or False

    return render_template("stu_book_view.html", active="catalog", book=book,
                           already=already, loan_days=LOAN_DAYS)


# ============================================================
# FEATURE 4: BORROW AND RETURN   (Shoeib)
# ============================================================

@app.route("/student/borrow/<int:catalog_id>", methods=["POST"])
@login_required("student")
def stu_borrow(catalog_id):
    # Borrow rules:
    #   1) the book must exist
    #   2) the student must not already have this book
    #   3) at least 1 copy must be free
    # All the database steps run as ONE transaction: either all of them
    # happen or none of them do.

    refresh_overdue()
    conn = get_db_connection()                  # open a connection to MySQL
    cursor = conn.cursor(dictionary=True)       # dictionary=True lets us use row["title"]

    try:
        # FOR UPDATE locks this book's row until we commit, so two students
        # cannot take the last copy at the same moment
        cursor.execute("SELECT * FROM book_catalog WHERE catalog_id = %s FOR UPDATE",
                       (catalog_id,))
        book = cursor.fetchone()

        # does this student already have this book?
        cursor.execute("SELECT COUNT(*) AS n FROM loan_records WHERE account_id = %s "
                       "AND catalog_id = %s AND loan_status IN ('Borrowed','Overdue')",
                       (session["account_id"], catalog_id))
        already = cursor.fetchone()["n"] > 0

        # check the rules one by one
        if book is None:
            flash("That book does not exist.", "error")
        elif already:
            flash("You have already borrowed this book. Return it before borrowing it again.", "error")
        elif book["available_copies"] <= 0:
            flash("No copies are currently available.", "error")
        else:
            # all rules passed, so make the loan
            today = date.today()
            due_date = today + timedelta(days=LOAN_DAYS)

            # step 1: add a new row to loan_records
            cursor.execute("INSERT INTO loan_records "
                           "(account_id, catalog_id, borrow_date, due_date, loan_status) "
                           "VALUES (%s, %s, %s, %s, 'Borrowed')",
                           (session["account_id"], catalog_id, today, due_date))

            # step 2: one less copy is available now
            cursor.execute("UPDATE book_catalog SET available_copies = available_copies - 1 "
                           "WHERE catalog_id = %s", (catalog_id,))

            flash("You borrowed '" + book["title"] + "'. It is due on "
                  + due_date.strftime("%d/%m/%Y") + ".", "success")

        conn.commit()               # save all the changes together

    except Exception:
        conn.rollback()             # something went wrong: undo everything
        raise                       # show the error (our error handler catches it)
    finally:
        cursor.close()              # always close, even if there was an error
        conn.close()

    return redirect(url_for("stu_book_view", catalog_id=catalog_id))


@app.route("/student/loans")
@login_required("student")
def stu_my_loans():
    refresh_overdue()

    # students only see the books they still have.
    current = fetch_all("""
        SELECT l.loan_id, l.borrow_date, l.due_date, l.loan_status,
               b.title, b.author, b.catalog_id
        FROM loan_records l
        JOIN book_catalog b ON l.catalog_id = b.catalog_id
        WHERE l.account_id = %s AND l.loan_status IN ('Borrowed', 'Overdue')
        ORDER BY l.due_date""", (session["account_id"],))

    return render_template("stu_my_loans.html", active="loans", current=current,
                           today=date.today())


@app.route("/student/return/<int:loan_id>", methods=["POST"])
@login_required("student")
def stu_return(loan_id):
    # Return a book: save the return date, mark the loan 'Returned',
    # and give the copy back to the library. Also one transaction.

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # find the loan, but ONLY if it belongs to this student
        # (so a student cannot return someone else's book)
        cursor.execute("SELECT * FROM loan_records WHERE loan_id = %s AND account_id = %s FOR UPDATE",
                       (loan_id, session["account_id"]))
        loan = cursor.fetchone()

        if loan is None or loan["loan_status"] == "Returned":
            flash("That book is not currently borrowed by you.", "error")
        else:
            # step 1: write today's date and change the status
            cursor.execute("UPDATE loan_records SET return_date = %s, loan_status = 'Returned' "
                           "WHERE loan_id = %s", (date.today(), loan_id))

            # step 2: one more copy is available again
            # LEAST() picks the smaller number, so available copies can
            # never become bigger than total copies
            cursor.execute("UPDATE book_catalog SET available_copies = "
                           "LEAST(total_copies, available_copies + 1) WHERE catalog_id = %s",
                           (loan["catalog_id"],))

            flash("Book returned successfully. Thank you!", "success")

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("stu_my_loans"))