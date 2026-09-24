from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.ventas.models import LineaPedido, Pedido


class LineaPedidoInline(DocumentoProtegidoMixin, admin.TabularInline):
    model = LineaPedido
    extra = 0
    fields = (
        "producto",
        "cantidad",
        "precio_unitario",
        "descuento_pct",
        "tasa_impuesto",
        "cantidad_entregada",
        "cantidad_facturada",
    )
    autocomplete_fields = ("producto",)


@admin.register(Pedido)
class PedidoAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("numero", "tercero", "fecha", "estado", "moneda", "total", "vendedor")
    list_filter = ("estado", "empresa", "moneda", "origen")
    search_fields = ("numero", "tercero__razon_social", "tercero__numero_documento")
    date_hierarchy = "fecha"
    inlines = (LineaPedidoInline,)
    autocomplete_fields = ("tercero",)
    readonly_fields = ("subtotal", "impuestos", "total", "creado_en", "actualizado_en")
    fieldsets = (
        (None, {"fields": ("empresa", "numero", "tercero", "vendedor", "almacen", "estado")}),
        ("Fechas", {"fields": ("fecha", "valido_hasta")}),
        ("Importes", {"fields": ("moneda", "tipo_cambio", "subtotal", "impuestos", "total")}),
        ("Origen comercial", {"fields": ("origen", "campania", "utm")}),
        ("Otros", {"fields": ("direccion_entrega", "notas", "ids_externos")}),
    )

    @admin.action(description="Recalcular totales")
    def recalcular(self, request, queryset):
        for pedido in queryset:
            pedido.recalcular_totales()
        self.message_user(request, f"{queryset.count()} pedido(s) recalculado(s).")

    actions = ("recalcular",)
