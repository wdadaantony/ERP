web: gunicorn config.wsgi --log-file -
worker: python manage.py procesar_cola --continuo
release: python manage.py migrate
