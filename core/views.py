from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .permissions import is_contador


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """GET /api/health/ — the service is up and can reach its database."""
    connection.ensure_connection()
    return Response({"status": "ok"})


@api_view(["GET"])
def me(request):
    """GET /api/v1/me/ — who is calling and what they can do."""
    return Response({
        "username": request.user.username,
        "email": request.user.email,
        "is_contador": is_contador(request.user),
    })
