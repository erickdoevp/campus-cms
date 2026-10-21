"""
Authentication with the JWT the LMS issues at login.

The LMS splits it into two cookies on the shared domain
(``edx-jwt-cookie-header-payload`` and ``edx-jwt-cookie-signature``); Campus'
server joins them and sends ``Authorization: JWT <token>``. The token is
signed with the LMS private RSA key, so the CMS only needs the public set.
"""
import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import authentication, exceptions

from .models import LmsAccount

ALGORITHMS = ["RS256", "RS512"]


def decode_jwt(token):
    try:
        keys = jwt.PyJWKSet.from_json(settings.OPENEDX_JWT_PUBLIC_JWK_SET).keys
    except jwt.PyJWTError as exc:
        raise exceptions.AuthenticationFailed("The CMS has no valid Open edX public key.") from exc
    for key in keys:
        try:
            return jwt.decode(
                token,
                key.key,
                algorithms=ALGORITHMS,
                audience=settings.OPENEDX_JWT_AUDIENCE,
                issuer=settings.OPENEDX_JWT_ISSUER,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.InvalidSignatureError:
            continue  # signed with another key of the set
        except jwt.PyJWTError as exc:
            raise exceptions.AuthenticationFailed(f"Invalid JWT: {exc}") from exc
    raise exceptions.AuthenticationFailed("Invalid JWT signature.")


@transaction.atomic
def user_from_claims(claims):
    """Return the CMS user for the token, creating or updating it."""
    lms_user_id = claims.get("user_id")
    username = claims.get("preferred_username")
    if not lms_user_id or not username:
        raise exceptions.AuthenticationFailed("The JWT has no user.")

    account = LmsAccount.objects.select_related("user").filter(lms_user_id=lms_user_id).first()
    if account is not None:
        user = account.user
    else:
        # A role can be granted before the first login (see the `contador`
        # command): that user only has a username, link it now.
        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if user is None:
            user = User(username=username)
            user.set_unusable_password()
            user.save()
        elif hasattr(user, "lms_account"):
            raise exceptions.AuthenticationFailed("This username belongs to another Open edX account.")
        LmsAccount.objects.create(user=user, lms_user_id=lms_user_id)

    email = claims.get("email") or ""
    if (user.username, user.email) != (username, email):
        user.username, user.email = username, email
        user.save(update_fields=["username", "email"])
    if not user.is_active:
        raise exceptions.AuthenticationFailed("User inactive.")
    return user


class OpenEdxJwtAuthentication(authentication.BaseAuthentication):
    keyword = "JWT"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.")
        claims = decode_jwt(header[1].decode())
        if claims.get("is_restricted"):
            # Tokens of restricted OAuth applications carry limited scopes.
            raise exceptions.AuthenticationFailed("Restricted tokens are not accepted.")
        return user_from_claims(claims), claims

    def authenticate_header(self, request):
        return self.keyword
