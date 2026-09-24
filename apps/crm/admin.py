from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.crm.models import Actividad, ArchivoLead, FiltroGuardado, Lead


class ActividadInline(admin.TabularInline):
    model = Actividad
    extra = 0


class ArchivoLeadInline(admin.TabularInline):
    model = ArchivoLead
    extra = 0


@admin.register(Lead)
class LeadAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = (
        "nombre", "empresa_lead", "estado", "prioridad", "vendedor", "equipo_ventas",
        "origen", "valor_estimado", "probabilidad", "cierre_esperado", "creado_en",
    )
    list_filter = ("empresa", "estado", "prioridad", "origen", "vendedor", "equipo_ventas")
    search_fields = ("nombre", "empresa_lead", "email", "telefono", "movil", "ciudad", "referido_por")
    inlines = (ActividadInline, ArchivoLeadInline)


@admin.register(FiltroGuardado)
class FiltroGuardadoAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("nombre", "usuario", "vista", "empresa")
    list_filter = ("empresa", "vista")
    search_fields = ("nombre", "usuario__username")
