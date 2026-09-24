from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render


@login_required
def cotizacion(request, pk):
    from apps.ventas.vistas import _mis_pedidos
    pedido = get_object_or_404(_mis_pedidos(request), pk=pk)
    return render(request, 'gestion/cotizacion.html', {'pedido': pedido, 'lineas': pedido.lineas.select_related('producto')})
