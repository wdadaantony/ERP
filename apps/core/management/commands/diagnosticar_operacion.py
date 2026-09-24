from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from apps.integraciones.models import TrabajoIntegracion
from apps.core.models import Empresa
from apps.gestion.preparacion import revisar


class Command(BaseCommand):
    help = 'Diagnóstico local de configuración y trabajos atrasados; no realiza conexiones.'
    def handle(self, *args, **options):
        for empresa in Empresa.objects.filter(activa=True):
            controles, conexiones = revisar(empresa)
            self.stdout.write(f'Empresa #{empresa.pk}: {empresa.razon_social}')
            for nombre, listo, detalle in controles:
                self.stdout.write(f'  {"OK" if listo else "PENDIENTE"}: {nombre} — {detalle}')
            for nombre, estado in conexiones:
                self.stdout.write(f'  {nombre}: {estado}')
        limite = timezone.now() - timedelta(minutes=15)
        atrasados = TrabajoIntegracion.objects.filter(estado__in=['en_cola', 'reintentar'], ejecutar_despues_de__lt=limite).count()
        fallidos = TrabajoIntegracion.objects.filter(estado='fallido').count()
        if atrasados or fallidos:
            raise CommandError(f'Cola: {atrasados} trabajos atrasados y {fallidos} fallidos. Revisar trabajador e integraciones.')
        self.stdout.write('Sin trabajos atrasados ni fallidos.')
