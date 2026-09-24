"""Qué hacer con cada evento que llega de afuera.

El receptor HTTP solo guarda y responde rápido; la traducción a cambios en el ERP
vive aquí. Un evento que falla al procesarse queda con `procesado=False` y su
mensaje de error, listo para reprocesar: nunca se pierde.

Todo lo que llega es dato de un tercero, no una orden: se valida la firma antes
de creerle, y cada manejador comprueba que el documento referido exista y esté en
un estado donde el cambio tenga sentido.
"""
import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.integraciones.models import ServicioExterno

logger = logging.getLogger(__name__)

_MANEJADORES = {}


def webhook_de(codigo_servicio):
    """Registra el manejador de los eventos de un tipo de servicio."""

    def decorador(funcion):
        _MANEJADORES[codigo_servicio] = funcion
        return funcion

    return decorador


class EventoNoAplicable(ValidationError):
    """El evento llegó bien pero no hay nada que hacer con él."""


@transaction.atomic
def procesar_evento(evento):
    """Aplica un evento al ERP. Devuelve el texto de lo que hizo.

    Es idempotente: un evento ya procesado no se vuelve a aplicar.
    """
    if evento.procesado:
        return "ya estaba procesado"
    if not evento.firma_valida:
        raise ValidationError("No se procesa un evento con firma inválida.")

    manejador = _MANEJADORES.get(evento.servicio.codigo)
    if manejador is None:
        raise EventoNoAplicable(
            f"No hay manejador de webhooks para «{evento.servicio.codigo}»."
        )

    resultado = manejador(evento)
    evento.procesado = True
    evento.mensaje_error = ""
    evento.save(update_fields=["procesado", "mensaje_error", "actualizado_en"])
    return resultado


def reprocesar_pendientes(empresa=None, limite=100):
    """Reintenta los eventos que quedaron sin procesar. Devuelve (hechos, fallidos)."""
    from apps.integraciones.models import EventoWebhook

    consulta = EventoWebhook.objects.filter(procesado=False, firma_valida=True)
    if empresa is not None:
        consulta = consulta.filter(empresa=empresa)

    hechos = fallidos = 0
    for evento in consulta.select_related("servicio")[:limite]:
        try:
            procesar_evento(evento)
            hechos += 1
        except Exception as exc:  # noqa: BLE001 - uno malo no detiene al resto
            evento.mensaje_error = str(exc)
            evento.save(update_fields=["mensaje_error", "actualizado_en"])
            fallidos += 1
            logger.warning("Evento %s no se pudo procesar: %s", evento.pk, exc)
    return hechos, fallidos


# -- Manejadores por servicio ---------------------------------------------


@webhook_de(ServicioExterno.Codigo.OSE)
def _webhook_del_ose(evento):
    """El OSE avisa el resultado de un comprobante: aceptado, observado o rechazado."""
    from apps.facturacion.models import Comprobante
    from apps.facturacion.servicios import aplicar_respuesta

    cuerpo = evento.cuerpo or {}
    comprobante = _buscar_comprobante(evento.empresa, cuerpo)
    if comprobante is None:
        raise EventoNoAplicable(
            f"El aviso apunta a un comprobante que no existe: {cuerpo.get('numero_completo')}"
        )

    aplicar_respuesta(comprobante, cuerpo, cuerpo.get("id_externo", ""))
    return f"{comprobante.numero_completo} → {comprobante.estado}"


def _buscar_comprobante(empresa, cuerpo):
    from apps.facturacion.models import Comprobante

    numero = cuerpo.get("numero_completo", "")
    if numero and "-" in numero:
        serie, correlativo = numero.split("-", 1)
        if correlativo.isdigit():
            return Comprobante.objects.filter(
                empresa=empresa, serie=serie, correlativo=int(correlativo)
            ).first()
    if cuerpo.get("id_externo"):
        return Comprobante.objects.filter(
            empresa=empresa, id_externo=cuerpo["id_externo"]
        ).first()
    return None


@webhook_de(ServicioExterno.Codigo.PASARELA)
def _webhook_de_la_pasarela(evento):
    """La pasarela avisa un cobro. La referencia es lo que evita duplicarlo."""
    from decimal import Decimal

    from apps.facturacion.models import Comprobante
    from apps.tesoreria.models import MetodoPago
    from apps.tesoreria.servicios import registrar_cobro

    cuerpo = evento.cuerpo or {}
    estado = str(cuerpo.get("estado", "")).lower()
    if estado not in ("pagado", "aprobado", "exitoso"):
        raise EventoNoAplicable(f"Aviso de pago en estado «{estado}»: no hay nada que cobrar.")

    comprobante = _buscar_comprobante(evento.empresa, cuerpo)
    if comprobante is None:
        raise EventoNoAplicable(
            f"El aviso de pago apunta a un comprobante que no existe: "
            f"{cuerpo.get('numero_completo')}"
        )

    if cuerpo.get("monto") is None:
        raise EventoNoAplicable("El aviso debe indicar el importe exacto del pago.")
    monto = Decimal(str(cuerpo["monto"]))

    movimiento = registrar_cobro(
        comprobante,
        monto,
        metodo=MetodoPago.PASARELA,
        referencia_externa=cuerpo.get("referencia", "") or evento.id_evento_externo,
        comision=Decimal(str(cuerpo.get("comision", 0))),
        tipo_cambio=cuerpo.get("tipo_cambio"),
    )
    return f"cobro {movimiento.monto} sobre {comprobante.numero_completo}"
