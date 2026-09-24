from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin
from django.contrib.auth.admin import UserAdmin

from apps.core.models import Empresa, LimiteAprobacion, RegistroAuditoria, Serie, Usuario


@admin.register(Empresa)
class EmpresaAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        # Cambiar la moneda base reinterpretaría todo el histórico contable.
        return ("moneda_base",) if obj else ()
    list_display = ("razon_social", "ruc", "moneda_base", "activa")
    search_fields = ("razon_social", "ruc")
    list_filter = ("activa", "moneda_base")


@admin.register(Serie)
class SerieAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    readonly_fields = ("correlativo_actual",)

    def get_readonly_fields(self, request, obj=None):
        return ("correlativo_actual", "empresa", "tipo_documento", "serie") if obj else ("correlativo_actual",)

    def has_delete_permission(self, request, obj=None):
        return False
    list_display = ("serie", "tipo_documento", "correlativo_actual", "empresa", "activa")
    list_filter = ("empresa", "tipo_documento", "activa")


@admin.register(Usuario)
class UsuarioAdmin(SoloSuperusuarioMixin, UserAdmin):
    list_display = ("username", "first_name", "last_name", "email", "empresa_actual", "is_staff")
    fieldsets = UserAdmin.fieldsets + (
        ("ERP", {"fields": ("empresas", "empresa_actual", "telefono")}),
    )
    filter_horizontal = UserAdmin.filter_horizontal + ("empresas",)


@admin.register(LimiteAprobacion)
class LimiteAprobacionAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    list_display = (
        "usuario",
        "empresa",
        "descuento_maximo_pct",
        "monto_maximo_venta",
        "monto_maximo_compra",
        "puede_vender_al_credito",
    )
    list_filter = ("empresa",)


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    """La auditoría se lee, no se edita."""

    list_display = ("creado_en", "usuario", "modelo", "objeto_id", "accion", "canal")
    list_filter = ("accion", "modelo", "empresa")
    search_fields = ("objeto_id", "modelo")
    date_hierarchy = "creado_en"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
