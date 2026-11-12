#!/usr/bin/env python3
"""
Initialize portfolio database for first-time deployment.

Run this once after deploying to PythonAnywhere:
    python init_database.py

This will:
1. Create SQLite schema
2. Create default users (change passwords afterward!)
3. Show the next steps
"""

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

def main():
    """Run initialization steps."""
    print("\n🚀 Portfolio Manager - Database Initialization\n")
    
    try:
        init_database()
        
        print("\n✅ Initialization complete!")
        print("\n📝 Next steps:")
        print("   1. Visit your app: https://yourusername.pythonanywhere.com")
        print("   2. Set the admin password: python -m web.set_password admin")
        print("   3. Put your holdings file in place (Stocks.csv, or set PORTFOLIO_CSV_PATH)")
        
    except Exception as e:
        print(f"\n❌ Error during initialization: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
