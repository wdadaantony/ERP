from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.terceros.models import CondicionPago, Contacto, DireccionEntrega, Tercero


class ContactoInline(admin.TabularInline):
    model = Contacto
    extra = 0


class DireccionEntregaInline(admin.TabularInline):
    model = DireccionEntrega
    extra = 0


@admin.register(Tercero)
class TerceroAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = (
        "numero_documento",
        "razon_social",
        "es_cliente",
        "es_proveedor",
        "vendedor_asignado",
        "activo",
    )
    list_filter = ("empresa", "es_cliente", "es_proveedor", "activo", "tipo_documento")
    search_fields = ("razon_social", "numero_documento", "email", "nombre_comercial")
    inlines = (ContactoInline, DireccionEntregaInline)


@admin.register(CondicionPago)
class CondicionPagoAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("nombre", "dias", "empresa")
    list_filter = ("empresa",)
