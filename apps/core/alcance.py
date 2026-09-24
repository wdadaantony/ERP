"""Permiso por registro: «solo mis clientes», «solo mis pedidos».

Dos vendedores en el mismo módulo ven listas distintas. Quien tenga el permiso
`ver_todo` del módulo (jefes, administración) ve la empresa completa.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


def ve_todo(usuario, app):
    """¿Puede este usuario ver todos los registros del módulo, no solo los suyos?"""
    return usuario.is_superuser or usuario.has_perm(f"{app}.ver_todo")


def acotar(queryset, request, app, campo_usuario):
    """Filtra por empresa activa y, si no ve todo, por el usuario dueño del registro."""
    queryset = queryset.filter(empresa=request.empresa)
    if not ve_todo(request.user, app):
        queryset = queryset.filter(**{campo_usuario: request.user})
    return queryset


class VistaERP(LoginRequiredMixin):
    """Base de todas las vistas operativas: exige sesión y empresa activa.

    Las subclases declaran `app` y, si el modelo tiene dueño, `campo_usuario`.
    """

    app = ""
    campo_usuario = None
    permiso_requerido = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.permiso_requerido and not request.user.has_perm(self.permiso_requerido):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def acotar(self, queryset):
        if self.campo_usuario:
            return acotar(queryset, self.request, self.app, self.campo_usuario)
        return queryset.filter(empresa=self.request.empresa)

    def get_queryset(self):
        return self.acotar(super().get_queryset())
