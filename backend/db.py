import sqlite3
conn = sqlite3.connect("app.db")
conn.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, amount REAL, category TEXT, date TEXT)")
conn.commit()
