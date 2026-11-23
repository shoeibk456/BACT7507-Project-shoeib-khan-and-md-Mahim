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
# FEATURE 4: BORROW AND RETURN   
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



# ============================================================
# PART 3: EBOOK SEARCH (Google Books) AND STUDENT PROFILE
# ============================================================

# ---------- FEATURE 5: EBOOK SEARCH using the Google Books API  ----------

# Turn one Google Books result into a small dictionary the HTML page can use
def simplify_google_book(item):
    info = item.get("volumeInfo", {})                     # the book details (title, authors...)
    cover = info.get("imageLinks", {}).get("thumbnail")   # small cover picture address (may not exist)

    # link to the book's page on Google Books (built from the book's id)
    link = None
    if item.get("id"):
        link = "https://books.google.com/books?id=" + item["id"]

    # Google sometimes gives http:// addresses; change them to https://
    if cover:
        cover = cover.replace("http://", "https://")

    # join the author names with commas; if there are none, say "Unknown author"
    authors = ", ".join(info.get("authors", []))
    if authors == "":
        authors = "Unknown author"

    return {
        "title": info.get("title", "Untitled"),
        "author": authors,
        "year": (info.get("publishedDate") or "")[:4],    # first 4 letters of the date = the year
        "cover": cover,
        "link": link,
    }


@app.route("/student/ebooks")
@login_required("student")
def stu_ebook_search():
    term = request.args.get("q", "").strip()     # what the student typed in the search box
    results = []                                 # the books we found
    error = None                                 # a message if something goes wrong

    if term:                                     # only search if something was typed
        try:
            # the question we send to Google
            params = {"q": term, "maxResults": 15, "printType": "books"}
            if GOOGLE_API_KEY:
                params["key"] = GOOGLE_API_KEY

            response = requests.get(GOOGLE_BOOKS_URL, params=params, timeout=10)
            response.raise_for_status()          # jump to "except" if Google says there is an error

            # Google puts the books in a list called "items"
            for item in response.json().get("items", []):
                results.append(simplify_google_book(item))

        except requests.exceptions.HTTPError as problem:
            # Google answered, but with an error number
            if problem.response is not None and problem.response.status_code == 429:
                error = "Google Books is busy right now (daily limit reached). Please try again later."
            else:
                error = "Google Books returned an error. Please try again later."
        except requests.exceptions.RequestException:
            # we could not reach Google at all (no internet, firewall...)
            error = ("Could not reach Google Books. "
                     "Please check your internet connection and try again.")
        except ValueError:
            # Google sent something we could not read
            error = "Google Books sent an unexpected reply. Please try again."

    return render_template("stu_ebook_search.html", active="ebooks", term=term,
                           results=results, error=error)


# ---------- STUDENT PROFILE ----------

@app.route("/student/account", methods=["GET", "POST"])
@login_required("student")
def stu_account():
    me = session["account_id"]

    # the student pressed "Save" on the profile form
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()

        # collect every mistake in a list
        errors = []
        if full_name == "":
            errors.append("Name cannot be empty.")
        if phone != "" and not PHONE_PATTERN.match(phone):
            errors.append("Phone number can only contain digits, spaces, + ( ) and -.")

        if errors:
            # show every mistake
            for message in errors:
                flash(message, "error")
        else:
            # no mistakes: save the changes (an empty phone is saved as NULL / nothing)
            if phone == "":
                phone = None
            execute("UPDATE accounts SET full_name = %s, phone = %s WHERE account_id = %s",
                    (full_name, phone, me))
            session["full_name"] = full_name     # update the name shown at the top of the page
            flash("Profile updated successfully.", "success")
            return redirect(url_for("stu_account"))

    # show the profile page with the student's saved details
    account = fetch_one("SELECT * FROM accounts WHERE account_id = %s", (me,))
    return render_template("stu_account.html", active="account", account=account)


