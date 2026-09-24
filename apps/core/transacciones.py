"""Serializa las escrituras operativas por empresa, también en SQLite.

El bloqueo se toma antes de leer saldos/stock y vive hasta el commit exterior.
Es deliberadamente grueso: prioriza integridad antes de optimizar paralelismo.
"""
from functools import wraps

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F


def bloquear_empresa(empresa_id):
    from apps.core.models import Empresa

    empresas = Empresa.objects.filter(pk=empresa_id)
    if connection.vendor == "sqlite":
        # SELECT FOR UPDATE no bloquea filas en SQLite. Esta escritura obtiene
        # el bloqueo de escritor sin cambiar datos ni disparar señales.
        existe = empresas.update(activa=F("activa"))
    else:
        existe = empresas.select_for_update().exists()
    if not existe:
        raise ValidationError("La empresa de la operación no existe.")


def operacion_serializada(funcion):
    @wraps(funcion)
    def ejecutar(objeto, *args, **kwargs):
        empresa_id = getattr(objeto, "empresa_id", None) or objeto.pk
        with transaction.atomic():
            bloquear_empresa(empresa_id)
            if getattr(objeto, "empresa_id", None) and objeto.pk:
                objeto.refresh_from_db()
            return funcion(objeto, *args, **kwargs)
    return ejecutar


def vista_serializada(funcion):
    @wraps(funcion)
    def ejecutar(request, *args, **kwargs):
        if request.method != "POST":
            return funcion(request, *args, **kwargs)
        with transaction.atomic():
            bloquear_empresa(request.empresa.pk)
            return funcion(request, *args, **kwargs)
    return ejecutar
