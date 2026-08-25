from backend.db.db import get_connection
from backend.engine.mark_payment_recovered import mark_payment_recovered

conn = get_connection()
print(mark_payment_recovered("pay_673114bf176f4d", conn))   # expect status "ok"
print(mark_payment_recovered("pay_673114bf176f4d", conn))   # expect "already_recovered"
print(mark_payment_recovered("pay_doesnotexist", conn))     # expect "payment_not_found"
conn.close()