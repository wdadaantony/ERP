"""Productos y servicios."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from apps.core.transacciones import vista_serializada
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.catalogo.models import CategoriaProducto, Producto, UnidadMedida
from apps.inventario.models import stock_disponible, stock_en_mano


class FormularioProducto(forms.ModelForm):
    class Meta:
        model = Producto
        fields = (
            "codigo", "nombre", "descripcion", "tipo", "categoria", "unidad_medida",
            "precio_lista", "afectacion_igv", "controla_stock", "controla_lotes",
            "controla_series", "stock_minimo", "activo",
        )
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].queryset = CategoriaProducto.objects.filter(empresa=empresa)
        self.fields["unidad_medida"].queryset = UnidadMedida.objects.filter(empresa=empresa)


@login_required
@vista_serializada
def lista(request):
    productos = Producto.objects.filter(empresa=request.empresa).select_related(
        "unidad_medida", "categoria"
    )
    q = request.GET.get("q", "").strip()
    filtro = request.GET.get("f", "")
    if q:
        productos = productos.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q))
    if filtro == "bienes":
        productos = productos.filter(tipo="bien")
    elif filtro == "servicios":
        productos = productos.filter(tipo="servicio")
    elif filtro == "inactivos":
        productos = productos.filter(activo=False)
    else:
        productos = productos.filter(activo=True)

    pagina = Paginator(productos, 40).get_page(request.GET.get("pagina"))
    for p in pagina:
        p.stock = stock_en_mano(p, empresa=request.empresa) if p.controla_stock else None
        p.disponible = stock_disponible(p, empresa=request.empresa) if p.controla_stock else None
        p.bajo_minimo = p.stock is not None and p.stock_minimo and p.stock < p.stock_minimo
    return render(request, "catalogo/lista.html", {
        "page_obj": pagina, "q": q, "f": filtro, "consulta": f"f={filtro}&q={q}",
    })


@login_required
@vista_serializada
def editar(request, pk=None):
    producto = get_object_or_404(Producto, pk=pk, empresa=request.empresa) if pk else None
    if request.method == "POST":
        form = FormularioProducto(request.POST, instance=producto, empresa=request.empresa)
        if form.is_valid():
            nuevo = form.save(commit=False)
            nuevo.empresa = request.empresa
            nuevo.save()
            messages.success(request, f"{nuevo.codigo} guardado.")
            return redirect("catalogo:lista")
    else:
        form = FormularioProducto(instance=producto, empresa=request.empresa)
    return render(request, "catalogo/editar.html", {"form": form, "producto": producto})
