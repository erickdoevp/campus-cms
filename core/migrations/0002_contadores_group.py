from django.db import migrations

CONTADORES = "Contadores"


def create_group(apps, schema_editor):
    apps.get_model("auth", "Group").objects.get_or_create(name=CONTADORES)


def delete_group(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name=CONTADORES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("core", "0001_initial"),
    ]

    operations = [migrations.RunPython(create_group, delete_group)]
