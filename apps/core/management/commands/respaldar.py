"""Copia consistente de SQLite y documentos, con manifiesto verificable."""
import hashlib
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Genera un respaldo SQLite + media. No incluye variables de entorno ni secretos externos.'

    def add_arguments(self, parser):
        parser.add_argument('--destino', required=True)

    def handle(self, *args, **options):
        db = settings.DATABASES['default']
        if not db['ENGINE'].endswith('sqlite3'):
            raise CommandError('Para PostgreSQL utiliza pg_dump y respaldo privado de MEDIA_ROOT; este comando solo soporta SQLite.')
        destino = Path(options['destino']).resolve()
        for publico in (settings.STATIC_ROOT, settings.MEDIA_ROOT, settings.BASE_DIR / 'static'):
            if destino.is_relative_to(Path(publico).resolve()):
                raise CommandError('El respaldo no puede guardarse dentro de archivos servidos por HTTP.')
        destino.mkdir(parents=True, exist_ok=True)
        nombre = 'erp-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.zip'
        final = destino / nombre
        with tempfile.TemporaryDirectory() as temporal:
            copia = Path(temporal) / 'database.sqlite3'
            with closing(sqlite3.connect(Path(db['NAME']).resolve().as_uri() + '?mode=ro', uri=True)) as origen, closing(sqlite3.connect(copia)) as salida:
                origen.backup(salida)
                if salida.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise CommandError('La copia no superó la verificación SQLite.')
            archivos = [(copia, 'database.sqlite3')]
            media = Path(settings.MEDIA_ROOT)
            if media.exists():
                archivos.extend((p, 'media/' + p.relative_to(media).as_posix()) for p in media.rglob('*') if p.is_file() and not p.is_symlink())
            manifiesto = {'version': 1, 'creado': datetime.now(timezone.utc).isoformat(), 'sha256': {}}
            with zipfile.ZipFile(final, 'x', compression=zipfile.ZIP_DEFLATED) as z:
                for ruta, miembro in archivos:
                    datos = ruta.read_bytes()
                    z.writestr(miembro, datos)
                    manifiesto['sha256'][miembro] = hashlib.sha256(datos).hexdigest()
                z.writestr('manifest.json', json.dumps(manifiesto))
        self.stdout.write(str(final))
