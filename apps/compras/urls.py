from django.urls import path

from apps.compras import vistas

app_name = "compras"

urlpatterns = [
    path("", vistas.lista, name="lista"),
    path("nueva/", vistas.editar, name="nueva"),
    path("<int:pk>/", vistas.detalle, name="detalle"),
    path("<int:pk>/editar/", vistas.editar, name="editar"),
    path("<int:pk>/aprobar/", vistas.aprobar, name="aprobar"),
    path("<int:pk>/enviar/", vistas.enviar, name="enviar"),
    path("<int:pk>/recibir/", vistas.recibir, name="recibir"),
]
