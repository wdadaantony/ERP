from django.urls import path

from apps.crm import vistas

app_name = "crm"

urlpatterns = [
    path("", vistas.embudo, name="embudo"),
    path("lista/", vistas.lista, name="lista"),
    path("nuevo/", vistas.editar, name="nuevo"),
    path("<int:pk>/", vistas.detalle, name="detalle"),
    path("<int:pk>/editar/", vistas.editar, name="editar"),
    path("<int:pk>/mover/", vistas.mover, name="mover"),
    path("<int:pk>/a-cliente/", vistas.a_cliente, name="a_cliente"),
    path("<int:pk>/a-pedido/", vistas.a_pedido, name="a_pedido"),
    path("<int:pk>/cerrar/", vistas.cerrar, name="cerrar"),
    path("<int:pk>/archivos/", vistas.subir_archivo, name="subir_archivo"),
    path("filtros/guardar/", vistas.guardar_filtro, name="guardar_filtro"),
    path("filtros/<int:pk>/eliminar/", vistas.eliminar_filtro, name="eliminar_filtro"),
]
