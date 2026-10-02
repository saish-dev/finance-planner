from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('planner', '0005_retirementaccount_monthly_cap'),
    ]

    operations = [
        migrations.RenameField(
            model_name='expense',
            old_name='monthly_amount',
            new_name='amount',
        ),
        migrations.AlterField(
            model_name='expense',
            name='amount',
            field=models.DecimalField(decimal_places=2, help_text="What this costs each time it falls due -- not divided down to a monthly figure.", max_digits=14, verbose_name='Amount'),
        ),
        migrations.AddField(
            model_name='expense',
            name='frequency',
            field=models.CharField(choices=[('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('half_yearly', 'Half-Yearly'), ('yearly', 'Yearly')], default='monthly', max_length=20),
        ),
        migrations.AddField(
            model_name='expense',
            name='due_month',
            field=models.IntegerField(choices=[(1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'), (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'), (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')], default=1, help_text='Month this expense is charged. Quarterly and half-yearly expenses repeat every 3 or 6 months from here. Ignored for monthly expenses.', verbose_name='Due in'),
        ),
    ]
