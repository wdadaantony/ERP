"""Contrato que cumple todo conector.

El núcleo nunca llama a un servicio externo directamente: encola un trabajo, y un
trabajador toma ese trabajo y se lo entrega al conector correspondiente. Así,
cambiar de pasarela o de OSE es cambiar una sola clase.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field

from django.utils import timezone

from apps.integraciones.models import (
    RegistroIntegracion,
    ServicioExterno,
    TrabajoIntegracion,
)


class ErrorConector(Exception):
    """Falla al hablar con el servicio externo. Dispara el reintento."""

    def __init__(self, mensaje, codigo_http=None, recuperable=True):
        super().__init__(mensaje)
        self.codigo_http = codigo_http
        self.recuperable = recuperable


@dataclass
class Respuesta:
    """Lo que devuelve un conector, ya normalizado."""

    exito: bool
    id_externo: str = ""
    codigo_http: int | None = None
    datos: dict = field(default_factory=dict)
    mensaje: str = ""


class Conector:
    """Base de todos los conectores.

    Las subclases implementan `_ejecutar`; esta clase se encarga de lo repetido:
    medir el tiempo, escribir la bitácora y traducir las fallas a reintentos.
    """

    #: Código de ServicioExterno.Codigo que atiende este conector.
    codigo_servicio: str = ""
    soporta_modo_real = False

    @property
    def credenciales(self):
        from apps.integraciones.configuracion import credenciales
        return credenciales(self.servicio)
    #: Operaciones que sabe atender.
    operaciones: tuple[str, ...] = ()

    def __init__(self, servicio: ServicioExterno):
        if self.codigo_servicio and servicio.codigo != self.codigo_servicio:
            raise ValueError(
                f"{type(self).__name__} no atiende servicios de tipo «{servicio.codigo}»"
            )
        self.servicio = servicio

    # -- API pública ----------------------------------------------------

    def ejecutar(self, operacion: str, carga: dict, objeto_id: str = "", entidad: str = ""):
        """Ejecuta una operación dejando el rastro completo en la bitácora."""
        if not self.servicio.activo:
            raise ErrorConector('El servicio está desactivado.', recuperable=False)
        if not self.servicio.modo_simulado and not self.soporta_modo_real:
            raise ErrorConector('Instala y valida el adaptador de este proveedor antes de activar el modo real.', recuperable=False)
        if self.operaciones and operacion not in self.operaciones:
            raise ErrorConector(
                f"{type(self).__name__} no conoce la operación «{operacion}»", recuperable=False
            )

        from apps.integraciones.configuracion import ocultar_secretos
        registro = RegistroIntegracion.objects.create(
            empresa=self.servicio.empresa,
            servicio=self.servicio,
            direccion=RegistroIntegracion.Direccion.SALIDA,
            operacion=operacion,
            entidad=entidad,
            objeto_id=objeto_id,
            contenido_enviado=ocultar_secretos(carga),
            estado=RegistroIntegracion.Estado.ENVIADO,
            intentos=1,
        )
        inicio = time.monotonic()
        try:
            respuesta = self._ejecutar(operacion, carga)
        except ErrorConector as exc:
            registro.estado = RegistroIntegracion.Estado.ERROR
            registro.mensaje_error = str(exc)
            registro.codigo_http = exc.codigo_http
            registro.duracion_ms = int((time.monotonic() - inicio) * 1000)
            registro.save()
            raise
        registro.estado = (
            RegistroIntegracion.Estado.EXITO
            if respuesta.exito
            else RegistroIntegracion.Estado.ERROR
        )
        registro.contenido_recibido = ocultar_secretos(respuesta.datos)
        registro.id_externo = respuesta.id_externo
        registro.codigo_http = respuesta.codigo_http
        registro.mensaje_error = "" if respuesta.exito else respuesta.mensaje
        registro.duracion_ms = int((time.monotonic() - inicio) * 1000)
        registro.save()
        return respuesta

    def encolar(self, operacion: str, carga: dict, llave: str, objeto_id="", entidad=""):
        """Deja el trabajo en la cola. Si ya existe esa llave, no se duplica."""
        trabajo, _ = TrabajoIntegracion.objects.get_or_create(
            servicio=self.servicio,
            llave_idempotencia=llave,
            defaults={
                "empresa": self.servicio.empresa,
                "operacion": operacion,
                "carga": carga,
                "objeto_id": str(objeto_id),
                "entidad": entidad,
                "ejecutar_despues_de": timezone.now(),
            },
        )
        return trabajo

    def validar_firma(self, cuerpo: bytes, firma: str) -> bool:
        """Comprueba la firma HMAC de un webhook antes de creerle nada."""
        from apps.integraciones.configuracion import secreto as resolver_secreto
        from django.core.exceptions import ValidationError
        try:
            secreto = resolver_secreto(self.servicio.secreto_webhook)
        except ValidationError:
            return False
        if not secreto:
            return False
        esperada = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
        return hmac.compare_digest(esperada, firma or "")

    # -- A implementar por cada conector --------------------------------

    def _ejecutar(self, operacion: str, carga: dict) -> Respuesta:
        raise NotImplementedError

    def procesar_webhook(self, evento):
        """Traduce un evento entrante a cambios en el ERP. Opcional por conector."""
        raise NotImplementedError


_REGISTRO: dict[str, type[Conector]] = {}


def registrar(cls: type[Conector]) -> type[Conector]:
    """Decorador que apunta un conector para poder buscarlo por código de servicio."""
    _REGISTRO[cls.codigo_servicio] = cls
    return cls


def obtener_conector(servicio: ServicioExterno) -> Conector:
    from django.conf import settings
    from django.utils.module_loading import import_string
    ruta = getattr(settings, 'ERP_CONNECTOR_BACKENDS', {}).get(f'{servicio.codigo}:{servicio.proveedor}')
    if ruta:
        cls = import_string(ruta)
        if not issubclass(cls, Conector):
            raise ErrorConector('El adaptador debe implementar el contrato Conector.', recuperable=False)
        return cls(servicio)
    cls = _REGISTRO.get(servicio.codigo)
    if cls is None:
        raise ErrorConector(
            f"No hay conector registrado para «{servicio.codigo}»", recuperable=False
        )
    return cls(servicio)