@app.route("/student/account/password", methods=["POST"])
@login_required("student")
def stu_change_password():
    current = request.form.get("current_password", "")    # the old password
    new = request.form.get("new_password", "")            # the new password
    confirm = request.form.get("confirm_password", "")    # the new password typed again

    # get the saved (scrambled) password of this student
    account = fetch_one("SELECT password_hash FROM accounts WHERE account_id = %s",
                        (session["account_id"],))

    # check the rules one by one
    if not check_password_hash(account["password_hash"], current):
        flash("Your current password is not correct.", "error")
    elif len(new) < 6:
        flash("The new password must be at least 6 characters.", "error")
    elif new != confirm:
        flash("The new password and the confirmation do not match.", "error")
    else:
        # all good: save the new password as a hash (never as plain text)
        new_hash = generate_password_hash(new, method="pbkdf2:sha256")
        execute("UPDATE accounts SET password_hash = %s WHERE account_id = %s",
                (new_hash, session["account_id"]))
        flash("Password changed successfully.", "success")

    return redirect(url_for("stu_account"))



#==========================
# ======MAHIM'S PART======
#==========================


# ============================================================
# PART 4a - ADMIN: dashboard, books, categories 
# ============================================================


# ---------- ADMIN DASHBOARD (numbers + chart data come from SQL) ----------
@app.route("/admin/home")
@login_required("admin")
def adm_home():
    refresh_overdue()                      # first mark late books as Overdue

    # Card 1 and 2: total copies and copies still on the shelf
    totals = fetch_one("SELECT COALESCE(SUM(total_copies),0) AS total, "
                       "COALESCE(SUM(available_copies),0) AS available FROM book_catalog")

    # Card 3: loans that are open (Borrowed or Overdue)
    borrowed = fetch_one("SELECT COUNT(*) AS n FROM loan_records "
                         "WHERE loan_status IN ('Borrowed','Overdue')")["n"]

    # Card 4: loans that are late
    overdue = fetch_one("SELECT COUNT(*) AS n FROM loan_records WHERE loan_status = 'Overdue'")["n"]

    # Card 5: number of students
    students = fetch_one("SELECT COUNT(*) AS n FROM accounts WHERE role = 'student'")["n"]

    stats = {"total_books": int(totals["total"]),
             "available": int(totals["available"]),
             "borrowed": borrowed,
             "overdue": overdue,
             "students": students}

    # Bar chart: for each category, how many copies are available and how many are borrowed
    by_subject = fetch_all("""
        SELECT COALESCE(s.subject_name, 'Uncategorised') AS label,
               COALESCE(SUM(b.available_copies), 0) AS available,
               COALESCE(SUM(b.total_copies - b.available_copies), 0) AS borrowed
        FROM book_catalog b LEFT JOIN subjects s ON b.subject_id = s.subject_id
        GROUP BY label ORDER BY label""")

    # Chart.js wants three simple lists, so we build them with a loop
    labels = []
    available_list = []
    borrowed_list = []
    for row in by_subject:
        labels.append(row["label"])
        available_list.append(int(row["available"]))
        borrowed_list.append(int(row["borrowed"]))
    chart = {"labels": labels, "available": available_list, "borrowed": borrowed_list}

    # Doughnut chart: how many loans are Borrowed, Returned and Overdue
    status_rows = fetch_all("SELECT loan_status, COUNT(*) AS n FROM loan_records GROUP BY loan_status")
    counts = {}
    for row in status_rows:
        counts[row["loan_status"]] = row["n"]
    status_chart = [counts.get("Borrowed", 0), counts.get("Returned", 0), counts.get("Overdue", 0)]

    # Table: the 6 newest loans
    recent = fetch_all("""
        SELECT a.full_name AS student_name, b.title AS book_title,
               l.borrow_date, l.loan_status
        FROM loan_records l
        JOIN accounts a ON l.account_id = a.account_id
        JOIN book_catalog b ON l.catalog_id = b.catalog_id
        ORDER BY l.loan_id DESC LIMIT 6""")

    return render_template("adm_home.html", active="home", stats=stats, chart=chart,
                           status_chart=status_chart, recent=recent)


