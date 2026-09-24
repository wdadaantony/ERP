from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.inventario.models import Almacen, Lote, MovimientoStock, ReservaStock, Ubicacion


@admin.register(Almacen)
class AlmacenAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("codigo", "nombre", "responsable", "activo", "empresa")
    list_filter = ("empresa", "activo")
    search_fields = ("codigo", "nombre")


@admin.register(Ubicacion)
class UbicacionAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("codigo", "nombre", "tipo", "almacen", "empresa")
    list_filter = ("empresa", "tipo", "almacen")
    search_fields = ("codigo", "nombre")


@admin.register(MovimientoStock)
class MovimientoStockAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    """Los movimientos se consultan; se crean desde los documentos, no a mano."""

    list_display = (
        "fecha",
        "producto",
        "cantidad",
        "origen",
        "destino",
        "documento_origen",
        "usuario",
    )
    list_filter = ("empresa", "origen", "destino")
    search_fields = ("producto__codigo", "producto__nombre", "documento_origen")
    date_hierarchy = "fecha"
    autocomplete_fields = ("producto",)

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ReservaStock)
class ReservaStockAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("producto", "cantidad", "ubicacion", "estado", "documento_origen")
    list_filter = ("empresa", "estado")
    search_fields = ("producto__codigo", "documento_origen")


@admin.register(Lote)
class LoteAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("codigo", "producto", "fecha_vencimiento", "empresa")
    list_filter = ("empresa",)
    search_fields = ("codigo",)
