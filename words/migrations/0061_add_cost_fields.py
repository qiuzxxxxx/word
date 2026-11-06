"""为 AICallLog 添加成本追踪字段"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('words', '0060_alter_aimodel_api_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='aicalllog',
            name='input_tokens',
            field=models.IntegerField(default=0, verbose_name='输入 tokens'),
        ),
        migrations.AddField(
            model_name='aicalllog',
            name='output_tokens',
            field=models.IntegerField(default=0, verbose_name='输出 tokens'),
        ),
        migrations.AddField(
            model_name='aicalllog',
            name='cost_usd',
            field=models.DecimalField(decimal_places=6, default=0, max_digits=10, verbose_name='成本（美元）'),
        ),
    ]
