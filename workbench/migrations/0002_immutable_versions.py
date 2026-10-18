from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('workbench', '0001_initial')]
    operations = [migrations.RunSQL(
        "CREATE TRIGGER immutable_principle_version BEFORE UPDATE ON workbench_principleversion BEGIN SELECT RAISE(ABORT, 'PrincipleVersion is immutable'); END;",
        'DROP TRIGGER IF EXISTS immutable_principle_version;'
    )]
