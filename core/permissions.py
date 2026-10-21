from rest_framework.permissions import BasePermission

# Created by migration core/0002. Members manage courses, prices and sales.
CONTADORES = "Contadores"


def is_contador(user):
    return bool(user and user.is_authenticated) and (
        user.is_superuser or user.groups.filter(name=CONTADORES).exists()
    )


class IsContador(BasePermission):
    message = "Solo los contadores pueden hacer esto."

    def has_permission(self, request, view):
        return is_contador(request.user)
