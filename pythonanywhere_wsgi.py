"""
WSGI application for PythonAnywhere.

PythonAnywhere runs WSGI applications, not ASGI directly.
This wraps the FastAPI app with uvicorn's ASGI-to-WSGI adapter.

Configuration:
1. Upload this file to /home/yourusername/mysite/pythonanywhere_wsgi.py
2. In PythonAnywhere Web tab:
   - Source code: /home/yourusername/mysite
   - WSGI configuration file: /home/yourusername/mysite/pythonanywhere_wsgi.py
3. Set environment variables in Web tab (not via .env):
   - WEB_SECURE_COOKIES=true
   - STORAGE_DB_PATH=/home/yourusername/mysite/data/portfolio_history.sqlite3
   - GOOGLE_SERVICE_ACCOUNT_FILE=/home/yourusername/mysite/secrets/service-account.json
   - (all other .env variables)
"""

import os
import sys
from pathlib import Path

# Add your site to the path
site_dir = Path(__file__).parent
sys.path.insert(0, str(site_dir))

# Create data directory if needed
data_dir = site_dir / "data"
data_dir.mkdir(exist_ok=True)

# Import FastAPI app
from web.app import app

# Use asgiref WSGI-to-ASGI adapter (included with FastAPI/uvicorn)
# PythonAnywhere will call this WSGI application
from asgiref.wsgi import WsgiToAsgi

# Wrap FastAPI ASGI app for WSGI compatibility
application = WsgiToAsgi(app)
