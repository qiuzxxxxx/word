"""初始化默认成就数据"""
from django.db import migrations


def create_default_achievements(apps, schema_editor):
    Achievement = apps.get_model('words', 'Achievement')
    
    defaults = [
        # 连续打卡系列
        {'key': 'streak_3', 'name': '三日坚持', 'description': '连续打卡 3 天', 'icon': 'ph-fire',
         'category': 'streak', 'threshold': 3, 'condition_type': 'streak_days', 'sort_order': 1},
        {'key': 'streak_7', 'name': '一周达人', 'description': '连续打卡 7 天', 'icon': 'ph-fire',
         'category': 'streak', 'threshold': 7, 'condition_type': 'streak_days', 'sort_order': 2},
        {'key': 'streak_30', 'name': '月度之星', 'description': '连续打卡 30 天', 'icon': 'ph-trophy',
         'category': 'streak', 'threshold': 30, 'condition_type': 'streak_days', 'sort_order': 3},
        {'key': 'streak_100', 'name': '百日精进', 'description': '连续打卡 100 天', 'icon': 'ph-crown',
         'category': 'streak', 'threshold': 100, 'condition_type': 'streak_days', 'sort_order': 4},
        
        # 单词数量系列
        {'key': 'words_100', 'name': '初窥门径', 'description': '掌握 100 个单词', 'icon': 'ph-book-open',
         'category': 'words', 'threshold': 100, 'condition_type': 'words_mastered', 'sort_order': 10},
        {'key': 'words_500', 'name': '学有所成', 'description': '掌握 500 个单词', 'icon': 'ph-book-open',
         'category': 'words', 'threshold': 500, 'condition_type': 'words_mastered', 'sort_order': 11},
        {'key': 'words_1000', 'name': '词汇达人', 'description': '掌握 1000 个单词', 'icon': 'ph-star',
         'category': 'words', 'threshold': 1000, 'condition_type': 'words_mastered', 'sort_order': 12},
        {'key': 'words_2000', 'name': '考研必备', 'description': '掌握 2000 个单词', 'icon': 'ph-graduation-cap',
         'category': 'words', 'threshold': 2000, 'condition_type': 'words_mastered', 'sort_order': 13},
        
        # 正确率系列
        {'key': 'accuracy_80', 'name': '准确射手', 'description': '单次学习正确率达到 80%', 'icon': 'ph-target',
         'category': 'accuracy', 'threshold': 80, 'condition_type': 'accuracy_percent', 'sort_order': 20},
        {'key': 'accuracy_90', 'name': '神准无比', 'description': '单次学习正确率达到 90%', 'icon': 'ph-target',
         'category': 'accuracy', 'threshold': 90, 'condition_type': 'accuracy_percent', 'sort_order': 21},
        {'key': 'accuracy_100', 'name': '完美无瑕', 'description': '单次学习全部答对（至少 20 词）', 'icon': 'ph-diamond',
         'category': 'accuracy', 'threshold': 100, 'condition_type': 'accuracy_perfect', 'sort_order': 22},
        
        # 学习时长系列
        {'key': 'time_60', 'name': '初学时', 'description': '累计学习 60 分钟', 'icon': 'ph-lightning',
         'category': 'time', 'threshold': 60, 'condition_type': 'study_minutes', 'sort_order': 30},
        {'key': 'time_300', 'name': '勤学不辍', 'description': '累计学习 5 小时', 'icon': 'ph-lightning',
         'category': 'time', 'threshold': 300, 'condition_type': 'study_minutes', 'sort_order': 31},
        {'key': 'time_600', 'name': '学海无涯', 'description': '累计学习 10 小时', 'icon': 'ph-medal',
         'category': 'time', 'threshold': 600, 'condition_type': 'study_minutes', 'sort_order': 32},
    ]
    
    for data in defaults:
        Achievement.objects.update_or_create(key=data['key'], defaults=data)


def remove_default_achievements(apps, schema_editor):
    Achievement = apps.get_model('words', 'Achievement')
    Achievement.objects.filter(
        key__in=[
            'streak_3', 'streak_7', 'streak_30', 'streak_100',
            'words_100', 'words_500', 'words_1000', 'words_2000',
            'accuracy_80', 'accuracy_90', 'accuracy_100',
            'time_60', 'time_300', 'time_600',
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('words', '0062_add_achievement_models'),
    ]

    operations = [
        migrations.RunPython(create_default_achievements, remove_default_achievements),
    ]
