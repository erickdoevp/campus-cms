import time

from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import LmsAccount
from core.permissions import CONTADORES

from .utils import auth_header, make_jwt, use_test_jwt_keys


@use_test_jwt_keys
class JwtAuthenticationTests(TestCase):
    def get_me(self, **headers):
        return self.client.get(reverse("me"), **headers)

    def test_no_token_is_unauthorized(self):
        response = self.get_me()
        assert response.status_code == 401
        assert response["WWW-Authenticate"] == "JWT"

    def test_first_login_creates_the_user(self):
        response = self.get_me(**auth_header(user_id=7, username="ana", email="ana@example.com"))

        assert response.status_code == 200, response.content
        assert response.json() == {
            "username": "ana", "email": "ana@example.com", "is_contador": False, "course_org": settings.COURSE_ORG,
        }
        account = LmsAccount.objects.get(lms_user_id=7)
        assert account.user.username == "ana"
        assert not account.user.has_usable_password()

    def test_username_and_email_follow_the_lms(self):
        self.get_me(**auth_header(user_id=7, username="ana", email="ana@example.com"))
        self.get_me(**auth_header(user_id=7, username="ana.lopez", email="ana@nuevo.com"))

        user = LmsAccount.objects.get(lms_user_id=7).user
        assert (user.username, user.email) == ("ana.lopez", "ana@nuevo.com")
        assert User.objects.count() == 1

    def test_role_granted_before_first_login_is_kept(self):
        call_command("contador", "ana")
        response = self.get_me(**auth_header(user_id=7, username="ana"))

        assert response.json()["is_contador"] is True
        assert LmsAccount.objects.get(lms_user_id=7).user.groups.filter(name=CONTADORES).exists()

    def test_contador_role_can_be_removed(self):
        call_command("contador", "ana")
        call_command("contador", "ana", "--remove")
        assert self.get_me(**auth_header(username="ana")).json()["is_contador"] is False

    def test_rejected_tokens(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(time.time())
        tokens = {
            "wrong key": make_jwt(key=other_key),
            "wrong issuer": make_jwt(iss="http://evil.test/oauth2"),
            "wrong audience": make_jwt(aud="other"),
            "expired": make_jwt(iat=now - 7200, exp=now - 3600),
            "restricted": make_jwt(is_restricted=True),
            "no user": make_jwt(user_id=None),
        }
        for reason, token in tokens.items():
            with self.subTest(reason):
                assert self.get_me(HTTP_AUTHORIZATION=f"JWT {token}").status_code == 401

    def test_username_of_another_account_is_rejected(self):
        self.get_me(**auth_header(user_id=7, username="ana"))
        assert self.get_me(**auth_header(user_id=8, username="ana")).status_code == 401

    def test_contadores_group_exists(self):
        assert Group.objects.filter(name=CONTADORES).exists()
