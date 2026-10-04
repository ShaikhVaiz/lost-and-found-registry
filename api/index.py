import sys
import os
import urllib.parse

# Ensure the root project directory is on Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# WSGI Middleware to extract original path on Vercel and prevent redirect loops
class VercelPathMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        matched_path = environ.get("HTTP_X_MATCHED_PATH")
        
        query = environ.get("QUERY_STRING", "")
        params = urllib.parse.parse_qs(query)
        path_param = params.get("path", [None])[0]

        real_path = matched_path or path_param

        if real_path:
            while real_path.startswith("//"):
                real_path = real_path[1:]
            if not real_path.startswith("/"):
                real_path = "/" + real_path
            if real_path not in ("/api/index", "/api/index.py"):
                environ["PATH_INFO"] = real_path

        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelPathMiddleware(app.wsgi_app)
