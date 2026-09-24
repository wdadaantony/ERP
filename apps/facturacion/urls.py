from django.urls import path

from apps.facturacion import vistas

app_name = "facturacion"

urlpatterns = [
    path("", vistas.lista, name="lista"),
    path("<int:pk>/", vistas.detalle, name="detalle"),
    path("<int:pk>/nota-credito/", vistas.nota_credito, name="nota_credito"),
    path("<int:pk>/reenviar/", vistas.reenviar, name="reenviar"),
]
