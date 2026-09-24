"""Pantallas de venta: listado, ficha del pedido, edición y las acciones del flujo."""
import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from apps.core.transacciones import vista_serializada
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.catalogo.models import Producto
from apps.core.alcance import acotar
from apps.facturacion.servicios import emitir_comprobante
from apps.inventario.models import stock_disponible
from apps.inventario.servicios import StockInsuficiente, despachar_pedido, emitir_guia
from apps.ventas.formularios import (
    FormsetLineas,
    FormularioCancelar,
    FormularioGuia,
    FormularioOrdenCompra,
    FormularioPedido,
)
from apps.ventas.models import EstadoPedido, Pedido
from apps.ventas.servicios import (
    LimiteExcedido,
    cancelar_pedido,
    confirmar_pedido,
    generar_orden_compra,
    reintentar_reserva,
    siguiente_numero_pedido,
)

#: Orden del camino feliz, para la línea de tiempo de la ficha.
CAMINO = [
    EstadoPedido.BORRADOR,
    EstadoPedido.CONFIRMADO,
    EstadoPedido.RESERVADO,
    EstadoPedido.ENTREGADO,
    EstadoPedido.FACTURADO,
    EstadoPedido.PAGADO,
    EstadoPedido.CERRADO,
]
EDITABLES = (EstadoPedido.BORRADOR, EstadoPedido.ENVIADA, EstadoPedido.VENCIDA)


def _mis_pedidos(request):
    return acotar(Pedido.objects.all(), request, "ventas", "vendedor").select_related(
        "tercero", "vendedor"
    )


@login_required
@vista_serializada
def lista(request):
    pedidos = _mis_pedidos(request)
    estado = request.GET.get("estado", "")
    busqueda = request.GET.get("q", "").strip()
    if estado == "abiertos":
        pedidos = pedidos.exclude(
            estado__in=(EstadoPedido.CERRADO, EstadoPedido.CANCELADO, EstadoPedido.VENCIDA)
        )
    elif estado:
        pedidos = pedidos.filter(estado=estado)
    if busqueda:
        pedidos = pedidos.filter(
            Q(numero__icontains=busqueda)
            | Q(tercero__razon_social__icontains=busqueda)
            | Q(tercero__numero_documento__icontains=busqueda)
        )

    pagina = Paginator(pedidos, 30).get_page(request.GET.get("pagina"))
    return render(
        request,
        "ventas/lista.html",
        {
            "page_obj": pagina,
            "estado": estado,
            "q": busqueda,
            "consulta": f"estado={estado}&q={busqueda}",
            "estados": EstadoPedido.choices,
        },
    )


@login_required
@vista_serializada
def detalle(request, pk):
    pedido = get_object_or_404(_mis_pedidos(request), pk=pk)
    lineas = list(pedido.lineas.select_related("producto", "producto__unidad_medida"))
    for linea in lineas:
        linea.disponible = (
            stock_disponible(linea.producto, empresa=pedido.empresa)
            if linea.producto.controla_stock
            else None
        )

    if pedido.estado in CAMINO:
        indice = CAMINO.index(pedido.estado)
    elif pedido.estado == EstadoPedido.ENVIADA:
        indice = 0
    elif pedido.estado == EstadoPedido.EN_ESPERA:
        indice = 1
    else:
        indice = -1
    pasos = [
        {
            "estado": e,
            "nombre": EstadoPedido(e).label,
            "hecho": i < indice,
            "actual": i == indice,
        }
        for i, e in enumerate(CAMINO)
    ]

    return render(
        request,
        "ventas/detalle.html",
        {
            "pedido": pedido,
            "lineas": lineas,
            "pasos": pasos,
            "desvio": pedido.estado in (EstadoPedido.EN_ESPERA, EstadoPedido.CANCELADO, EstadoPedido.VENCIDA),
            "editable": pedido.estado in EDITABLES,
            "comprobante": pedido.comprobantes.filter(tipo__in=("01", "03")).first() if request.user.has_perm("facturacion.view_comprobante") else None,
            "guia": pedido.guias.first() if request.user.has_perm("inventario.view_guiaremision") else None,
            "ordenes": pedido.ordenes_compra.all() if request.user.has_perm("compras.view_ordencompra") else [],
            "form_cancelar": FormularioCancelar(),
            "form_oc": FormularioOrdenCompra(empresa=pedido.empresa),
            "form_guia": FormularioGuia(
                initial={
                    "punto_llegada": (
                        pedido.direccion_entrega.direccion
                        if pedido.direccion_entrega
                        else pedido.tercero.direccion_fiscal
                    ),
                    "fecha_traslado": date.today(),
                }
            ),
            "E": EstadoPedido,
        },
    )


