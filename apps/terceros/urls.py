from django.urls import path

from apps.terceros import vistas

app_name = "terceros"

urlpatterns = [
    path("", vistas.lista, name="lista"),
    path("nuevo/", vistas.editar, name="nuevo"),
    path("<int:pk>/", vistas.detalle, name="detalle"),
    path("<int:pk>/editar/", vistas.editar, name="editar"),
]
