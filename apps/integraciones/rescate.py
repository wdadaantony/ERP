"""Tareas de rescate: lo que se corre cada tanto para no depender de los avisos.

«El pago se aprueba pero no llega el aviso» es el fallo más común de una pasarela.
La solución del diseño no es confiar en el webhook, sino preguntar: cada 15
minutos se consulta por los comprobantes que siguen con saldo.

Lo mismo con el OSE: un comprobante que lleva mucho «enviado» sin CDR se vuelve a
consultar en vez de quedarse colgado para siempre.
"""
import logging
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError

from django.utils import timezone

from apps.integraciones.conectores import ErrorConector, obtener_conector
from apps.integraciones.models import ServicioExterno

logger = logging.getLogger(__name__)

ESTADOS_PAGADO = ("pagado", "aprobado", "exitoso")


def rescatar_cobros(empresa=None, antiguedad_horas=72, limite=100):
    """Pregunta a la pasarela por los comprobantes que siguen debiendo.

    Devuelve `(consultados, cobros_registrados)`. Solo mira comprobantes
    recientes: perseguir una factura de hace medio año es trabajo del cobrador,
    no de esta tarea.
    """
    from apps.facturacion.models import Comprobante, EstadoComprobante, TipoComprobante
    from apps.tesoreria.models import MetodoPago
    from apps.tesoreria.servicios import registrar_cobro

    desde = timezone.now().date() - timedelta(hours=antiguedad_horas)
    consulta = Comprobante.objects.filter(
        estado__in=(EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO),
        tipo__in=(TipoComprobante.FACTURA, TipoComprobante.BOLETA),
        fecha_emision__gte=desde,
    ).select_related("empresa", "tercero")
    if empresa is not None:
        consulta = consulta.filter(empresa=empresa)

    consultados = registrados = 0
    conectores = {}

    for comprobante in consulta[:limite]:
        if comprobante.esta_pagado:
            continue
        conector = conectores.get(comprobante.empresa_id)
        if conector is None:
            servicio = ServicioExterno.objects.filter(
                empresa=comprobante.empresa,
                codigo=ServicioExterno.Codigo.PASARELA,
                activo=True,
            ).first()
            if servicio is None:
                continue
            conector = conectores[comprobante.empresa_id] = obtener_conector(servicio)

        try:
            respuesta = conector.ejecutar(
                "consultar_pago",
                {"referencia": comprobante.numero_completo, "monto": str(comprobante.saldo)},
                objeto_id=str(comprobante.pk),
                entidad="facturacion.Comprobante",
            )
        except ErrorConector as exc:
            logger.warning("No se pudo consultar %s: %s", comprobante.numero_completo, exc)
            continue
        consultados += 1

        datos = respuesta.datos or {}
        if str(datos.get("estado", "")).lower() not in ESTADOS_PAGADO:
            continue

        if datos.get("monto") is None:
            logger.warning("Pago sin importe exacto para %s; requiere revisión", comprobante.pk)
            continue
        monto = datos["monto"]
        try:
            registrar_cobro(
                comprobante,
                monto,
                metodo=MetodoPago.PASARELA,
                referencia_externa=datos.get("referencia") or respuesta.id_externo,
                comision=datos.get("comision", 0),
                tipo_cambio=datos.get("tipo_cambio"),
            )
        except ValidationError as exc:
            logger.warning("Pago no aplicado a %s: %s", comprobante.pk, exc)
            continue
        registrados += 1
        logger.info("Cobro rescatado para %s por %s", comprobante.numero_completo, monto)

    return consultados, registrados


def rescatar_comprobantes(empresa=None, minutos_sin_respuesta=30, limite=100):
    """Reconsulta al OSE los comprobantes enviados que nunca recibieron su CDR.

    Devuelve `(consultados, resueltos)`.
    """
    from apps.facturacion.models import Comprobante, EstadoComprobante
    from apps.facturacion.servicios import aplicar_respuesta

    limite_tiempo = timezone.now() - timedelta(minutes=minutos_sin_respuesta)
    consulta = Comprobante.objects.filter(
        estado=EstadoComprobante.ENVIADO, actualizado_en__lte=limite_tiempo
    ).select_related("empresa")
    if empresa is not None:
        consulta = consulta.filter(empresa=empresa)

    consultados = resueltos = 0
    conectores = {}

    for comprobante in consulta[:limite]:
        conector = conectores.get(comprobante.empresa_id)
        if conector is None:
            servicio = ServicioExterno.objects.filter(
                empresa=comprobante.empresa, codigo=ServicioExterno.Codigo.OSE, activo=True
            ).first()
            if servicio is None:
                continue
            conector = conectores[comprobante.empresa_id] = obtener_conector(servicio)

        try:
            respuesta = conector.ejecutar(
                "consultar_estado",
                {
                    "numero_completo": comprobante.numero_completo,
                    "id_externo": comprobante.id_externo,
                },
                objeto_id=str(comprobante.pk),
                entidad="facturacion.Comprobante",
            )
        except ErrorConector as exc:
            logger.warning("No se pudo consultar %s: %s", comprobante.numero_completo, exc)
            continue
        consultados += 1

        estado = str((respuesta.datos or {}).get("estado", "")).lower()
        if estado in ("aceptado", "observado", "rechazado"):
            aplicar_respuesta(comprobante, respuesta.datos, respuesta.id_externo)
            resueltos += 1

    return consultados, resueltos
