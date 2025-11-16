from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware
from fastapi import HTTPException, Request, Depends
from google.oauth2.credentials import Credentials
from google.cloud import bigquery
import os
import logging

logger = logging.getLogger(__name__)

# Configuration from environment variables
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8081/auth/callback")
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "").split(",") if os.getenv("ALLOWED_DOMAINS") else []
# Allow oneupgames.gg domain by default
if not ALLOWED_DOMAINS:
    ALLOWED_DOMAINS = ["oneupgames.gg"]
PROJECT_ID = os.getenv("PROJECT_ID", "guns-and-gangs")

# OAuth Scopes
GOOGLE_SCOPES = [
    "openid",
    "email", 
    "profile",
    "https://www.googleapis.com/auth/bigquery"
]

# Initialize OAuth
oauth = OAuth()
# Register OAuth even if credentials are missing (will fail gracefully)
try:
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        oauth.register(
            name='google',
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={
                'scope': ' '.join(GOOGLE_SCOPES)
            },
            authorize_params={'state': None}
        )
    else:
        logger.warning("GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not set. OAuth will not work.")
except Exception as e:
    logger.error(f"Failed to register OAuth: {e}")

def get_current_user(request: Request):
    """Get current authenticated user from session"""
    user = request.session.get('user')
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def get_user_credentials(request: Request):
    """Get user's Google credentials from session"""
    token = request.session.get('token')
    if not token:
        raise HTTPException(status_code=401, detail="No authentication token")
    
    credentials = Credentials(
        token=token.get('access_token'),
        refresh_token=token.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=GOOGLE_SCOPES
    )
    
    return credentials

def get_user_bigquery_client(request: Request):
    """Get BigQuery client using user's credentials"""
    credentials = get_user_credentials(request)
    return bigquery.Client(
        credentials=credentials,
        project=PROJECT_ID
    )

def check_domain_access(email: str) -> bool:
    """Check if user's email domain is allowed"""
    if not ALLOWED_DOMAINS:
        return True  # No domain restriction
    
    domain = email.split('@')[-1]
    return domain in ALLOWED_DOMAINS

async def require_auth(request: Request):
    """Dependency to require authentication"""
    user = get_current_user(request)
    
    # Check domain access if configured
    if ALLOWED_DOMAINS and not check_domain_access(user.get('email', '')):
        raise HTTPException(status_code=403, detail="Domain not allowed")
    
    return user

