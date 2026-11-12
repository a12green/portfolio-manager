#!/usr/bin/env python3
"""
Initialize portfolio database for first-time deployment.

Run this once after deploying to PythonAnywhere:
    python init_database.py

This will:
1. Create SQLite schema
2. Create default users (change passwords afterward!)
3. Test Google Sheets connection (optional)
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def init_database():
    """Initialize SQLite database with schema."""
    from ledger.db import connect_db, init_schema
    from web.auth import seed_default_users
    from ledger.trades_db import init_trade_schema
    
    print("📁 Initializing database...")
    
    # Ensure data directory exists
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)
    print(f"   ✓ Data directory: {data_dir}")
    
    # Initialize main portfolio database
    conn = connect_db(data_dir / "portfolio_history.sqlite3")
    try:
        init_schema(conn)
        print("   ✓ Portfolio schema created")
    finally:
        conn.close()
    
    # Initialize trades database
    conn = connect_db(data_dir / "portfolio_history.sqlite3")
    try:
        init_trade_schema(conn)
        seed_default_users(conn)
        print("   ✓ Trades schema created")
        print("   ✓ Admin account created (locked until a password is set)")
        print("\n   ⚠️  Set the admin password now: python -m web.set_password admin")
    finally:
        conn.close()

def test_google_sheets():
    """Test Google Sheets connection (optional)."""
    from dotenv import load_dotenv
    import gspread
    from google.oauth2.service_account import Credentials
    
    load_dotenv()
    
    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    
    if not service_account or not sheet_id:
        print("\n⏭️  Skipping Google Sheets test (not configured in .env)")
        return
    
    try:
        print("\n📊 Testing Google Sheets connection...")
        creds = Credentials.from_service_account_file(
            service_account,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id)
        print(f"   ✓ Connected to spreadsheet: '{sheet.title}'")
        print(f"   ✓ Worksheets: {', '.join(ws.title for ws in sheet.worksheets()[:5])}")
    except FileNotFoundError:
        print(f"   ✗ Service account file not found: {service_account}")
        print("     → Upload service-account.json to PythonAnywhere")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        print("     → Check GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_SHEETS_SPREADSHEET_ID")

def main():
    """Run initialization steps."""
    print("\n🚀 Portfolio Manager - Database Initialization\n")
    
    try:
        init_database()
        test_google_sheets()
        
        print("\n✅ Initialization complete!")
        print("\n📝 Next steps:")
        print("   1. Visit your app: https://yourusername.pythonanywhere.com")
        print("   2. Set the admin password: python -m web.set_password admin")
        print("   3. Upload portfolio data or import from Google Sheets")
        
    except Exception as e:
        print(f"\n❌ Error during initialization: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
