from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.compras.models import LineaOrdenCompra, LineaRecepcion, OrdenCompra, Recepcion


class LineaOrdenCompraInline(DocumentoProtegidoMixin, admin.TabularInline):
    model = LineaOrdenCompra
    extra = 0
    autocomplete_fields = ("producto",)


@admin.register(OrdenCompra)
class OrdenCompraAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("numero", "proveedor", "fecha", "estado", "moneda", "total")
    list_filter = ("empresa", "estado", "moneda")
    search_fields = ("numero", "proveedor__razon_social")
    date_hierarchy = "fecha"
    inlines = (LineaOrdenCompraInline,)
    autocomplete_fields = ("proveedor",)
    readonly_fields = ("subtotal", "impuestos", "total")


class LineaRecepcionInline(DocumentoProtegidoMixin, admin.TabularInline):
    model = LineaRecepcion
    extra = 0
    autocomplete_fields = ("producto",)


@admin.register(Recepcion)
class RecepcionAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("numero", "orden", "almacen", "fecha", "guia_proveedor")
    list_filter = ("empresa", "almacen")
    search_fields = ("numero", "guia_proveedor")
    inlines = (LineaRecepcionInline,)
