from django.urls import path

from apps.catalogo import vistas

app_name = "catalogo"

urlpatterns = [
    path("", vistas.lista, name="lista"),
    path("nuevo/", vistas.editar, name="nuevo"),
    path("<int:pk>/editar/", vistas.editar, name="editar"),
]
