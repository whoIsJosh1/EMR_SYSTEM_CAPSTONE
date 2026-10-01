# ============================================================
# reset_admin_password.py v2
# I-run ito ONCE pagkatapos ma-setup ang database
# para ma-fix ang admin password hash.
#
# Usage:
#   python reset_admin_password.py
# ============================================================
import sys, os
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal, get_barangay_name
from models.models import User
from utils.security import hash_password, verify_password

def reset():
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.role == "admin").first()
        if not admin:
            print("❌ No admin account found. Run schema.sql first.")
            return

        new_pw   = "Admin@123"
        new_hash = hash_password(new_pw)
        admin.password_hash   = new_hash
        admin.failed_attempts = 0
        admin.locked_until    = None
        admin.status          = "active"
        db.commit()

        if verify_password(new_pw, new_hash):
            print(f"✅ Admin password reset — database: {get_barangay_name()}")
            print(f"   Email:    {admin.email}")
            print(f"   Password: {new_pw}")
            print("   ⚠️  Change this password after first login!")
        else:
            print("❌ Verification failed.")
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    reset()