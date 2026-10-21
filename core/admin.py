from django.contrib import admin

from .models import LmsAccount


@admin.register(LmsAccount)
class LmsAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "lms_user_id")
    search_fields = ("user__username", "user__email", "lms_user_id")
    raw_id_fields = ("user",)
