from django.core.management.base import BaseCommand, CommandError

from core import openedx


class Command(BaseCommand):
    help = "Checks that the service account can call the LMS and Studio APIs."

    def handle(self, *args, **options):
        try:
            me = openedx.lms("GET", "/api/user/v1/me")
            self.stdout.write(f"LMS: authenticated as {me['username']}")
            runs = openedx.studio("GET", "/api/v1/course_runs/")
            self.stdout.write(f"Studio: {runs.get('count', len(runs.get('results', [])))} course run(s) visible")
        except openedx.OpenEdxError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Open edX connection OK"))
