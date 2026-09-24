"""Receptor de webhooks.

Regla del diseño: guardar primero, procesar después. La vista valida la firma,
deja el evento en la base y responde de inmediato. Si el procesamiento falla, el
evento queda guardado con su error y se reprocesa aparte — el servicio externo no
tiene por qué reintentar por algo que ya recibimos bien.
"""
import hmac
import json
import logging

from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.integraciones.conectores import obtener_conector
from apps.integraciones.models import EventoWebhook, ServicioExterno
from apps.integraciones.webhooks import procesar_evento

logger = logging.getLogger(__name__)

#: De dónde sale la firma, según el servicio. Se prueban todas.
CABECERAS_DE_FIRMA = (
    "HTTP_X_FIRMA",
    "HTTP_X_SIGNATURE",
    "HTTP_X_HUB_SIGNATURE_256",
    "HTTP_X_CULQI_SIGNATURE",
)

#: Cabeceras que sí conviene guardar. El resto puede traer datos sensibles.
CABECERAS_GUARDADAS = ("CONTENT_TYPE", "HTTP_USER_AGENT", "HTTP_X_EVENTO", "HTTP_X_EVENT_TYPE")


@csrf_exempt
def recibir(request, servicio_id):
    """Punto de entrada de todos los webhooks: `/integraciones/webhook/<servicio_id>/`.

    Cada servicio tiene su propia URL y su propio secreto, de modo que filtrar
    una firma solo compromete a ese conector.
    """
    servicio = ServicioExterno.objects.filter(pk=servicio_id, activo=True).first()
    if servicio is None:
        return JsonResponse({"error": "servicio desconocido"}, status=404)

    if request.method == "GET":
        return _verificar_suscripcion(request, servicio)
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    cuerpo_crudo = request.body
    firma = _firma_de(request)
    conector = obtener_conector(servicio)
    firma_valida = conector.validar_firma(cuerpo_crudo, firma)

    try:
        cuerpo = json.loads(cuerpo_crudo or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "cuerpo no es JSON válido"}, status=400)
    if not isinstance(cuerpo, dict):
        return JsonResponse({"error": "se esperaba un objeto JSON"}, status=400)

    id_evento = str(cuerpo.get("id") or cuerpo.get("id_evento") or "")[:140]
    if id_evento and firma_valida:
        # Solo un evento legítimo bloquea a otro con el mismo id. Si contara
        # cualquiera, bastaría con mandar un evento sin firmar con un id adivinado
        # para que el aviso real de después se descartara como duplicado.
        repetido = EventoWebhook.objects.filter(
            servicio=servicio, id_evento_externo=id_evento, firma_valida=True
        ).first()
        if repetido is not None:
            # Reenvío del mismo aviso: se acusa recibo sin volver a aplicarlo.
            return JsonResponse({"estado": "duplicado", "evento": repetido.pk}, status=200)

    evento = EventoWebhook.objects.create(
        empresa=servicio.empresa,
        servicio=servicio,
        evento=str(cuerpo.get("evento") or cuerpo.get("tipo") or "desconocido")[:80],
        id_evento_externo=id_evento,
        cuerpo=cuerpo,
        cabeceras={
            clave: request.META[clave] for clave in CABECERAS_GUARDADAS if clave in request.META
        },
        firma_valida=firma_valida,
    )

    if not firma_valida:
        # Se guarda igual, para poder investigar quién está tocando la puerta.
        evento.mensaje_error = "Firma inválida: el evento no se procesó."
        evento.save(update_fields=["mensaje_error"])
        logger.warning("Webhook con firma inválida en %s (evento %s)", servicio, evento.pk)
        return JsonResponse({"error": "firma inválida"}, status=401)

    try:
        resultado = procesar_evento(evento)
    except Exception as exc:  # noqa: BLE001 - ya está guardado; se reprocesa después
        evento.mensaje_error = str(exc)
        evento.save(update_fields=["mensaje_error"])
        logger.warning("Evento %s recibido pero no aplicado: %s", evento.pk, exc)
        return JsonResponse({"estado": "recibido", "evento": evento.pk}, status=202)

    return JsonResponse({"estado": "procesado", "evento": evento.pk, "detalle": resultado})


def _verificar_suscripcion(request, servicio):
    """Verificación de alta que exigen Meta y WhatsApp (el «hub challenge»).

    Meta llama por GET con un token que debe coincidir con el secreto del
    servicio; si coincide, hay que devolver el challenge tal cual, en texto
    plano. Sin esto, Meta no deja registrar la URL.
    """
    token = request.GET.get("hub.verify_token", "")
    challenge = request.GET.get("hub.challenge", "")
    from apps.integraciones.configuracion import credenciales, secreto
    from django.core.exceptions import ValidationError
    try:
        esperado = credenciales(servicio).get('verify_token') or secreto(servicio.secreto_webhook)
    except ValidationError:
        return JsonResponse({'error': 'integración pendiente de configuración'}, status=503)
    if not esperado or not hmac.compare_digest(str(token), str(esperado)):
        logger.warning("Verificación de webhook rechazada en %s", servicio)
        return JsonResponse({"error": "token de verificación inválido"}, status=403)
    return HttpResponse(challenge, content_type="text/plain")


def _firma_de(request):
    for cabecera in CABECERAS_DE_FIRMA:
        valor = request.META.get(cabecera)
        if valor:
            # Algunos servicios mandan «sha256=<hex>».
            return valor.split("=", 1)[1] if valor.startswith("sha256=") else valor
    return ""
