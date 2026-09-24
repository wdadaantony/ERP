from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from apps.core.tests.test_flujo_completo import BaseFlujo
from apps.core.models import Empresa, LimiteAprobacion, Usuario
from apps.core.permisos import crear_roles
from apps.contabilidad.models import CuentaContable, ReglaContable, Asiento, PeriodoContable
from apps.inventario.models import Ubicacion, stock_en_mano, stock_disponible, MovimientoStock
from apps.inventario.servicios import despachar_pedido
from apps.ventas.servicios import confirmar_pedido, cancelar_pedido
from apps.terceros.models import CondicionPago
from apps.tesoreria.models import CuentaBancaria, Movimiento, LineaExtractoBancario
from apps.tesoreria.servicios import conciliar_extracto
from apps.crm.models import Lead, ArchivoLead
from .models import FacturaProveedor, OperacionInventario, Membresia, PerfilEmpresa
from .servicios import aprobar_factura, pagar_factura, aplicar_inventario


class GestionOperativa(BaseFlujo):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        crear_roles()
        cls.usuario.groups.add(Group.objects.get(name='ERP · Gerencia'))
        cls.destino = CuentaContable.objects.get(empresa=cls.empresa, codigo='2011')
        cls.impuesto = CuentaContable.objects.get(empresa=cls.empresa, codigo='4011')
        ReglaContable.objects.create(empresa=cls.empresa, tipo_documento='compra_igv', cuenta_debe=cls.impuesto, cuenta_haber=cls.impuesto)
        cls.cuenta = CuentaBancaria.objects.get(empresa=cls.empresa)
        cls.ubicacion = Ubicacion.objects.get(empresa=cls.empresa, codigo='ALM-01/STOCK')

    def factura(self, **kwargs):
        datos = dict(empresa=self.empresa, proveedor=self.proveedor, numero='F001-1', fecha=date.today(),
            vencimiento=date.today(), subtotal=100, impuestos=18, cuenta_destino=self.destino, concepto='Mercadería', creada_por=self.usuario)
        datos.update(kwargs)
        return FacturaProveedor.objects.create(**datos)

    def operacion(self, **kwargs):
        datos = dict(empresa=self.empresa, producto=self.laptop, tipo='conteo', origen=self.ubicacion,
            cantidad=9, existencia_esperada=10, solicitante=self.usuario, motivo='Conteo físico revisado')
        datos.update(kwargs)
        return OperacionInventario.objects.create(**datos)

    def test_factura_pago_parcial_idempotencia_y_conciliacion(self):
        f = self.factura()
        aprobar_factura(f, self.usuario)
        self.assertTrue(f.asiento.cuadra)
        aprobar_factura(f, self.usuario)
        self.assertEqual(Asiento.objects.count(), 1)
        m = pagar_factura(f, self.usuario, 50, self.cuenta, 'OP-1')
        self.assertEqual(f.saldo, 68)
        self.assertEqual(pagar_factura(f, self.usuario, 50, self.cuenta, 'OP-1').pk, m.pk)
        with self.assertRaises(ValidationError):
            pagar_factura(f, self.usuario, 69, self.cuenta, 'OP-2')
        self.assertEqual(Movimiento.objects.count(), 1)
        pagar_factura(f, self.usuario, 68, self.cuenta, 'OP-2')
        self.assertEqual(f.saldo, 0)
        LineaExtractoBancario.objects.create(empresa=self.empresa, cuenta=self.cuenta, fecha=date.today(), monto=-50, referencia='OP-1')
        self.assertEqual(conciliar_extracto(self.cuenta), 1)

    def test_pago_no_pasa_sin_aprobacion_y_periodo_cerrado_revierte(self):
        f = self.factura()
        with self.assertRaises(ValidationError):
            pagar_factura(f, self.usuario, 10, self.cuenta, 'OP-1')
        aprobar_factura(f, self.usuario)
        PeriodoContable.objects.filter(empresa=self.empresa).update(cerrado=True)
        with self.assertRaises(ValidationError):
            pagar_factura(f, self.usuario, 10, self.cuenta, 'OP-1')
        self.assertEqual(f.saldo, 118)
        self.assertFalse(Movimiento.objects.exists())

    def test_cuenta_ajena_no_contabiliza(self):
        otra = Empresa.objects.create(ruc='20999999991', razon_social='Otra')
        cuenta = CuentaContable.objects.create(empresa=otra, codigo='2011', nombre='Ajena')
        f = self.factura(cuenta_destino=cuenta)
        with self.assertRaises(ValidationError):
            aprobar_factura(f, self.usuario)
        self.assertFalse(Asiento.objects.exists())

    def test_pago_multimoneda_cierra_al_centavo(self):
        self.cuenta.moneda = 'USD'
        self.cuenta.save()
        f = self.factura(moneda='USD', tipo_cambio=Decimal('3.555555'))
        aprobar_factura(f, self.usuario)
        for i, monto in enumerate([39, 39, 40]):
            pagar_factura(f, self.usuario, monto, self.cuenta, f'USD-{i}', tipo_cambio=Decimal('3.6'))
        self.assertEqual(f.saldo, 0)
        self.assertTrue(all(a.cuadra for a in Asiento.objects.all()))

    def test_conteo_obsoleto_y_stock_reservado(self):
        op = self.operacion(existencia_esperada=11)
        with self.assertRaises(ValidationError):
            aplicar_inventario(op, self.usuario)
        p = self._pedido(cantidad=10)
        confirmar_pedido(p)
        with self.assertRaises(ValidationError):
            aplicar_inventario(self.operacion(), self.usuario)
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), 10)

    def test_transferencia_y_conteo_idempotente(self):
        destino = Ubicacion.objects.create(empresa=self.empresa, almacen=self.almacen, codigo='ALM-01/OTRO', nombre='Otro')
        op = self.operacion(tipo='transferencia', destino=destino, cantidad=3)
        aplicar_inventario(op, self.usuario)
        aplicar_inventario(op, self.usuario)
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), 10)
        self.assertEqual(stock_en_mano(self.laptop, destino, self.empresa), 3)
        conteo = self.operacion(cantidad=6, existencia_esperada=7)
        aplicar_inventario(conteo, self.usuario)
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), 9)

    def test_despacho_parcial_no_duplica_y_no_cancela(self):
        p = self._pedido(cantidad=4)
        confirmar_pedido(p)
        linea = p.lineas.get()
        for _ in range(2):
            despachar_pedido(p, self.usuario, cantidades_acumuladas={linea.pk: 2})
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), 8)
        self.assertEqual(stock_disponible(self.laptop, empresa=self.empresa), 6)
        self.assertEqual(p.estado, 'reservado')
        with self.assertRaises(ValidationError):
            cancelar_pedido(p)
        despachar_pedido(p, self.usuario)
        self.assertEqual(p.estado, 'entregado')
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), 6)

    def test_devolucion_no_supera_original(self):
        p = self._pedido(cantidad=2)
        confirmar_pedido(p)
        original = despachar_pedido(p)[0]
        aplicar_inventario(self.operacion(tipo='devolucion', movimiento_original=original, cantidad=1), self.usuario)
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), 9)
        with self.assertRaises(ValidationError):
            aplicar_inventario(self.operacion(tipo='devolucion', movimiento_original=original, cantidad=2), self.usuario)

    def test_credito_cuenta_pedidos_comprometidos(self):
        condicion = CondicionPago.objects.create(empresa=self.empresa, nombre='Crédito', dias=30)
        self.cliente.condicion_pago = condicion
        self.cliente.linea_credito = 4000
        self.cliente.save()
        p = self._pedido(cantidad=1)
        with self.assertRaises(ValidationError):
            confirmar_pedido(p, self.usuario)
        LimiteAprobacion.objects.create(empresa=self.empresa, usuario=self.usuario, puede_vender_al_credito=True)
        confirmar_pedido(p, self.usuario)
        otro = self._pedido(cantidad=1)
        with self.assertRaises(ValidationError):
            confirmar_pedido(otro, self.usuario)
        otro.refresh_from_db()
        self.assertEqual(otro.estado, 'borrador')

    def test_membresias_no_filtran_roles_de_otra_empresa(self):
        otra = Empresa.objects.create(ruc='20999999992', razon_social='Otra')
        self.usuario.empresas.add(otra)
        m = Membresia.objects.create(empresa=self.empresa, usuario=self.usuario)
        m.roles.add(Group.objects.get(name='ERP · Ventas'))
        m2 = Membresia.objects.create(empresa=otra, usuario=self.usuario)
        m2.roles.add(Group.objects.get(name='ERP · Compras'))
        self.assertFalse(self.usuario.has_perm('gestion.aprobar_factura'))
        self.assertTrue(self.usuario.has_perm('ventas.add_pedido'))
        self.usuario.empresa_actual = otra
        self.usuario.save()
        self.assertFalse(self.usuario.has_perm('ventas.add_pedido'))
        self.assertTrue(self.usuario.has_perm('compras.add_ordencompra'))

    def test_archivos_privados_respetan_propietario(self):
        lead = Lead.objects.create(empresa=self.empresa, nombre='Privado', vendedor=self.usuario)
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            archivo = ArchivoLead.objects.create(empresa=self.empresa, lead=lead, archivo=SimpleUploadedFile('prueba.pdf', b'%PDF-ejemplo'))
            self.client.force_login(self.usuario)
            respuesta = self.client.get(reverse('core:archivo', args=['lead', archivo.pk]))
            self.assertEqual(respuesta.status_code, 200)
            # Cerrar el archivo sin emitir request_finished dentro del atomic de TestCase.
            with patch('django.http.response.signals.request_finished.send'):
                respuesta.close()
            self.assertEqual(self.client.get('/media/' + archivo.archivo.name).status_code, 404)
            otro = Usuario.objects.create_user('otro', empresa_actual=self.empresa)
            otro.empresas.add(self.empresa)
            otro.groups.add(Group.objects.get(name='ERP · Ventas'))
            self.client.force_login(otro)
            self.assertEqual(self.client.get(reverse('core:archivo', args=['lead', archivo.pk])).status_code, 404)

    def test_pantallas_y_permisos_de_gestion(self):
        self.client.force_login(self.usuario)
        for nombre in ['proveedores', 'nueva_factura', 'inventario', 'nueva_operacion', 'cierre', 'agenda', 'reportes', 'preparacion']:
            self.assertEqual(self.client.get(reverse('gestion:' + nombre)).status_code, 200, nombre)
        f = self.factura()
        self.assertEqual(self.client.get(reverse('gestion:factura', args=[f.pk])).status_code, 200)
        self.usuario.groups.set([Group.objects.get(name='ERP · Ventas')])
        self.assertEqual(self.client.post(reverse('gestion:aprobar', args=[f.pk])).status_code, 403)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_recuperacion_envia_enlace_sin_revelar_usuario(self):
        from django.core import mail
        self.usuario.email = 'vendedor@example.test'
        self.usuario.save()
        r = self.client.post(reverse('cuenta:recuperar'), {'email': self.usuario.email})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/cuenta/restablecer/', mail.outbox[0].body)

    def test_exportacion_no_ejecuta_formula(self):
        from .vistas import csv_seguro
        r = csv_seguro('prueba', ['Cliente'], [['=SUM(1,1)']])
        self.assertIn("'=SUM", r.content.decode())

    def test_adaptadores_reales_bloqueados_y_secretos_externos(self):
        import os
        from apps.integraciones.configuracion import secreto, ocultar_secretos
        from apps.integraciones.conectores import obtener_conector, ErrorConector
        from apps.integraciones.models import ServicioExterno
        servicio = ServicioExterno.objects.filter(empresa=self.empresa, codigo='ose').first()
        servicio.modo_simulado = False
        with self.assertRaises(ErrorConector):
            obtener_conector(servicio).ejecutar('emitir_comprobante', {})
        with patch.dict(os.environ, {'ERP_PRUEBA_SECRETO': 'valor'}):
            self.assertEqual(secreto('env:ERP_PRUEBA_SECRETO'), 'valor')
        self.assertEqual(ocultar_secretos({'nested': {'token': 'privado'}})['nested']['token'], '[oculto]')

    def test_alta_empresa_sin_datos_demo(self):
        call_command('alta_empresa', ruc='20999999993', razon_social='Nueva', usuario=self.usuario.username, stdout=StringIO())
        nueva = Empresa.objects.get(ruc='20999999993')
        from apps.catalogo.models import Producto
        self.assertFalse(Producto.objects.filter(empresa=nueva).exists())
        self.assertTrue(Membresia.objects.filter(empresa=nueva, usuario=self.usuario).exists())
        self.assertFalse(MovimientoStock.objects.filter(empresa=nueva).exists())

    def test_importacion_invalida_no_deja_productos_parciales(self):
        from .importacion import importar_productos
        from apps.catalogo.models import Producto
        cabecera = 'codigo,nombre,unidad,tipo,precio_lista,stock_minimo\n'
        contenido = (cabecera + 'NUEVO,Producto nuevo,NIU,bien,10,0\nOTRO,Producto malo,NOEXISTE,bien,10,0\n').encode()
        with self.assertRaises(ValidationError):
            importar_productos(self.empresa, contenido, self.usuario)
        self.assertFalse(Producto.objects.filter(codigo='NUEVO').exists())
        contenido = (cabecera + 'NUEVO,Producto nuevo,NIU,bien,10,0\n').encode()
        self.assertEqual(importar_productos(self.empresa, contenido, self.usuario), 1)
        with self.assertRaises(ValidationError):
            importar_productos(self.empresa, contenido, self.usuario)

    def test_adaptador_guarda_documentos_privados_inmutables(self):
        import base64
        from apps.facturacion.archivos import guardar_archivos
        from apps.facturacion.servicios import emitir_comprobante
        p = self._pedido(cantidad=1)
        confirmar_pedido(p)
        despachar_pedido(p)
        c, _ = emitir_comprobante(p)
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            datos = {'pdf': base64.b64encode(b'%PDF-1.4\nprueba').decode()}
            guardar_archivos(c, datos)
            nombre = c.pdf.name
            guardar_archivos(c, datos)
            self.assertEqual(c.pdf.name, nombre)
            with self.assertRaises(ValidationError):
                guardar_archivos(c, {'pdf': base64.b64encode(b'%PDF-1.4\notro').decode()})
            with self.assertRaises(ValidationError):
                guardar_archivos(c, {'xml': '!base64-invalido!'})

    def test_asiento_manual_desbalanceado_no_crea_documento(self):
        self.client.force_login(self.usuario)
        datos = {'fecha': date.today().isoformat(), 'glosa': 'Ajuste de prueba',
            'lineas-TOTAL_FORMS': '2', 'lineas-INITIAL_FORMS': '0',
            'lineas-0-cuenta': self.destino.pk, 'lineas-0-debe': '100',
            'lineas-1-cuenta': self.impuesto.pk, 'lineas-1-haber': '99'}
        r = self.client.post(reverse('gestion:ajuste'), datos)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Asiento.objects.exists())
        datos['lineas-1-haber'] = '100'
        self.assertEqual(self.client.post(reverse('gestion:ajuste'), datos).status_code, 302)
        self.assertTrue(Asiento.objects.get().cuadra)

    def test_balance_filtra_fechas_y_rechaza_periodo_invalido(self):
        self.client.force_login(self.usuario)
        aprobar_factura(self.factura(), self.usuario)
        r = self.client.get(reverse('contabilidad:balance'), {'hasta': '2000-01-01'})
        self.assertEqual(r.context['total_debe'], 0)
        self.assertEqual(self.client.get(reverse('contabilidad:asientos'), {'periodo': 'no-fecha'}).status_code, 200)

    def test_contabilizacion_stock_configurable(self):
        costo = CuentaContable.objects.get(empresa=self.empresa, codigo='6911')
        ReglaContable.objects.create(empresa=self.empresa, tipo_documento='costo_venta', cuenta_debe=costo, cuenta_haber=self.destino)
        PerfilEmpresa.objects.create(empresa=self.empresa, contabilizar_inventario=True)
        p = self._pedido(cantidad=1)
        confirmar_pedido(p)
        despachar_pedido(p, self.usuario)
        self.assertTrue(Asiento.objects.get(clave_automatica__startswith='stock:').cuadra)

    @override_settings(ERP_REQUIRE_ENV_SECRETS=True)
    def test_secretos_estructurados_no_evaden_referencias_de_entorno(self):
        from types import SimpleNamespace
        from apps.integraciones.configuracion import credenciales
        for datos in (["secreto"], {"oauth": {"token": "secreto"}}, {"pin": 1234}):
            with self.assertRaises(ValidationError):
                credenciales(SimpleNamespace(credenciales=datos))

    def test_dashboard_proveedores_y_graficas(self):
        self.client.force_login(self.usuario)
        f = self.factura()
        aprobar_factura(f, self.usuario)
        pagar_factura(f, self.usuario, 50, self.cuenta, 'DASH-1')
        respuesta = self.client.get(reverse('core:inicio'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['saldo_proveedores'], Decimal('68'))
        self.assertEqual(len(respuesta.context['serie_facturacion']), 6)
        self.assertContains(respuesta, 'Dashboard de tu empresa')
        self.assertContains(respuesta, 'Cuentas por pagar')

    def test_dashboard_no_consulta_indicadores_sin_permiso(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from apps.core.dashboard import indicadores
        usuario = Mock()
        usuario.has_perm.return_value = False
        consulta = Mock()
        datos = indicadores(SimpleNamespace(user=usuario, empresa=self.empresa), consulta, consulta, date.today())
        self.assertIsNone(datos['saldo_proveedores'])
        self.assertEqual(datos['serie_facturacion'], [])
        self.assertEqual(datos['distribucion_pedidos'], [])
        self.assertEqual(consulta.mock_calls, [])
