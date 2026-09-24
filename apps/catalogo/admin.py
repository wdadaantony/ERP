from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.catalogo.models import (
    CategoriaProducto,
    ListaPrecios,
    PrecioProducto,
    Producto,
    UnidadMedida,
)


@admin.register(Producto)
class ProductoAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = (
        "codigo",
        "nombre",
        "tipo",
        "unidad_medida",
        "precio_lista",
        "afectacion_igv",
        "controla_stock",
        "activo",
    )
    list_filter = ("empresa", "tipo", "activo", "controla_stock", "afectacion_igv", "categoria")
    search_fields = ("codigo", "nombre", "descripcion")


@admin.register(UnidadMedida)
class UnidadMedidaAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("codigo", "nombre", "empresa")
    list_filter = ("empresa",)


@admin.register(CategoriaProducto)
class CategoriaProductoAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("nombre", "padre", "empresa")
    list_filter = ("empresa",)


class PrecioProductoInline(admin.TabularInline):
    model = PrecioProducto
    extra = 0
    autocomplete_fields = ("producto",)


@admin.register(ListaPrecios)
class ListaPreciosAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("nombre", "moneda", "activa", "empresa")
    list_filter = ("empresa", "activa")
    inlines = (PrecioProductoInline,)
