#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    is_runserver = sys.argv[1:2] == ['runserver']
    specified_settings = next((argument.split('=', 1)[1] for argument in sys.argv if argument.startswith('--settings=')), None)
    if '--settings' in sys.argv and sys.argv.index('--settings') + 1 < len(sys.argv):
        specified_settings = sys.argv[sys.argv.index('--settings') + 1]
    local_settings = 'vocab_project.local_web_settings'
    settings_module = os.environ.setdefault('DJANGO_SETTINGS_MODULE', local_settings if is_runserver else 'vocab_project.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    if is_runserver and (specified_settings or settings_module) == local_settings and not {'--help', '-h'}.intersection(sys.argv):
        if os.environ.get('RUN_MAIN') != 'true':
            import django
            from django.core.management import call_command

            os.environ['DJANGO_SETTINGS_MODULE'] = local_settings
            django.setup()
            call_command('prepare_local_web')
        if '--insecure' not in sys.argv:
            sys.argv.append('--insecure')
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
