"""
Client for the Open edX APIs, authenticated as the CMS service account.

The LMS issues a JWT for the client-credentials application; the token
carries the service user (staff), which Studio's course run API requires.
"""
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache

TOKEN_CACHE_KEY = "openedx:service-token"
TIMEOUT = 15
# Studio writes that touch course settings (dates, team) republish and
# reindex the course before answering, which takes well over 15 s in dev.
WRITE_TIMEOUT = 90


class OpenEdxError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _access_token():
    token = cache.get(TOKEN_CACHE_KEY)
    if token:
        return token
    response = requests.post(
        f"{settings.OPENEDX_LMS_URL}/oauth2/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": settings.OPENEDX_OAUTH2_CLIENT_ID,
            "client_secret": settings.OPENEDX_OAUTH2_CLIENT_SECRET,
            "token_type": "jwt",
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise OpenEdxError(f"Could not get a token from the LMS ({response.status_code}): {response.text[:200]}")
    data = response.json()
    token = data["access_token"]
    # Refresh a minute early so a request never goes out with an expired token.
    cache.set(TOKEN_CACHE_KEY, token, max(data.get("expires_in", 3600) - 60, 60))
    return token


def request(method, url, **kwargs):
    headers = {"Authorization": f"JWT {_access_token()}", **kwargs.pop("headers", {})}
    timeout = TIMEOUT if method == "GET" else WRITE_TIMEOUT
    try:
        response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        # Callers handle OpenEdxError (e.g. "retry later"); a timeout must not become a 500.
        raise OpenEdxError(f"{method} {url} -> {type(exc).__name__}: {exc}") from exc
    if response.status_code >= 400:
        raise OpenEdxError(f"{method} {url} -> {response.status_code}: {response.text[:300]}", response.status_code)
    return response.json() if response.content else None


def lms(method, path, **kwargs):
    return request(method, f"{settings.OPENEDX_LMS_URL}{path}", **kwargs)


def studio(method, path, **kwargs):
    return request(method, f"{settings.OPENEDX_STUDIO_URL}{path}", **kwargs)


def user_exists(username):
    try:
        lms("GET", f"/api/user/v1/accounts/{quote(username)}")
    except OpenEdxError as exc:
        if exc.status_code == 404:
            return False
        raise
    return True
