from django.contrib import admin

from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "number", "run", "status", "price", "course_key", "modified")
    list_filter = ("status", "pacing")
    search_fields = ("title", "number", "course_key")
    readonly_fields = ("org", "course_key", "created_by", "created", "modified")
