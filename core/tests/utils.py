"""Signs JWTs like the LMS does, with a throwaway RSA key."""
import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

ISSUER = "http://lms.test/oauth2"
AUDIENCE = "openedx"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_private_key.public_key()))
JWK_SET = json.dumps({"keys": [{**_public_jwk, "kid": "openedx"}]})

use_test_jwt_keys = override_settings(
    OPENEDX_JWT_PUBLIC_JWK_SET=JWK_SET, OPENEDX_JWT_ISSUER=ISSUER, OPENEDX_JWT_AUDIENCE=AUDIENCE,
)


def make_jwt(user_id=7, username="contadora", email="contadora@example.com", key=_private_key, **claims):
    now = int(time.time())
    payload = {
        "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 3600,
        "user_id": user_id, "preferred_username": username, "email": email,
        "is_restricted": False, **claims,
    }
    return jwt.encode(payload, key, algorithm="RS512")


def auth_header(**kwargs):
    return {"HTTP_AUTHORIZATION": f"JWT {make_jwt(**kwargs)}"}
