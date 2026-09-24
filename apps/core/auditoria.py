"""Registro explícito de operaciones de negocio, sin secretos ni archivos."""
from apps.core.models import RegistroAuditoria


def registrar(objeto, usuario, accion, antes=None, despues=None):
    return RegistroAuditoria.objects.create(
        empresa_id=objeto.empresa_id, usuario=usuario,
        modelo=objeto._meta.label, objeto_id=str(objeto.pk), accion=accion,
        valores_antes=antes or {}, valores_despues=despues or {},
    )
