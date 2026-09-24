from django.db import connection, DatabaseError
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def salud(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({'estado': 'no_disponible'}, status=503)
    respuesta = JsonResponse({'estado': 'disponible'})
    respuesta['Cache-Control'] = 'no-store'
    return respuesta