@login_required
@vista_serializada
def editar(request, pk=None):
    """Alta y edición en la misma pantalla. Las líneas se editan en tabla."""
    empresa = request.empresa
    pedido = None
    if pk is not None:
        pedido = get_object_or_404(_mis_pedidos(request), pk=pk)
        if pedido.estado not in EDITABLES:
            messages.warning(request, "Un pedido confirmado ya no se edita; cancélalo y crea otro.")
            return redirect("ventas:detalle", pk=pedido.pk)

    if request.method == "POST":
        form = FormularioPedido(request.POST, instance=pedido, empresa=empresa)
        formset = FormsetLineas(request.POST, instance=pedido, empresa=empresa)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                nuevo = form.save(commit=False)
                nuevo.empresa = empresa
                if pedido is None:
                    nuevo.numero = siguiente_numero_pedido(empresa)
                    nuevo.vendedor = request.user
                nuevo.save()
                formset.instance = nuevo
                for linea in formset.save(commit=False):
                    linea.empresa = empresa
                    if not linea.descripcion:
                        linea.descripcion = linea.producto.nombre
                    linea.save()
                for linea in formset.deleted_objects:
                    linea.delete()
                nuevo.recalcular_totales()
            messages.success(
                request, f"{'Cotización creada' if pedido is None else 'Pedido actualizado'}: {nuevo.numero}."
            )
            return redirect("ventas:detalle", pk=nuevo.pk)
    else:
        form = FormularioPedido(
            instance=pedido, empresa=empresa, initial={"fecha": date.today()} if pedido is None else None
        )
        formset = FormsetLineas(instance=pedido, empresa=empresa)

    precios = {
        str(p.pk): {
            "precio": str(p.precio_lista),
            "igv": p.afectacion_igv == "10",
            "unidad": p.unidad_medida.codigo,
        }
        for p in Producto.objects.filter(empresa=empresa, activo=True).select_related("unidad_medida")
    }
    return render(
        request,
        "ventas/editar.html",
        {"form": form, "formset": formset, "pedido": pedido, "precios_json": json.dumps(precios)},
    )


# -- Acciones del flujo -----------------------------------------------------


def _accion(request, pk, funcion, exito):
    pedido = get_object_or_404(_mis_pedidos(request), pk=pk)
    try:
        resultado = funcion(pedido)
    except LimiteExcedido as exc:
        messages.warning(request, " ".join(exc.messages))
    except StockInsuficiente as exc:
        messages.error(request, " ".join(exc.messages))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        texto = exito(pedido, resultado) if callable(exito) else exito
        if texto:
            messages.success(request, texto)
    return redirect("ventas:detalle", pk=pedido.pk)


@login_required
@vista_serializada
@require_POST
def enviar(request, pk):
    return _accion(
        request, pk, lambda p: p.transicionar(EstadoPedido.ENVIADA, request.user), "Cotización marcada como enviada."
    )


@login_required
@vista_serializada
@require_POST
def confirmar(request, pk):
    def exito(pedido, _):
        if pedido.estado == EstadoPedido.EN_ESPERA:
            faltan = ", ".join(f"{f['producto'].nombre}" for f in pedido.faltantes)
            messages.warning(request, f"Falta stock de: {faltan}. El pedido quedó en espera.")
            return ""
        return "Pedido confirmado y stock reservado."

    return _accion(request, pk, lambda p: confirmar_pedido(p, request.user), exito)


@login_required
@vista_serializada
@require_POST
def reintentar(request, pk):
    def exito(pedido, _):
        if pedido.estado == EstadoPedido.EN_ESPERA:
            return None
        return "Stock reservado. El pedido ya puede despacharse."

    def funcion(p):
        reintentar_reserva(p, request.user)
        if p.estado == EstadoPedido.EN_ESPERA:
            messages.warning(request, "Todavía no hay stock suficiente.")
        return p

    return _accion(request, pk, funcion, exito)


@login_required
@vista_serializada
@require_POST
def despachar(request, pk):
    def funcion(p):
        if p.estado != EstadoPedido.RESERVADO:
            raise ValidationError("Solo se puede despachar un pedido reservado.")
        despachar_pedido(p, request.user)

    return _accion(request, pk, funcion, "Mercadería despachada. Ya se puede emitir la guía y facturar.")


@login_required
@vista_serializada
@require_POST
def facturar(request, pk):
    def funcion(p):
        comprobante, _ = emitir_comprobante(p, request.user)
        return comprobante

    return _accion(
        request,
        pk,
        funcion,
        lambda p, c: f"Comprobante {c.numero_completo} emitido y en cola hacia SUNAT.",
    )


@login_required
@vista_serializada
@require_POST
def cancelar(request, pk):
    form = FormularioCancelar(request.POST)
    motivo = form.cleaned_data["motivo"] if form.is_valid() else ""
    return _accion(
        request, pk, lambda p: cancelar_pedido(p, request.user, motivo), "Pedido cancelado y stock liberado."
    )


@login_required
@vista_serializada
@require_POST
def comprar_faltante(request, pk):
    pedido = get_object_or_404(_mis_pedidos(request), pk=pk)
    form = FormularioOrdenCompra(request.POST, empresa=pedido.empresa)
    if not form.is_valid():
        messages.error(request, "Elige un proveedor.")
        return redirect("ventas:detalle", pk=pk)
    try:
        orden = generar_orden_compra(pedido, form.cleaned_data["proveedor"], request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("ventas:detalle", pk=pk)
    messages.success(request, f"Orden de compra {orden.numero} creada con el faltante.")
    return redirect("compras:detalle", pk=orden.pk)


@login_required
@vista_serializada
@require_POST
def guia(request, pk):
    pedido = get_object_or_404(_mis_pedidos(request), pk=pk)
    form = FormularioGuia(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisa los datos de la guía.")
        return redirect("ventas:detalle", pk=pk)
    try:
        guia_, _ = emitir_guia(pedido, usuario=request.user, **form.cleaned_data)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("ventas:detalle", pk=pk)
    messages.success(request, f"Guía {guia_.numero_completo} emitida y en cola hacia SUNAT.")
    return redirect("ventas:detalle", pk=pk)


@login_required
@vista_serializada
def buscar_productos(request):
    """Autocompletado para la tabla de líneas."""
    q = request.GET.get("q", "").strip()
    productos = Producto.objects.filter(empresa=request.empresa, activo=True)
    if q:
        productos = productos.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q))
    return JsonResponse(
        [
            {"id": p.pk, "texto": f"{p.codigo} — {p.nombre}", "precio": str(p.precio_lista)}
            for p in productos[:15]
        ],
        safe=False,
    )
