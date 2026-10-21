from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """GET /api/health/ — the service is up and can reach its database."""
    connection.ensure_connection()
    return Response({"status": "ok"})
