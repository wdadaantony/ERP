"""El trabajador que vacía la cola de integraciones.

Es el «2. Un trabajador toma la tarea» del diagrama de secuencia. Toma los
trabajos vencidos, se los pasa al conector correspondiente y aplica la respuesta
al ERP. Ante una falla recuperable devuelve el trabajo a la cola con la espera
que toca; al tercer intento lo manda a la cola de errores.
"""
import logging

from django.db import connection, transaction
from django.utils import timezone

from apps.integraciones.conectores import ErrorConector, obtener_conector
from apps.integraciones.models import TrabajoIntegracion

logger = logging.getLogger(__name__)

#: Qué hacer con la respuesta de cada operación, una vez que el conector responde.
_POSPROCESO = {}


def al_terminar(operacion):
    """Registra la función que aplica al ERP el resultado de una operación."""

    def decorador(funcion):
        _POSPROCESO[operacion] = funcion
        return funcion

    return decorador


@al_terminar("emitir_comprobante")
def _aplicar_comprobante(trabajo, respuesta):
    from apps.facturacion.models import Comprobante
    from apps.facturacion.servicios import aplicar_respuesta

    comprobante = Comprobante.objects.filter(pk=trabajo.objeto_id).first()
    if comprobante is None:
        logger.warning("Trabajo %s apunta a un comprobante que ya no existe", trabajo.pk)
        return
    from django.db import transaction
    from apps.facturacion.archivos import guardar_archivos
    with transaction.atomic():
        aplicar_respuesta(comprobante, respuesta.datos, respuesta.id_externo)
        guardar_archivos(comprobante, respuesta.datos.get('archivos', {}))


@al_terminar("emitir_guia")
def _aplicar_guia(trabajo, respuesta):
    from apps.inventario.models import GuiaRemision

    guia = GuiaRemision.objects.filter(pk=trabajo.objeto_id).first()
    if guia is None:
        logger.warning("Trabajo %s apunta a una guía que ya no existe", trabajo.pk)
        return
    datos = respuesta.datos or {}
    estado = str(datos.get("estado", "")).lower()
    guia.estado = {
        "aceptado": GuiaRemision.Estado.ACEPTADO,
        "rechazado": GuiaRemision.Estado.RECHAZADO,
    }.get(estado, GuiaRemision.Estado.ENVIADO)
    guia.id_externo = respuesta.id_externo or guia.id_externo
    guia.mensaje_respuesta = datos.get("mensaje", "")
    guia.save(update_fields=["estado", "id_externo", "mensaje_respuesta", "actualizado_en"])


@al_terminar("enviar_conversion")
def _marcar_conversion(trabajo, respuesta):
    from apps.crm.models import Lead

    Lead.objects.filter(pk=trabajo.objeto_id).update(conversion_enviada=True)


def trabajos_pendientes(empresa=None, limite=50):
    """Los que ya toca ejecutar: en cola o con el reintento cumplido."""
    consulta = TrabajoIntegracion.objects.filter(
        estado__in=(TrabajoIntegracion.Estado.EN_COLA, TrabajoIntegracion.Estado.REINTENTAR),
        ejecutar_despues_de__lte=timezone.now(),
    ).select_related("servicio")
    if empresa is not None:
        consulta = consulta.filter(empresa=empresa)
    return consulta.order_by("ejecutar_despues_de")[:limite]


def procesar_trabajo(trabajo):
    """Ejecuta un trabajo y devuelve su estado final.

    Nunca levanta: una falla de un trabajo no puede tumbar el resto de la cola.
    """
    with transaction.atomic():
        # Lo marcamos en proceso con bloqueo para que dos trabajadores no lo tomen.
        # En Postgres el `skip_locked` deja que otro trabajador siga de largo; en
        # SQLite no hay bloqueo de fila y la comprobación de estado hace de red.
        consulta = TrabajoIntegracion.objects.filter(
            pk=trabajo.pk,
            estado__in=(
                TrabajoIntegracion.Estado.EN_COLA,
                TrabajoIntegracion.Estado.REINTENTAR,
            ),
        )
        if connection.features.has_select_for_update_skip_locked:
            consulta = consulta.select_for_update(skip_locked=True)
        bloqueado = consulta.first()
        if bloqueado is None:
            return None
        bloqueado.estado = TrabajoIntegracion.Estado.EN_PROCESO
        bloqueado.save(update_fields=["estado", "actualizado_en"])

    try:
        conector = obtener_conector(bloqueado.servicio)
        respuesta = conector.ejecutar(
            bloqueado.operacion,
            bloqueado.carga,
            objeto_id=bloqueado.objeto_id,
            entidad=bloqueado.entidad,
        )
    except ErrorConector as exc:
        if not exc.recuperable:
            bloqueado.estado = TrabajoIntegracion.Estado.FALLIDO
            bloqueado.ultimo_error = str(exc)
            bloqueado.intentos += 1
            bloqueado.save(
                update_fields=["estado", "ultimo_error", "intentos", "actualizado_en"]
            )
            logger.error("Trabajo %s fallido sin reintento: %s", bloqueado.pk, exc)
            return bloqueado.estado
        estado = bloqueado.programar_reintento(str(exc))
        logger.warning("Trabajo %s reintentará (%s): %s", bloqueado.pk, estado, exc)
        return estado
    except Exception as exc:  # noqa: BLE001 - una falla inesperada tampoco tumba la cola
        estado = bloqueado.programar_reintento(f"Error inesperado: {exc}")
        logger.exception("Trabajo %s con error inesperado", bloqueado.pk)
        return estado

    if not respuesta.exito:
        estado = bloqueado.programar_reintento(respuesta.mensaje)
        return estado

    posproceso = _POSPROCESO.get(bloqueado.operacion)
    if posproceso is not None:
        try:
            posproceso(bloqueado, respuesta)
        except Exception as exc:  # noqa: BLE001
            # El envío salió bien; lo que falló es aplicarlo al ERP. Se reintenta
            # eso, no el envío: la llave de idempotencia protege el otro lado.
            estado = bloqueado.programar_reintento(f"Error al aplicar la respuesta: {exc}")
            logger.exception("Trabajo %s: falló el posproceso", bloqueado.pk)
            return estado

    bloqueado.estado = TrabajoIntegracion.Estado.HECHO
    bloqueado.intentos += 1
    bloqueado.ultimo_error = ""
    bloqueado.save(update_fields=["estado", "intentos", "ultimo_error", "actualizado_en"])
    return bloqueado.estado


def procesar_cola(empresa=None, limite=50):
    """Vacía lo que esté vencido. Devuelve el conteo por estado final."""
    resumen = {}
    for trabajo in list(trabajos_pendientes(empresa, limite)):
        estado = procesar_trabajo(trabajo)
        if estado is None:
            continue
        resumen[estado] = resumen.get(estado, 0) + 1
    return resumen
