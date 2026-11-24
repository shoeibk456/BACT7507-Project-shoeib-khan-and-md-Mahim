import mysql.connector          # the tool that lets Python talk to MySQL


DB_HOST = "localhost"           
DB_USER = "root"                
DB_NAME = "libconnect_db"       
DB_PASSWORD = "ad0*7tqqyaYK"               


try:
    from secret_keys import DB_PASSWORD
except ImportError:
    pass                        


# ---------- 2. OPEN A CONNECTION ----------
def get_db_connection():
    # app.py uses this directly for borrow/return, where we need a transaction
    return mysql.connector.connect(host=DB_HOST, user=DB_USER,
                                   password=DB_PASSWORD, database=DB_NAME)


# ---------- 3. READ MANY ROWS (SELECT) ----------
def fetch_all(sql, params=()):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)    # dictionary=True: each row is like {"title": "..."}
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()             # a list of rows (an empty list if nothing found)
    finally:
        cursor.close()                       # always close, even if there was an error
        conn.close()


# ---------- 4. READ ONE ROW (SELECT) ----------
def fetch_one(sql, params=()):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()             # one row, or None if nothing found
    finally:
        cursor.close()
        conn.close()


# ---------- 5. CHANGE DATA (INSERT / UPDATE / DELETE) ----------
def execute(sql, params=()):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        conn.commit()                        # save the change
        return cursor.lastrowid              # the new id after an INSERT
    except Exception:
        conn.rollback()                      # something went wrong: undo the change
        raise                                # pass the error on so app.py can show a message
    finally:
        cursor.close()
        conn.close()