import hashlib
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Verifica hashes y SQLite. Opcionalmente restaura en un directorio NUEVO, nunca sobre datos existentes.'

    def add_arguments(self, parser):
        parser.add_argument('archivo')
        parser.add_argument('--restaurar-en')

    def handle(self, *args, **options):
        with zipfile.ZipFile(options['archivo']) as z:
            nombres = z.namelist()
            if len(nombres) != len(set(nombres)):
                raise CommandError('El archivo contiene entradas duplicadas.')
            for nombre in nombres:
                p = PurePosixPath(nombre)
                if p.is_absolute() or '..' in p.parts or ':' in nombre or '\\' in nombre:
                    raise CommandError('Ruta insegura en el respaldo.')
            m = json.loads(z.read('manifest.json'))
            if set(nombres) != set(m['sha256']) | {'manifest.json'}:
                raise CommandError('El manifiesto no corresponde al contenido.')
            for nombre, huella in m['sha256'].items():
                if hashlib.sha256(z.read(nombre)).hexdigest() != huella:
                    raise CommandError(f'Integridad incorrecta: {nombre}')
            with tempfile.TemporaryDirectory() as temp:
                db = Path(temp) / 'database.sqlite3'
                db.write_bytes(z.read('database.sqlite3'))
                with closing(sqlite3.connect(db)) as conexion:
                    if conexion.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise CommandError('SQLite no supera integrity_check.')
            if options['restaurar_en']:
                destino = Path(options['restaurar_en']).resolve()
                if destino.exists():
                    raise CommandError('La restauración exige un directorio que todavía no exista.')
                destino.mkdir(parents=True)
                for nombre in m['sha256']:
                    ruta = destino / nombre
                    ruta.parent.mkdir(parents=True, exist_ok=True)
                    ruta.write_bytes(z.read(nombre))
        self.stdout.write('Respaldo verificado: hashes y base de datos íntegros.')
