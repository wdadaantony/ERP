"""Documentos operativos complementarios, siempre separados por empresa."""
import uuid
from decimal import Decimal
from django.db import models
from django.db.models import Sum
from apps.core.models import ModeloEmpresa, Moneda


class FacturaProveedor(ModeloEmpresa):
    proveedor = models.ForeignKey('terceros.Tercero', on_delete=models.PROTECT)
    numero = models.CharField(max_length=40)
    orden = models.ForeignKey('compras.OrdenCompra', on_delete=models.PROTECT, null=True, blank=True)
    fecha = models.DateField()
    vencimiento = models.DateField()
    moneda = models.CharField(max_length=3, choices=Moneda, default=Moneda.PEN)
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=6, default=1)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    impuestos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cuenta_destino = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT)
    concepto = models.CharField(max_length=255)
    estado = models.CharField(max_length=15, default='borrador', choices=(('borrador', 'Por aprobar'), ('aprobada', 'Aprobada')))
    asiento = models.OneToOneField('contabilidad.Asiento', on_delete=models.PROTECT, null=True, blank=True)
    creada_por = models.ForeignKey('core.Usuario', on_delete=models.PROTECT, related_name='+')
    aprobada_por = models.ForeignKey('core.Usuario', on_delete=models.PROTECT, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ('vencimiento', 'pk')
        permissions = [('aprobar_factura', 'Puede aprobar facturas de proveedor'), ('pagar_factura', 'Puede registrar pagos a proveedores')]
        constraints = [models.UniqueConstraint(fields=('empresa', 'proveedor', 'numero'), name='factura_proveedor_unica'),
                       models.CheckConstraint(condition=models.Q(subtotal__gte=0, impuestos__gte=0), name='factura_proveedor_no_negativa')]

    @property
    def total(self):
        return self.subtotal + self.impuestos

    @property
    def pagado(self):
        return self.pagos.aggregate(t=Sum('movimiento__monto'))['t'] or Decimal('0')

    @property
    def saldo(self):
        return self.total - self.pagado

    def __str__(self):
        return f'{self.numero} · {self.proveedor}'


class PagoProveedor(ModeloEmpresa):
    factura = models.ForeignKey(FacturaProveedor, on_delete=models.PROTECT, related_name='pagos')
    movimiento = models.OneToOneField('tesoreria.Movimiento', on_delete=models.PROTECT, related_name='pago_proveedor')
    usuario = models.ForeignKey('core.Usuario', on_delete=models.PROTECT)


class OperacionInventario(ModeloEmpresa):
    clave = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tipo = models.CharField(max_length=15, choices=(('transferencia', 'Transferencia'), ('conteo', 'Conteo físico'), ('devolucion', 'Devolución de movimiento')))
    producto = models.ForeignKey('catalogo.Producto', on_delete=models.PROTECT)
    origen = models.ForeignKey('inventario.Ubicacion', on_delete=models.PROTECT, related_name='+')
    destino = models.ForeignKey('inventario.Ubicacion', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    cantidad = models.DecimalField(max_digits=14, decimal_places=4)
    existencia_esperada = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    movimiento_original = models.ForeignKey('inventario.MovimientoStock', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    motivo = models.CharField(max_length=255)
    solicitante = models.ForeignKey('core.Usuario', on_delete=models.PROTECT, related_name='+')
    aprobada_por = models.ForeignKey('core.Usuario', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    estado = models.CharField(max_length=15, default='pendiente', choices=(('pendiente', 'Por aprobar'), ('hecho', 'Aplicada')))
    movimiento = models.OneToOneField('inventario.MovimientoStock', on_delete=models.PROTECT, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ('-creado_en',)
        permissions = [('aprobar_inventario', 'Puede aprobar ajustes, transferencias y devoluciones')]


class PerfilEmpresa(ModeloEmpresa):
    """Configuración comercial por cliente; no cambia el significado de históricos."""
    empresa = models.OneToOneField('core.Empresa', on_delete=models.PROTECT, related_name='perfil')
    rubro = models.CharField(max_length=100, blank=True)
    contabilizar_inventario = models.BooleanField(default=False, help_text='Activar después de configurar costo_venta y ajuste_inventario y validar saldos iniciales.')
    correo_soporte = models.EmailField(blank=True)


class Membresia(ModeloEmpresa):
    usuario = models.ForeignKey('core.Usuario', on_delete=models.CASCADE, related_name='membresias')
    roles = models.ManyToManyField('auth.Group', blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('empresa', 'usuario'), name='membresia_empresa_usuario')]
