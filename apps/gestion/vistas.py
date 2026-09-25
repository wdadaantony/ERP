import csv
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core.transacciones import vista_serializada
from apps.core.auditoria import registrar
from apps.core.alcance import acotar
from apps.inventario.models import stock_en_mano
from .models import FacturaProveedor, OperacionInventario
from .formularios import FormularioFactura, FormularioPago, FormularioInventario, FormularioPeriodo
from .servicios import aprobar_factura, pagar_factura, aplicar_inventario


def formulario(request, form, titulo, aviso=''):
    return render(request, 'gestion/formulario.html', {'form': form, 'titulo': titulo, 'aviso': aviso})


@login_required
def preparacion(request):
    from .preparacion import revisar
    controles, conexiones, fases = revisar(request.empresa)
    return render(request, 'gestion/preparacion.html', {'controles': controles, 'conexiones': conexiones, 'fases': fases})


@login_required
def importar(request):
    from django import forms
    from .importacion import analizar_productos, importar_productos
    class Carga(forms.Form):
        archivo = forms.FileField(label='Archivo CSV')
        confirmar = forms.BooleanField(required=False, label='Confirmo la importación de los productos del archivo validado')
    form = Carga(request.POST or None, request.FILES or None)
    vista_previa = []
    if request.method == 'POST' and form.is_valid():
        try:
            archivo = form.cleaned_data['archivo']
            if archivo.size > 2 * 1024 * 1024:
                raise ValidationError('El archivo no puede superar 2 MB.')
            contenido = archivo.read()
            if form.cleaned_data['confirmar']:
                total = importar_productos(request.empresa, contenido, request.user)
                messages.success(request, f'{total} productos importados sin modificar existencias.')
                return redirect('catalogo:lista')
            vista_previa = analizar_productos(request.empresa, contenido)
        except ValidationError as exc:
            form.add_error(None, '; '.join(exc.messages))
    return render(request, 'gestion/importar.html', {'form': form, 'filas': vista_previa})


@login_required
def despacho(request, pk):
    from django import forms
    from apps.ventas.vistas import _mis_pedidos
    from apps.inventario.servicios import despachar_pedido
    p = get_object_or_404(_mis_pedidos(request), pk=pk, estado='reservado')
    form = forms.Form(request.POST or None)
    for linea in p.lineas.select_related('producto'):
        form.fields[str(linea.pk)] = forms.DecimalField(label=f'{linea.producto.nombre} · entregado: {linea.cantidad_entregada} / pedido: {linea.cantidad}',
            initial=linea.cantidad, min_value=linea.cantidad_entregada, max_value=linea.cantidad, decimal_places=4)
    if request.method == 'POST' and form.is_valid():
        try:
            despachar_pedido(p, request.user, cantidades_acumuladas={int(k): v for k, v in form.cleaned_data.items()})
            messages.success(request, 'Entrega registrada. Las cantidades pendientes conservan su reserva.')
            return redirect('ventas:detalle', pk=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return formulario(request, form, f'Entrega parcial · {p.numero}', 'Indica el total acumulado que quedará entregado tras esta salida. Repetir el mismo total no vuelve a descontar stock.')


@login_required
def proveedores(request):
    qs = FacturaProveedor.objects.filter(empresa=request.empresa).select_related('proveedor', 'empresa')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(proveedor__razon_social__icontains=q))
    return render(request, 'gestion/proveedores.html', {'page_obj': Paginator(qs, 30).get_page(request.GET.get('pagina')), 'q': q})


@login_required
@vista_serializada
def nueva_factura(request):
    form = FormularioFactura(request.POST or None, empresa=request.empresa)
    if request.method == 'POST' and form.is_valid():
        factura = form.save(commit=False)
        factura.creada_por = request.user
        factura.save()
        registrar(factura, request.user, 'crear', despues={'numero': factura.numero, 'total': str(factura.total)})
        return redirect('gestion:factura', pk=factura.pk)
    return formulario(request, form, 'Registrar factura de proveedor', 'La factura queda pendiente de aprobación. Todavía no genera deuda contabilizada.')


@login_required
def factura(request, pk):
    f = get_object_or_404(FacturaProveedor, empresa=request.empresa, pk=pk)
    return render(request, 'gestion/factura.html', {'f': f, 'pagos': f.pagos.select_related('movimiento', 'usuario')})


