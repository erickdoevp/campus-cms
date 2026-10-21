from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from core.permissions import CONTADORES


class Command(BaseCommand):
    help = (
        "Gives (or with --remove, takes away) the Contador role to an Open edX username. "
        "Works before the user's first login: the account is linked when they log in."
    )

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--remove", action="store_true")

    def handle(self, username, remove, **options):
        group = Group.objects.get(name=CONTADORES)
        User = get_user_model()
        if remove:
            user = User.objects.filter(username=username).first()
            if user is not None:
                user.groups.remove(group)
            self.stdout.write(self.style.SUCCESS(f"{username} ya no es contador"))
            return
        user = User.objects.filter(username=username).first()
        if user is None:
            user = User(username=username)
            user.set_unusable_password()
            user.save()
        user.groups.add(group)
        self.stdout.write(self.style.SUCCESS(f"{username} ahora es contador"))
