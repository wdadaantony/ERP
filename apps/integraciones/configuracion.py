"""Resolución de secretos externos y diagnóstico local: nunca realiza conexiones."""
import json
import os
from django.conf import settings
from django.core.exceptions import ValidationError


def secreto(valor):
    if str(valor).startswith('env:'):
        nombre = valor[4:]
        if not nombre or not os.getenv(nombre):
            raise ValidationError(f'Falta configurar la variable de entorno {nombre}.')
        return os.environ[nombre]
    if valor and settings.ERP_REQUIRE_ENV_SECRETS:
        raise ValidationError('En producción los secretos deben referenciar env:NOMBRE_VARIABLE.')
    return valor


def credenciales(servicio):
    datos = servicio.credenciales or {}
    if not isinstance(datos, dict):
        raise ValidationError('Las credenciales deben ser un objeto JSON.')
    if '_env' in datos:
        try:
            datos = json.loads(secreto('env:' + datos['_env']))
        except (ValueError, TypeError):
            raise ValidationError('La variable de credenciales debe contener un objeto JSON válido.')
        if not isinstance(datos, dict):
            raise ValidationError('Las credenciales deben ser un objeto JSON.')
        return datos
    if settings.ERP_REQUIRE_ENV_SECRETS and any(not isinstance(valor, str) for valor in datos.values()):
        raise ValidationError('Usa referencias env:VARIABLE o _env para credenciales estructuradas.')
    return {clave: secreto(valor) if isinstance(valor, str) else valor for clave, valor in datos.items()}


def diagnostico(servicio):
    from apps.integraciones.conectores import obtener_conector
    if not servicio.activo:
        return 'Desactivado'
    if servicio.modo_simulado:
        return 'Simulado · no realiza conexiones'
    try:
        conector = obtener_conector(servicio)
        if not conector.soporta_modo_real:
            return 'Pendiente de instalar adaptador del proveedor'
        credenciales(servicio)
        secreto(servicio.secreto_webhook)
        return 'Configurado · pendiente de verificar con el proveedor'
    except Exception:
        return 'Configuración incompleta: revisar adaptador y variables de entorno'


def ocultar_secretos(datos):
    claves = ('token', 'password', 'secret', 'authorization', 'api_key', 'clave', 'credencial', 'archivos')
    if isinstance(datos, dict):
        return {k: '[oculto]' if any(c in str(k).lower() for c in claves) else ocultar_secretos(v) for k, v in datos.items()}
    if isinstance(datos, list):
        return [ocultar_secretos(v) for v in datos]
    return datos