@login_required
@require_POST
def aprobar(request, pk):
    f = get_object_or_404(FacturaProveedor, empresa=request.empresa, pk=pk)
    try:
        aprobar_factura(f, request.user)
        messages.success(request, 'Factura aprobada y contabilizada.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect('gestion:factura', pk=pk)


@login_required
def pagar(request, pk):
    f = get_object_or_404(FacturaProveedor, empresa=request.empresa, pk=pk, estado='aprobada')
    form = FormularioPago(request.POST or None, factura=f)
    if request.method == 'POST' and form.is_valid():
        try:
            pagar_factura(f, request.user, **form.cleaned_data)
            messages.success(request, 'Pago registrado y contabilizado. Disponible para conciliación bancaria.')
            return redirect('gestion:factura', pk=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return formulario(request, form, f'Registrar pago · {f.numero}', 'Registra una operación bancaria ya realizada. Este formulario no transfiere dinero ni se conecta al banco.')


@login_required
def inventario(request):
    qs = OperacionInventario.objects.filter(empresa=request.empresa).select_related('producto', 'origen', 'destino', 'solicitante')
    return render(request, 'gestion/inventario.html', {'page_obj': Paginator(qs, 30).get_page(request.GET.get('pagina'))})


@login_required
@vista_serializada
def nueva_operacion(request):
    form = FormularioInventario(request.POST or None, empresa=request.empresa)
    if request.method == 'POST' and form.is_valid():
        op = form.save(commit=False)
        op.empresa, op.solicitante = request.empresa, request.user
        op.existencia_esperada = stock_en_mano(op.producto, op.origen, request.empresa)
        op.save()
        registrar(op, request.user, 'crear', despues={'tipo': op.tipo, 'cantidad': str(op.cantidad), 'motivo': op.motivo})
        messages.success(request, 'Solicitud registrada. Requiere aprobación antes de mover stock.')
        return redirect('gestion:inventario')
    return formulario(request, form, 'Nueva operación de inventario')


@login_required
@require_POST
def aprobar_operacion(request, pk):
    op = get_object_or_404(OperacionInventario, empresa=request.empresa, pk=pk)
    try:
        aplicar_inventario(op, request.user)
        messages.success(request, 'Operación aplicada y auditada.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect('gestion:inventario')


@login_required
def cierre(request):
    from apps.contabilidad.servicios import cerrar_periodo_con_revaluacion
    form = FormularioPeriodo(request.POST or None, empresa=request.empresa)
    if request.method == 'POST' and form.is_valid():
        try:
            datos = form.cleaned_data
            periodo = datos['periodo']
            tasas = {m.upper(): datos[m] for m in ('pen', 'usd', 'eur') if datos.get(m)}
            cerrar_periodo_con_revaluacion(periodo, tasas, request.user)
            messages.success(request, 'Periodo cerrado con revaluación de cuentas por cobrar.')
            return redirect('contabilidad:balance')
        except ValidationError as exc:
            form.add_error(None, exc)
    return formulario(request, form, 'Cierre contable', 'Revalúa las cuentas por cobrar en moneda extranjera. La revisión de bancos, proveedores y ajustes debe completarse antes del cierre.')


@login_required
def agenda(request):
    from apps.crm.models import Actividad
    qs = acotar(Actividad.objects.filter(hecha=False), request, 'crm', 'lead__vendedor').select_related('lead', 'usuario').order_by('programada_para', 'pk')
    return render(request, 'gestion/agenda.html', {'page_obj': Paginator(qs, 40).get_page(request.GET.get('pagina')), 'ahora': timezone.now()})


@login_required
@require_POST
def completar_actividad(request, pk):
    from apps.crm.models import Actividad
    actividad = get_object_or_404(acotar(Actividad.objects.all(), request, 'crm', 'lead__vendedor'), pk=pk)
    actividad.hecha = True
    actividad.save(update_fields=['hecha', 'actualizado_en'])
    registrar(actividad, request.user, 'editar', {'hecha': False}, {'hecha': True})
    return redirect('gestion:agenda')


def csv_seguro(titulo, columnas, filas):
    respuesta = HttpResponse(content_type='text/csv; charset=utf-8')
    respuesta['Content-Disposition'] = f'attachment; filename="{titulo}.csv"'
    respuesta.write('\ufeff')
    escritor = csv.writer(respuesta)
    def seguro(valor):
        texto = str(valor if valor is not None else '')
        return "'" + texto if texto.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else texto
    escritor.writerow(columnas)
    for fila in filas:
        escritor.writerow([seguro(v) for v in fila])
    return respuesta


@login_required
def reportes(request):
    from django import forms
    from apps.facturacion.models import Comprobante
    from apps.core.monedas import a_moneda_base
    from apps.ventas.models import Pedido
    class Fechas(forms.Form):
        desde = forms.DateField(widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
        hasta = forms.DateField(widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
        def clean(self):
            datos = super().clean()
            if datos.get('desde') and datos.get('hasta') and datos['desde'] > datos['hasta']:
                raise forms.ValidationError('La fecha inicial no puede superar a la final.')
            return datos
    hoy = timezone.localdate()
    form = Fechas(request.GET if request.GET.get('desde') or request.GET.get('hasta') else {'desde': hoy.replace(day=1), 'hasta': hoy})
    filas, total, vencido = [], Decimal('0'), Decimal('0')
    if form.is_valid():
        qs = Comprobante.objects.filter(empresa=request.empresa, estado__in=['aceptado', 'observado'], fecha_emision__range=(form.cleaned_data['desde'], form.cleaned_data['hasta'])).select_related('empresa', 'tercero')
        for c in qs:
            importe = a_moneda_base(c.total, c) * (-1 if c.tipo == '07' else 1)
            total += importe
            filas.append([c.numero_completo, c.tercero.razon_social, c.fecha_emision, c.moneda, c.total, importe])
        if request.GET.get('exportar') == 'csv':
            return csv_seguro('ventas', ['Documento', 'Cliente', 'Fecha', 'Moneda', 'Total original', 'Neto moneda base'], filas)
    pendientes = Comprobante.objects.filter(empresa=request.empresa, estado__in=['aceptado', 'observado'], tipo__in=['01', '03']).select_related('empresa', 'tercero').prefetch_related('notas')
    tramos = {'Al día': Decimal('0'), '1–30 días': Decimal('0'), '31–60 días': Decimal('0'), '61–90 días': Decimal('0'), 'Más de 90 días': Decimal('0')}
    for c in pendientes:
        if c.saldo <= 0:
            continue
        dias = (hoy - c.fecha_vencimiento).days if c.fecha_vencimiento else 0
        tramo = 'Al día' if dias <= 0 else '1–30 días' if dias <= 30 else '31–60 días' if dias <= 60 else '61–90 días' if dias <= 90 else 'Más de 90 días'
        tramos[tramo] += c.saldo_base
    return render(request, 'gestion/reportes.html', {'form': form, 'filas': filas, 'total': total, 'tramos': tramos.items()})

