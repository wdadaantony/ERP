"""Stock, movimientos y guías."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from apps.catalogo.models import Producto
from apps.inventario.models import (
    GuiaRemision, MovimientoStock, ReservaStock, Ubicacion, stock_disponible, stock_en_mano,
)


@login_required
def stock(request):
    productos = Producto.objects.filter(
        empresa=request.empresa, activo=True, controla_stock=True
    ).select_related("unidad_medida")
    q = request.GET.get("q", "").strip()
    if q:
        productos = productos.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q))
    filas = []
    bajo_minimo = 0
    for p in productos:
        en_mano = stock_en_mano(p, empresa=request.empresa)
        disponible = stock_disponible(p, empresa=request.empresa)
        alerta = p.stock_minimo and en_mano < p.stock_minimo
        bajo_minimo += bool(alerta)
        filas.append({
            "producto": p, "en_mano": en_mano, "reservado": en_mano - disponible,
            "disponible": disponible, "alerta": alerta, "valor": en_mano * p.costo_promedio,
        })
    if request.GET.get("f") == "alerta":
        filas = [f for f in filas if f["alerta"]]
    return render(request, "inventario/stock.html", {
        "filas": filas, "q": q, "f": request.GET.get("f", ""), "bajo_minimo": bajo_minimo,
        "valor_total": sum(f["valor"] for f in filas),
    })


@login_required
def movimientos(request):
    movs = MovimientoStock.objects.filter(empresa=request.empresa).select_related(
        "producto", "origen", "destino", "usuario"
    )
    q = request.GET.get("q", "").strip()
    producto_id = request.GET.get("producto")
    if q:
        movs = movs.filter(
            Q(producto__codigo__icontains=q) | Q(producto__nombre__icontains=q)
            | Q(documento_origen__icontains=q)
        )
    producto = None
    if producto_id:
        producto = get_object_or_404(Producto, pk=producto_id, empresa=request.empresa)
        movs = movs.filter(producto=producto)
    pagina = Paginator(movs, 50).get_page(request.GET.get("pagina"))
    return render(request, "inventario/movimientos.html", {
        "page_obj": pagina, "q": q, "producto": producto,
        "consulta": f"q={q}&producto={producto_id or ''}",
        "T": Ubicacion.Tipo,
    })


@login_required
def guias(request):
    lista = GuiaRemision.objects.filter(empresa=request.empresa).select_related(
        "destinatario", "pedido"
    )
    pagina = Paginator(lista, 40).get_page(request.GET.get("pagina"))
    return render(request, "inventario/guias.html", {"page_obj": pagina, "consulta": ""})
