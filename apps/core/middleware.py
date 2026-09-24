"""Empresa activa en cada petición.

Todo lo que se lista o se crea pertenece a `request.empresa`. Si el usuario tiene
varias, puede cambiarla; si no tiene ninguna, no puede operar.
"""
from django.shortcuts import redirect
from django.urls import reverse

RUTAS_LIBRES = ("/admin/", "/integraciones/webhook/", "/cuenta/", "/static/", "/salud/")


class EmpresaActivaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.empresa = None
        usuario = getattr(request, "user", None)
        if usuario is not None and usuario.is_authenticated:
            request.empresa = self._resolver(usuario)
            if request.empresa is None and request.path != reverse("core:sin_empresa") and not request.path.startswith(RUTAS_LIBRES):
                return redirect(reverse("core:sin_empresa"))
        return self.get_response(request)

    def _resolver(self, usuario):
        if usuario.empresa_actual_id and usuario.empresa_actual.activa and (
            usuario.is_superuser or usuario.empresas.filter(pk=usuario.empresa_actual_id).exists()
        ):
            return usuario.empresa_actual
        # Sin empresa activa: se toma la primera disponible y se recuerda.
        if usuario.is_superuser:
            from apps.core.models import Empresa

            empresa = Empresa.objects.filter(activa=True).order_by("id").first()
        else:
            empresa = usuario.empresas.filter(activa=True).order_by("id").first()
        if empresa is not None:
            usuario.empresa_actual = empresa
            usuario.save(update_fields=["empresa_actual"])
        return empresa
