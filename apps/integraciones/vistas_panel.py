"""Panel de integraciones: cola, errores, webhooks y servicios."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.integraciones.models import (
    EventoWebhook, RegistroIntegracion, ServicioExterno, TrabajoIntegracion,
)


@login_required
def panel(request):
    empresa = request.empresa
    servicios = ServicioExterno.objects.filter(empresa=empresa)
    for s in servicios:
        from apps.integraciones.configuracion import diagnostico
        s.diagnostico = diagnostico(s)
        s.ultimo = s.registros.order_by("-creado_en").first()
        s.errores = s.registros.filter(estado=RegistroIntegracion.Estado.ERROR).count()
    trabajos = TrabajoIntegracion.objects.filter(empresa=empresa).select_related("servicio")
    return render(request, "integraciones/panel.html", {
        "servicios": servicios,
        "fallidos": trabajos.filter(estado=TrabajoIntegracion.Estado.FALLIDO)[:30],
        "pendientes": trabajos.filter(
            estado__in=(TrabajoIntegracion.Estado.EN_COLA, TrabajoIntegracion.Estado.REINTENTAR,
                        TrabajoIntegracion.Estado.EN_PROCESO)
        )[:30],
        "webhooks_pendientes": EventoWebhook.objects.filter(
            empresa=empresa, procesado=False
        ).select_related("servicio")[:30],
        "registros": RegistroIntegracion.objects.filter(empresa=empresa).select_related("servicio")[:40],
        "E": TrabajoIntegracion.Estado,
    })


@login_required
@require_POST
def reencolar(request, pk):
    t = get_object_or_404(TrabajoIntegracion, pk=pk, empresa=request.empresa)
    t.estado = TrabajoIntegracion.Estado.EN_COLA
    t.intentos = 0
    t.ejecutar_despues_de = timezone.now()
    t.save(update_fields=["estado", "intentos", "ejecutar_despues_de", "actualizado_en"])
    messages.success(request, f"Trabajo «{t.operacion}» reencolado.")
    return redirect("integraciones:panel")


@login_required
@require_POST
def procesar_ahora(request):
    from apps.integraciones.trabajador import procesar_cola
    from apps.integraciones.webhooks import reprocesar_pendientes

    resumen = procesar_cola(request.empresa)
    hechos, fallidos = reprocesar_pendientes(request.empresa)
    partes = [f"{v} {k}" for k, v in resumen.items()]
    if hechos or fallidos:
        partes.append(f"webhooks: {hechos} aplicados, {fallidos} con error")
    messages.success(request, "Cola procesada. " + (", ".join(partes) if partes else "No había nada pendiente."))
    return redirect("integraciones:panel")


@login_required
def registros(request):
    qs = RegistroIntegracion.objects.filter(empresa=request.empresa).select_related("servicio")
    estado = request.GET.get("estado", "")
    if estado:
        qs = qs.filter(estado=estado)
    pagina = Paginator(qs, 50).get_page(request.GET.get("pagina"))
    return render(request, "integraciones/registros.html", {
        "page_obj": pagina, "estado": estado, "consulta": f"estado={estado}",
    })
