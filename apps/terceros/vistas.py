"""Clientes y proveedores en una sola ficha."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from apps.core.transacciones import vista_serializada
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.alcance import acotar
from apps.terceros.formularios import FormsetContactos, FormsetDirecciones, FormularioTercero
from apps.terceros.models import Tercero


def _mis_terceros(request):
    return acotar(Tercero.objects.all(), request, "terceros", "vendedor_asignado")


@login_required
@vista_serializada
def lista(request):
    terceros = _mis_terceros(request).select_related("vendedor_asignado", "condicion_pago")
    tipo = request.GET.get("tipo", "")
    q = request.GET.get("q", "").strip()
    if tipo == "clientes":
        terceros = terceros.filter(es_cliente=True)
    elif tipo == "proveedores":
        terceros = terceros.filter(es_proveedor=True)
    if q:
        terceros = terceros.filter(
            Q(razon_social__icontains=q) | Q(numero_documento__icontains=q)
            | Q(nombre_comercial__icontains=q) | Q(email__icontains=q)
        )
    pagina = Paginator(terceros, 40).get_page(request.GET.get("pagina"))
    return render(request, "terceros/lista.html", {
        "page_obj": pagina, "tipo": tipo, "q": q, "consulta": f"tipo={tipo}&q={q}",
    })


@login_required
@vista_serializada
def detalle(request, pk):
    tercero = get_object_or_404(_mis_terceros(request), pk=pk)
    qs = tercero.comprobantes.filter(tipo__in=("01", "03")).select_related("empresa").prefetch_related("notas")
    if not request.user.has_perm("facturacion.view_comprobante"):
        qs = qs.none()
    elif not request.user.has_perm("ventas.ver_todo"):
        qs = qs.filter(pedido__vendedor=request.user)
    comprobantes = qs.order_by("-fecha_emision")[:20]
    saldo = sum((c.saldo_base for c in qs if c.estado in ("aceptado", "observado")), Decimal("0"))
    return render(request, "terceros/detalle.html", {
        "tercero": tercero,
        "pedidos": acotar(tercero.pedidos.all(), request, "ventas", "vendedor").select_related("vendedor")[:20]
            if request.user.has_perm("ventas.view_pedido") else [],
        "comprobantes": comprobantes,
        "ordenes": tercero.ordenes_compra.all()[:20] if request.user.has_perm("compras.view_ordencompra") else [],
        "saldo": saldo,
    })


@login_required
@vista_serializada
def editar(request, pk=None):
    empresa = request.empresa
    tercero = get_object_or_404(_mis_terceros(request), pk=pk) if pk else None
    if request.method == "POST":
        form = FormularioTercero(request.POST, instance=tercero, empresa=empresa)
        contactos = FormsetContactos(request.POST, instance=tercero, prefix="contactos")
        direcciones = FormsetDirecciones(request.POST, instance=tercero, prefix="direcciones")
        if form.is_valid() and contactos.is_valid() and direcciones.is_valid():
            with transaction.atomic():
                nuevo = form.save(commit=False)
                nuevo.empresa = empresa
                if tercero is None and not nuevo.vendedor_asignado:
                    nuevo.vendedor_asignado = request.user
                nuevo.save()
                for fs in (contactos, direcciones):
                    fs.instance = nuevo
                    for obj in fs.save(commit=False):
                        obj.empresa = empresa
                        obj.save()
                    for obj in fs.deleted_objects:
                        obj.delete()
            messages.success(request, f"{nuevo.razon_social} guardado.")
            return redirect("terceros:detalle", pk=nuevo.pk)
    else:
        form = FormularioTercero(instance=tercero, empresa=empresa)
        contactos = FormsetContactos(instance=tercero, prefix="contactos")
        direcciones = FormsetDirecciones(instance=tercero, prefix="direcciones")
    return render(request, "terceros/editar.html", {
        "form": form, "contactos": contactos, "direcciones": direcciones, "tercero": tercero,
    })
