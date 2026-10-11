from django.contrib.staticfiles.management.commands.runserver import Command as StaticRunserverCommand


class Command(StaticRunserverCommand):
    help = 'Run the local hackathon HTTP preview with WSGI and development static file serving.'
