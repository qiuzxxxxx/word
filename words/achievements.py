"""学习成就系统服务

提供成就检查、解锁、查询等功能。
"""

import logging
from django.utils import timezone
from django.db.models import Sum, Count, Q, F
from datetime import timedelta

logger = logging.getLogger(__name__)


# 预定义的成就（可通过 admin 或 data migration 创建）
DEFAULT_ACHIEVEMENTS = [
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


def init_default_achievements():
    """初始化默认成就（幂等操作）"""
    from words.models import Achievement
    created_count = 0
    for data in DEFAULT_ACHIEVEMENTS:
        _, created = Achievement.objects.update_or_create(
            key=data['key'],
            defaults=data
        )
        if created:
            created_count += 1
    return created_count


def check_and_unlock_achievements(user, progress=None):
    """检查并解锁用户的新成就
    
    Args:
        user: Django User 对象
        progress: StudyProgress 对象（可选，用于针对特定单词的检查）
    
    Returns:
        list: 新解锁的 Achievement 对象列表
    """
    from words.models import Achievement, UserAchievement, StudyProgress, DailyCheckIn, StudyRecord
    
    unlocked = []
    
    # 获取用户已解锁的成就 key
    unlocked_keys = set(
        UserAchievement.objects.filter(user=user)
        .values_list('achievement__key', flat=True)
    )
    
    # 获取所有可解锁的成就
    achievements = Achievement.objects.filter(is_active=True).exclude(key__in=unlocked_keys)
    
    # 计算用户统计数据
    stats = _calculate_user_stats(user)
    
    for ach in achievements:
        if _check_achievement_condition(ach, stats):
            UserAchievement.objects.create(user=user, achievement=ach)
            unlocked.append(ach)
            logger.info(f'User {user} unlocked achievement: {ach.key}')
    
    return unlocked


def _calculate_user_stats(user):
    """计算用户统计数据"""
    from words.models import StudyProgress, DailyCheckIn, StudyRecord
    
    # 连续打卡天数
    streak = _calculate_streak()
    
    # 掌握单词数
    words_mastered = StudyProgress.objects.filter(status='mastered').count()
    
    # 累计学习时长（分钟）
    total_minutes = DailyCheckIn.objects.aggregate(
        total=Sum('study_duration')
    )['total'] or 0
    total_minutes //= 60  # 秒转分钟
    
    # 最近一次学习的正确率
    latest_session = StudyRecord.objects.filter(
        created_at__gte=timezone.now() - timedelta(hours=24)
    ).aggregate(
        total=Count('id'),
        correct=Count('id', filter=Q(action__in=['known', 'spelling_ok', 'meaning_ok']))
    )
    if latest_session['total'] and latest_session['total'] >= 20:
        accuracy = round(latest_session['correct'] / latest_session['total'] * 100)
    else:
        accuracy = 0
    
    return {
        'streak_days': streak,
        'words_mastered': words_mastered,
        'study_minutes': total_minutes,
        'accuracy_percent': accuracy,
        'accuracy_perfect': accuracy == 100 and latest_session['total'] >= 20,
    }


def _calculate_streak():
    """计算连续打卡天数"""
    from words.models import DailyCheckIn
    
    today = timezone.localdate()
    streak = 0
    check_date = today
    
    while True:
        if DailyCheckIn.objects.filter(date=check_date, is_checked=True).exists():
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break
    
    return streak


def _check_achievement_condition(achievement, stats):
    """检查成就是否满足解锁条件"""
    condition = achievement.condition_type
    threshold = achievement.threshold
    
    if condition == 'streak_days':
        return stats.get('streak_days', 0) >= threshold
    elif condition == 'words_mastered':
        return stats.get('words_mastered', 0) >= threshold
    elif condition == 'study_minutes':
        return stats.get('study_minutes', 0) >= threshold
    elif condition == 'accuracy_percent':
        return stats.get('accuracy_percent', 0) >= threshold
    elif condition == 'accuracy_perfect':
        return stats.get('accuracy_perfect', False)
    
    return False


def get_user_achievements(user):
    """获取用户的所有成就（已解锁 + 未解锁）"""
    from words.models import Achievement, UserAchievement
    
    unlocked_map = {
        ua.achievement_id: ua.unlocked_at
        for ua in UserAchievement.objects.filter(user=user).select_related('achievement')
    }
    
    achievements = []
    for ach in Achievement.objects.filter(is_active=True):
        achievements.append({
            'achievement': ach,
            'unlocked': ach.id in unlocked_map,
            'unlocked_at': unlocked_map.get(ach.id),
        })
    
    # 按已解锁优先，然后按排序
    achievements.sort(key=lambda x: (not x['unlocked'], x['achievement'].sort_order))
    
    return achievements


def get_recent_achievements(user, limit=5):
    """获取用户最近解锁的成就"""
    from words.models import UserAchievement
    
    return list(
        UserAchievement.objects.filter(user=user)
        .select_related('achievement')
        .order_by('-unlocked_at')[:limit]
    )
