"""Órdenes de compra y recepciones."""
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from apps.core.transacciones import vista_serializada
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.catalogo.models import Producto
from apps.compras.models import (
    EstadoOrdenCompra, LineaOrdenCompra, LineaRecepcion, OrdenCompra, Recepcion,
)
from apps.core.models import Serie
from apps.core.monedas import a_moneda_base, factor_cambio
from apps.inventario.models import Almacen
from apps.inventario.servicios import ingresar_recepcion
from apps.terceros.models import Tercero
from apps.ventas.models import EstadoPedido
from apps.ventas.servicios import reintentar_reserva


class FormularioOrden(forms.ModelForm):
    class Meta:
        model = OrdenCompra
        fields = ("proveedor", "almacen_destino", "fecha", "fecha_entrega_esperada", "moneda", "tipo_cambio", "notas")
        widgets = {
            "fecha": forms.DateInput(format='%Y-%m-%d', attrs={"type": "date"}),
            "fecha_entrega_esperada": forms.DateInput(format='%Y-%m-%d', attrs={"type": "date"}),
            "notas": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields["proveedor"].queryset = Tercero.objects.filter(
            empresa=empresa, es_proveedor=True, activo=True
        )
        self.fields["almacen_destino"].queryset = Almacen.objects.filter(empresa=empresa, activo=True)

    def clean(self):
        datos = super().clean()
        if datos.get("moneda") and datos.get("tipo_cambio") is not None:
            datos["tipo_cambio"] = factor_cambio(datos["moneda"], self.empresa, datos["tipo_cambio"])
        return datos


class FormularioLineaOC(forms.ModelForm):
    class Meta:
        model = LineaOrdenCompra
        fields = ("producto", "cantidad", "precio_unitario")

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["producto"].queryset = Producto.objects.filter(
                empresa=empresa, activo=True, tipo="bien"
            )


class BaseFormsetOC(forms.BaseInlineFormSet):
    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        k = super().get_form_kwargs(index)
        k["empresa"] = self.empresa
        return k


FormsetLineasOC = inlineformset_factory(
    OrdenCompra, LineaOrdenCompra, form=FormularioLineaOC, formset=BaseFormsetOC,
    extra=1, can_delete=True,
)


@login_required
@vista_serializada
def lista(request):
    ordenes = OrdenCompra.objects.filter(empresa=request.empresa).select_related("proveedor")
    estado = request.GET.get("estado", "")
    if estado:
        ordenes = ordenes.filter(estado=estado)
    pagina = Paginator(ordenes, 30).get_page(request.GET.get("pagina"))
    return render(request, "compras/lista.html", {
        "page_obj": pagina, "estado": estado, "consulta": f"estado={estado}",
        "estados": EstadoOrdenCompra.choices,
    })


@login_required
@vista_serializada
def detalle(request, pk):
    orden = get_object_or_404(OrdenCompra, pk=pk, empresa=request.empresa)
    return render(request, "compras/detalle.html", {
        "orden": orden,
        "lineas": orden.lineas.select_related("producto", "producto__unidad_medida"),
        "recepciones": orden.recepciones.all(),
        "E": EstadoOrdenCompra,
        "pendiente": any(l.pendiente_recepcion > 0 for l in orden.lineas.all()),
    })


@login_required
@vista_serializada
def editar(request, pk=None):
    empresa = request.empresa
    orden = get_object_or_404(OrdenCompra, pk=pk, empresa=empresa) if pk else None
    if orden and orden.estado not in (EstadoOrdenCompra.BORRADOR, EstadoOrdenCompra.POR_APROBAR):
        messages.warning(request, "Una orden aprobada ya no se edita.")
        return redirect("compras:detalle", pk=pk)
    if request.method == "POST":
        form = FormularioOrden(request.POST, instance=orden, empresa=empresa)
        formset = FormsetLineasOC(request.POST, instance=orden, empresa=empresa)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                nueva = form.save(commit=False)
                nueva.empresa = empresa
                if orden is None:
                    serie, _ = Serie.objects.get_or_create(
                        empresa=empresa, tipo_documento="OC", serie="OC"
                    )
                    nueva.numero = f"OC-{serie.siguiente_numero():06d}"
                    nueva.solicitante = request.user
                nueva.save()
                formset.instance = nueva
                for l in formset.save(commit=False):
                    l.empresa = empresa
                    l.save()
                for l in formset.deleted_objects:
                    l.delete()
                nueva.recalcular_totales()
            messages.success(request, f"Orden {nueva.numero} guardada.")
            return redirect("compras:detalle", pk=nueva.pk)
    else:
        form = FormularioOrden(instance=orden, empresa=empresa, initial={"fecha": date.today()})
        formset = FormsetLineasOC(instance=orden, empresa=empresa)
    return render(request, "compras/editar.html", {"form": form, "formset": formset, "orden": orden})


@login_required
@vista_serializada
@require_POST
def aprobar(request, pk):
    if not request.user.is_superuser and not request.user.has_perm("compras.aprobar_ordencompra"):
        raise PermissionDenied
    orden = get_object_or_404(OrdenCompra, pk=pk, empresa=request.empresa)
    if orden.estado not in (EstadoOrdenCompra.BORRADOR, EstadoOrdenCompra.POR_APROBAR):
        messages.error(request, "Esta orden ya no está pendiente de aprobación.")
        return redirect("compras:detalle", pk=pk)
    from apps.core.models import LimiteAprobacion

    limite = LimiteAprobacion.objects.filter(usuario=request.user, empresa=orden.empresa).first()
    if (
        not request.user.is_superuser and limite is not None
        and limite.monto_maximo_compra and a_moneda_base(orden.total, orden) > limite.monto_maximo_compra
    ):
        orden.estado = EstadoOrdenCompra.POR_APROBAR
        orden.save(update_fields=["estado", "actualizado_en"])
        messages.warning(request, f"La orden supera tu límite de {limite.monto_maximo_compra}. Quedó esperando aprobación de alguien con más atribución.")
        return redirect("compras:detalle", pk=pk)
    orden.estado = EstadoOrdenCompra.APROBADA
    orden.aprobada_por = request.user
    orden.save(update_fields=["estado", "aprobada_por", "actualizado_en"])
    messages.success(request, "Orden aprobada.")
    return redirect("compras:detalle", pk=pk)


@login_required
@vista_serializada
@require_POST
def enviar(request, pk):
    orden = get_object_or_404(OrdenCompra, pk=pk, empresa=request.empresa)
    if orden.estado != EstadoOrdenCompra.APROBADA:
        messages.error(request, "Solo se envía una orden aprobada.")
    else:
        orden.estado = EstadoOrdenCompra.ENVIADA
        orden.save(update_fields=["estado", "actualizado_en"])
        messages.success(request, "Orden marcada como enviada al proveedor.")
    return redirect("compras:detalle", pk=pk)


@login_required
@vista_serializada
def recibir(request, pk):
    """Recepción de mercadería: por defecto todo lo pendiente; se puede ajustar."""
    orden = get_object_or_404(OrdenCompra, pk=pk, empresa=request.empresa)
    lineas = list(orden.lineas.select_related("producto"))
    if orden.estado not in (
        EstadoOrdenCompra.APROBADA, EstadoOrdenCompra.ENVIADA, EstadoOrdenCompra.RECIBIDA_PARCIAL
    ):
        messages.error(request, "Esta orden no está en un estado que admita recepción.")
        return redirect("compras:detalle", pk=pk)

    if request.method == "POST":
        try:
            with transaction.atomic():
                serie, _ = Serie.objects.get_or_create(
                    empresa=orden.empresa, tipo_documento="REC", serie="REC"
                )
                recepcion = Recepcion.objects.create(
                    empresa=orden.empresa,
                    numero=f"REC-{serie.siguiente_numero():06d}",
                    orden=orden,
                    almacen=orden.almacen_destino,
                    fecha=date.today(),
                    guia_proveedor=request.POST.get("guia_proveedor", "")[:60],
                    recibido_por=request.user,
                )
                alguna = False
                for l in lineas:
                    cantidad = Decimal(request.POST.get(f"cantidad_{l.pk}", "0") or "0")
                    if cantidad <= 0:
                        continue
                    if cantidad > l.pendiente_recepcion:
                        raise ValidationError(
                            f"{l.producto}: se reciben {cantidad} pero solo faltan {l.pendiente_recepcion}."
                        )
                    LineaRecepcion.objects.create(
                        empresa=orden.empresa, recepcion=recepcion, linea_orden=l,
                        producto=l.producto, cantidad=cantidad, costo_unitario=l.precio_unitario,
                    )
                    alguna = True
                if not alguna:
                    raise ValidationError("No se indicó ninguna cantidad recibida.")
                ingresar_recepcion(recepcion, request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("compras:recibir", pk=pk)

        messages.success(request, f"Recepción {recepcion.numero} registrada. El stock ya está disponible.")
        # Si la compra nació de un pedido en espera, se intenta destrabarlo.
        pedido = orden.pedido_origen
        if pedido and pedido.estado == EstadoPedido.EN_ESPERA:
            reintentar_reserva(pedido, request.user)
            if pedido.estado == EstadoPedido.RESERVADO:
                messages.success(request, f"El pedido {pedido.numero} ya tiene su stock reservado.")
        return redirect("compras:detalle", pk=pk)

    return render(request, "compras/recibir.html", {"orden": orden, "lineas": lineas})