# ---------- BOOKS: list and search ----------
@app.route("/admin/catalog")
@login_required("admin")
def adm_catalog():
    term = request.args.get("q", "").strip()           # text typed in the search box
    subject = request.args.get("subject", "").strip()  # category chosen in the dropdown
    books = search_books(term, subject)                # helper from Part 1
    subjects = fetch_all("SELECT subject_name FROM subjects ORDER BY subject_name")
    return render_template("adm_catalog.html", active="catalog", books=books,
                           subjects=subjects, term=term, subject=subject)


# ---------- BOOKS: add and edit (one page does both) ----------
@app.route("/admin/catalog/add", methods=["GET", "POST"], defaults={"catalog_id": None})
@app.route("/admin/catalog/edit/<int:catalog_id>", methods=["GET", "POST"])
@login_required("admin")
def adm_book_form(catalog_id):
    subjects = fetch_all("SELECT subject_id, subject_name FROM subjects ORDER BY subject_name")

    # When editing, load the book that is already saved
    existing = None
    if catalog_id:
        existing = fetch_one("SELECT * FROM book_catalog WHERE catalog_id = %s", (catalog_id,))
        if existing is None:
            flash("That book does not exist.", "error")
            return redirect(url_for("adm_catalog"))

    # The form was submitted (the admin pressed Save)
    if request.method == "POST":
        form = request.form
        errors = []                                       # we collect all problems in this list

        # 1. read the boxes
        title = form.get("title", "").strip()
        author = form.get("author", "").strip()
        isbn = form.get("isbn", "").strip() or None       # empty text becomes None (= NULL)
        publisher = form.get("publisher", "").strip() or None
        ebook_link = form.get("ebook_link", "").strip() or None
        ebook_available = form.get("ebook_available") == "yes"

        # 2. check the boxes
        if not title:
            errors.append("Title is required.")
        if not author:
            errors.append("Author is required.")
        year = to_int(form.get("publish_year"), "Year", errors, 1000, date.today().year + 1)
        total = to_int(form.get("total_copies"), "Total copies", errors, 0, required=True)
        subject_id = to_int(form.get("subject_id"), "Category", errors, required=True)

        # the ISBN must not belong to a different book
        if isbn:
            clash = fetch_one("SELECT catalog_id FROM book_catalog WHERE isbn = %s AND catalog_id <> %s",
                              (isbn, catalog_id or 0))
            if clash:
                errors.append("That ISBN is already used by another book.")

        if ebook_available and not ebook_link:
            errors.append("Please enter the eBook link, or choose 'No' for eBook available.")
        if ebook_link and not ebook_link.lower().startswith(("http://", "https://")):
            errors.append("The eBook link must start with http:// or https://")

        # when editing: total copies cannot be less than the copies students have right now
        open_loans = 0
        if catalog_id and total is not None:
            open_loans = fetch_one("SELECT COUNT(*) AS n FROM loan_records WHERE catalog_id = %s "
                                   "AND loan_status IN ('Borrowed','Overdue')", (catalog_id,))["n"]
            if total < open_loans:
                errors.append(f"Total copies cannot be lower than the {open_loans} copies currently borrowed.")

        # cover picture: keep the old one unless a new picture was chosen
        cover_file = request.files.get("cover_image")
        cover_name = existing["cover_image"] if existing else None
        if cover_file and cover_file.filename:
            if not allowed_image(cover_file.filename):
                errors.append("Cover must be an image file (png, jpg, jpeg, gif or webp).")

        # 3. if something is wrong, show the form again with the messages
        if errors:
            for message in errors:
                flash(message, "error")
            values = dict(form)                  # keep what the admin already typed
            values["cover_image"] = cover_name
            return render_template("adm_book_form.html", active="catalog", values=values,
                                   subjects=subjects, editing=bool(catalog_id))

        # 4. save the new cover picture with a random unique name
        if cover_file and cover_file.filename:
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            extension = cover_file.filename.rsplit(".", 1)[1].lower()
            cover_name = uuid.uuid4().hex + "." + extension
            cover_file.save(os.path.join(UPLOAD_FOLDER, cover_name))

        # 5. save the book in the database
        if catalog_id:
            # EDIT: available copies = total copies minus the copies that are out on loan
            execute("""UPDATE book_catalog SET title=%s, author=%s, isbn=%s, publisher=%s,
                       publish_year=%s, subject_id=%s, total_copies=%s, available_copies=%s,
                       cover_image=%s, ebook_available=%s, ebook_link=%s WHERE catalog_id=%s""",
                    (title, author, isbn, publisher, year, subject_id, total, total - open_loans,
                     cover_name, ebook_available, ebook_link, catalog_id))
            flash("Book updated successfully.", "success")
        else:
            # ADD: a new book starts with available copies = total copies
            execute("""INSERT INTO book_catalog (title, author, isbn, publisher, publish_year,
                       subject_id, total_copies, available_copies, cover_image, ebook_available, ebook_link)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (title, author, isbn, publisher, year, subject_id, total, total,
                     cover_name, ebook_available, ebook_link))
            flash("Book added successfully.", "success")
        return redirect(url_for("adm_catalog"))

    # The page was just opened (GET): show an empty form, or the saved book when editing
    values = {}
    if existing:
        values = dict(existing)
        if existing["ebook_available"]:
            values["ebook_available"] = "yes"
        else:
            values["ebook_available"] = "no"
    return render_template("adm_book_form.html", active="catalog", values=values,
                           subjects=subjects, editing=bool(catalog_id))


# ---------- BOOKS: delete ----------
@app.route("/admin/catalog/delete/<int:catalog_id>", methods=["POST"])
@login_required("admin")
def adm_book_delete(catalog_id):
    book = fetch_one("SELECT title, cover_image FROM book_catalog WHERE catalog_id = %s", (catalog_id,))
    if book is None:
        flash("That book does not exist.", "error")
        return redirect(url_for("adm_catalog"))

    # a book that students are holding cannot be deleted
    open_loans = fetch_one("SELECT COUNT(*) AS n FROM loan_records WHERE catalog_id = %s "
                           "AND loan_status IN ('Borrowed','Overdue')", (catalog_id,))["n"]
    if open_loans > 0:
        flash("'" + book["title"] + "' cannot be deleted while copies are borrowed.", "error")
        return redirect(url_for("adm_catalog"))

    execute("DELETE FROM book_catalog WHERE catalog_id = %s", (catalog_id,))

    # also remove the cover picture file from the uploads folder
    if book["cover_image"]:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, book["cover_image"]))
        except OSError:
            pass                                  # if the file is already gone, that is fine

    flash("'" + book["title"] + "' was deleted.", "success")
    return redirect(url_for("adm_catalog"))


# ---------- CATEGORIES: list, add and edit ----------
@app.route("/admin/subjects", methods=["GET", "POST"])
@app.route("/admin/subjects/edit/<int:subject_id>", methods=["GET", "POST"])
@login_required("admin")
def adm_subjects(subject_id=None):
    editing = None
    if subject_id:
        editing = fetch_one("SELECT * FROM subjects WHERE subject_id = %s", (subject_id,))
        if editing is None:
            flash("That category does not exist.", "error")
            return redirect(url_for("adm_subjects"))

    if request.method == "POST":
        name = request.form.get("subject_name", "").strip()

        # is there already another category with the same name? (ignore upper/lower case)
        clash = fetch_one("SELECT subject_id FROM subjects WHERE LOWER(subject_name) = LOWER(%s) "
                          "AND subject_id <> %s", (name, subject_id or 0))

        if not name:
            flash("Category name cannot be empty.", "error")
        elif len(name) > 100:
            flash("Category name is too long (maximum 100 characters).", "error")
        elif clash:
            flash("That category already exists.", "error")
        else:
            if subject_id:
                execute("UPDATE subjects SET subject_name = %s WHERE subject_id = %s", (name, subject_id))
                flash("Category updated successfully.", "success")
            else:
                execute("INSERT INTO subjects (subject_name) VALUES (%s)", (name,))
                flash("Category added successfully.", "success")
            return redirect(url_for("adm_subjects"))

    # the table of categories, with how many books each one has
    subjects = fetch_all("""
        SELECT s.subject_id, s.subject_name, COUNT(b.catalog_id) AS book_count
        FROM subjects s LEFT JOIN book_catalog b ON b.subject_id = s.subject_id
        GROUP BY s.subject_id, s.subject_name ORDER BY s.subject_name""")
    return render_template("adm_subjects.html", active="subjects", subjects=subjects, editing=editing)


# ---------- CATEGORIES: delete ----------
@app.route("/admin/subjects/delete/<int:subject_id>", methods=["POST"])
@login_required("admin")
def adm_subject_delete(subject_id):
    used = fetch_one("SELECT COUNT(*) AS n FROM book_catalog WHERE subject_id = %s", (subject_id,))["n"]
    if used > 0:
        flash("This category is used by " + str(used) + " book(s) and cannot be deleted.", "error")
    else:
        execute("DELETE FROM subjects WHERE subject_id = %s", (subject_id,))
        flash("Category deleted.", "success")
    return redirect(url_for("adm_subjects"))

    # ============================================================
# PART 4b - ADMIN: students, borrow records, start the app  (Shoeib types this one)
# Paste this at the very bottom of the file (after Part 4a).
# ============================================================


# ---------- STUDENTS: list and search ----------
@app.route("/admin/members")
@login_required("admin")
def adm_members():
    term = request.args.get("q", "").strip()
    like = "%" + term + "%"                     # % means "anything", so this finds the text anywhere

    # open_loans = how many books each student is holding right now
    members = fetch_all("""
        SELECT a.*, (SELECT COUNT(*) FROM loan_records l WHERE l.account_id = a.account_id
                     AND l.loan_status IN ('Borrowed','Overdue')) AS open_loans
        FROM accounts a
        WHERE a.role = 'student'
          AND (%s = '' OR a.full_name LIKE %s OR a.email LIKE %s OR a.student_id LIKE %s)
        ORDER BY a.student_id""", (term, like, like, like))
    return render_template("adm_members.html", active="members", members=members, term=term)


# ---------- STUDENTS: add and edit (one page does both) ----------
@app.route("/admin/members/add", methods=["GET", "POST"], defaults={"account_id": None})
@app.route("/admin/members/edit/<int:account_id>", methods=["GET", "POST"])
@login_required("admin")
def adm_member_form(account_id):
    # When editing, load the student that is already saved
    existing = None
    if account_id:
        existing = fetch_one("SELECT * FROM accounts WHERE account_id = %s AND role = 'student'",
                             (account_id,))
        if existing is None:
            flash("That student does not exist.", "error")
            return redirect(url_for("adm_members"))

    # The form was submitted
    if request.method == "POST":
        form = request.form
        errors = []

        # 1. read the boxes
        student_id = form.get("student_id", "").strip()
        full_name = form.get("full_name", "").strip()
        email = form.get("email", "").strip().lower()
        course = form.get("course", "").strip() or None
        phone = form.get("phone", "").strip() or None
        status = form.get("status", "Active")
        password = form.get("password", "")

        # 2. check the boxes
        if not student_id:
            errors.append("Student ID is required.")
        if not full_name:
            errors.append("Name is required.")
        if not EMAIL_PATTERN.match(email):
            errors.append("Please enter a valid email address.")
        if phone and not PHONE_PATTERN.match(phone):
            errors.append("Phone number can only contain digits, spaces, + ( ) and -.")
        if status not in ("Active", "Inactive"):
            errors.append("Status must be Active or Inactive.")

        # the email and the student ID must not belong to someone else
        if fetch_one("SELECT account_id FROM accounts WHERE email = %s AND account_id <> %s",
                     (email, account_id or 0)):
            errors.append("That email address is already registered.")
        if student_id and fetch_one("SELECT account_id FROM accounts WHERE student_id = %s AND account_id <> %s",
                                    (student_id, account_id or 0)):
            errors.append("That student ID is already used.")

        # password rules: required when adding, optional when editing
        if not account_id and len(password) < 6:
            errors.append("A starting password of at least 6 characters is required.")
        if account_id and password and len(password) < 6:
            errors.append("The new password must be at least 6 characters.")

        # 3. if something is wrong, show the form again
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("adm_member_form.html", active="members", values=dict(form),
                                   editing=bool(account_id))

        # 4. save in the database
        if account_id:
            if password:       # only change the password when a new one was typed
                new_hash = generate_password_hash(password, method="pbkdf2:sha256")
                execute("""UPDATE accounts SET student_id=%s, full_name=%s, email=%s, course=%s,
                           phone=%s, status=%s, password_hash=%s WHERE account_id=%s""",
                        (student_id, full_name, email, course, phone, status, new_hash, account_id))
            else:
                execute("""UPDATE accounts SET student_id=%s, full_name=%s, email=%s, course=%s,
                           phone=%s, status=%s WHERE account_id=%s""",
                        (student_id, full_name, email, course, phone, status, account_id))
            flash("Student updated successfully.", "success")
        else:
            new_hash = generate_password_hash(password, method="pbkdf2:sha256")
            execute("""INSERT INTO accounts (student_id, full_name, email, password_hash, role,
                       course, phone, status) VALUES (%s,%s,%s,%s,'student',%s,%s,%s)""",
                    (student_id, full_name, email, new_hash, course, phone, status))
            flash("Student added successfully.", "success")
        return redirect(url_for("adm_members"))

    # The page was just opened (GET): empty form, or the saved student when editing
    values = {}
    if existing:
        values = dict(existing)
    return render_template("adm_member_form.html", active="members", values=values,
                           editing=bool(account_id))


# ---------- STUDENTS: delete ----------
@app.route("/admin/members/delete/<int:account_id>", methods=["POST"])
@login_required("admin")
def adm_member_delete(account_id):
    student = fetch_one("SELECT full_name FROM accounts WHERE account_id = %s AND role = 'student'",
                        (account_id,))
    if student is None:
        flash("That student does not exist.", "error")
        return redirect(url_for("adm_members"))

    # a student who still holds books cannot be deleted
    open_loans = fetch_one("SELECT COUNT(*) AS n FROM loan_records WHERE account_id = %s "
                           "AND loan_status IN ('Borrowed','Overdue')", (account_id,))["n"]
    if open_loans > 0:
        flash(student["full_name"] + " still has borrowed books and cannot be deleted.", "error")
        return redirect(url_for("adm_members"))

    execute("DELETE FROM accounts WHERE account_id = %s", (account_id,))
    flash(student["full_name"] + " was deleted.", "success")
    return redirect(url_for("adm_members"))


# ---------- BORROW RECORDS: read-only list with search and status filter ----------
@app.route("/admin/loans")
@login_required("admin")
def adm_loan_records():
    refresh_overdue()
    term = request.args.get("q", "").strip()          # search text
    status = request.args.get("status", "").strip()   # Borrowed / Returned / Overdue (or empty = all)
    like = "%" + term + "%"

    loans = fetch_all("""
        SELECT l.loan_id, a.full_name AS student_name, a.student_id, b.title AS book_title,
               l.borrow_date, l.due_date, l.return_date, l.loan_status
        FROM loan_records l
        JOIN accounts a ON l.account_id = a.account_id
        JOIN book_catalog b ON l.catalog_id = b.catalog_id
        WHERE (%s = '' OR l.loan_status = %s)
          AND (%s = '' OR a.full_name LIKE %s OR a.student_id LIKE %s OR b.title LIKE %s)
        ORDER BY l.loan_id DESC""", (status, status, term, like, like, like))
    return render_template("adm_loan_records.html", active="loans", loans=loans,
                           term=term, status=status)


# ---------- START THE APP (always the last lines of the file) ----------
if __name__ == "__main__":
    app.run(debug=True)       # debug=True shows detailed errors while we develop