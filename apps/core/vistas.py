"""Inicio, cambio de empresa y utilidades de sesión."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.alcance import acotar, ve_todo
from apps.core.models import Empresa
from apps.core.monedas import a_moneda_base


@login_required
def inicio(request):
    """Tablero: lo que hay que atender hoy, y cómo va el mes."""
    from apps.crm.models import EstadoLead, Lead
    from apps.facturacion.models import Comprobante, EstadoComprobante, TipoComprobante
    from apps.integraciones.models import TrabajoIntegracion
    from apps.ventas.models import EstadoPedido, Pedido

    empresa = request.empresa
    if empresa is None:
        return redirect("core:sin_empresa")
    hoy = timezone.localdate()
    inicio_mes = hoy.replace(day=1)
    inicio_mes_anterior = (inicio_mes - timedelta(days=1)).replace(day=1)

    pedidos = acotar(Pedido.objects.all(), request, "ventas", "vendedor")
    comprobantes = Comprobante.objects.filter(
        empresa=empresa,
        tipo__in=(TipoComprobante.FACTURA, TipoComprobante.BOLETA),
        estado__in=(EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO),
    )
    if not ve_todo(request.user, "ventas"):
        comprobantes = comprobantes.filter(pedido__vendedor=request.user)

    def facturado(desde, hasta):
        return sum((a_moneda_base(c.total, c) for c in comprobantes.filter(
            fecha_emision__gte=desde, fecha_emision__lt=hasta
        ).select_related("empresa")), Decimal("0"))

    ventas_mes = facturado(inicio_mes, hoy + timedelta(days=1))
    fin_comparacion = min(inicio_mes, inicio_mes_anterior + timedelta(days=hoy.day))
    ventas_mes_anterior = facturado(inicio_mes_anterior, fin_comparacion)
    variacion = None
    if ventas_mes_anterior:
        variacion = (ventas_mes - ventas_mes_anterior) / ventas_mes_anterior * 100

    por_cobrar = [c for c in comprobantes.select_related("empresa").prefetch_related("notas") if not c.esta_pagado]
    saldo_por_cobrar = sum((c.saldo_base for c in por_cobrar), Decimal("0"))
    vencidos = [c for c in por_cobrar if c.fecha_vencimiento and c.fecha_vencimiento < hoy]

    contexto = {
        "ventas_mes": ventas_mes,
        "variacion": variacion,
        "saldo_por_cobrar": saldo_por_cobrar,
        "cantidad_por_cobrar": len(por_cobrar),
        "vencidos": sorted(vencidos, key=lambda c: c.fecha_vencimiento)[:6],
        "saldo_vencido": sum((c.saldo_base for c in vencidos), Decimal("0")),
        "cantidad_vencidos": len(vencidos),
        "pedidos_abiertos": pedidos.exclude(
            estado__in=(EstadoPedido.CERRADO, EstadoPedido.CANCELADO, EstadoPedido.VENCIDA)
        ).count(),
        "pedidos_por_estado": list(
            pedidos.values("estado").annotate(n=Count("id")).order_by("estado")
        ),
        "en_espera": pedidos.filter(estado=EstadoPedido.EN_ESPERA).select_related("tercero")[:6],
        "por_despachar": pedidos.filter(estado=EstadoPedido.RESERVADO).select_related("tercero")[:6],
        "leads_nuevos": acotar(Lead.objects.all(), request, "crm", "vendedor").filter(
            estado=EstadoLead.NUEVO
        )[:6],
        "rechazados": Comprobante.objects.filter(
            empresa=empresa, estado=EstadoComprobante.RECHAZADO
        ).select_related("tercero")[:6],
        "fallidos": TrabajoIntegracion.objects.filter(
            empresa=empresa, estado=TrabajoIntegracion.Estado.FALLIDO
        ).select_related("servicio")[:6],
        "ultimos_pedidos": pedidos.select_related("tercero")[:8],
        "estados": dict(EstadoPedido.choices),
    }
    # El inicio no debe revelar módulos que el rol no puede consultar.
    if not request.user.has_perm("facturacion.view_comprobante"):
        for clave in ("ventas_mes", "variacion", "saldo_por_cobrar", "saldo_vencido"):
            contexto[clave] = None
        contexto.update(cantidad_por_cobrar=0, cantidad_vencidos=0, vencidos=[], rechazados=[])
    if not request.user.has_perm("ventas.view_pedido"):
        contexto.update(pedidos_abiertos=0, pedidos_por_estado=[], en_espera=[],
                        por_despachar=[], ultimos_pedidos=[])
    if not request.user.has_perm("crm.view_lead"):
        contexto["leads_nuevos"] = []
    if not request.user.has_perm("integraciones.view_registrointegracion"):
        contexto["fallidos"] = []
    if not ve_todo(request.user, "ventas"):
        contexto["rechazados"] = comprobantes.none()
    from apps.core.dashboard import indicadores
    contexto.update(indicadores(request, comprobantes, pedidos, hoy))
    return render(request, "core/inicio.html", contexto)


@login_required
def cambiar_empresa(request):
    if request.user.is_superuser:
        empresas = Empresa.objects.filter(activa=True)
    else:
        empresas = request.user.empresas.filter(activa=True)

    if request.method == "POST":
        empresa = get_object_or_404(empresas, pk=request.POST.get("empresa"))
        request.user.empresa_actual = empresa
        request.user.save(update_fields=["empresa_actual"])
        messages.success(request, f"Ahora estás trabajando en {empresa}.")
        return redirect("core:inicio")
    return render(request, "core/cambiar_empresa.html", {"empresas": empresas})


@login_required
def sin_empresa(request):
    if request.empresa is not None:
        return redirect("core:inicio")
    return render(request, "core/sin_empresa.html")
