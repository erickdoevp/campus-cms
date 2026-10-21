from django.conf import settings
from django.db import models


class LmsAccount(models.Model):
    """
    Links a CMS user to their Open edX account. Users never get a CMS
    password: they log in to the LMS and the CMS trusts the LMS JWT.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lms_account")
    lms_user_id = models.PositiveIntegerField(unique=True)

    def __str__(self):
        return f"{self.user.username} (LMS {self.lms_user_id})"
