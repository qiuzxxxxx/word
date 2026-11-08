"""明代官阶主题的累计背诵等级。"""


# 品级和官职参考明代职官；词数门槛按随项目分发的 1,866 词词库设定，
# 最高门槛保留在 1,800 词，达到接近全库掌握时授予最高档。
RECITATION_LEVELS = (
    {
        'threshold': 0,
        'name': '布衣',
        'grade': '未入仕',
        'basis': '起步称号，不属于明代正式官阶',
    },
    {
        'threshold': 50,
        'name': '主簿',
        'grade': '正九品',
        'basis': '县级佐官；通常每县一员，员额有添革',
    },
    {
        'threshold': 125,
        'name': '县丞',
        'grade': '正八品',
        'basis': '县级佐官；通常每县一员，员额有添革',
    },
    {
        'threshold': 225,
        'name': '知县',
        'grade': '正七品',
        'basis': '每县一员；《明史》记载当时全国县数约一千七百七十一',
    },
    {
        'threshold': 350,
        'name': '通判',
        'grade': '正六品',
        'basis': '府级佐官，员额不固定',
    },
    {
        'threshold': 500,
        'name': '同知',
        'grade': '正五品',
        'basis': '府级佐官，随府设置',
    },
    {
        'threshold': 675,
        'name': '知府',
        'grade': '正四品',
        'basis': '每府一员',
    },
    {
        'threshold': 875,
        'name': '侍郎',
        'grade': '正三品',
        'basis': '六部佐官，员额随各部制度而异',
    },
    {
        'threshold': 1100,
        'name': '布政使',
        'grade': '从二品',
        'basis': '各布政使司左、右各设一员',
    },
    {
        'threshold': 1350,
        'name': '尚书',
        'grade': '正二品',
        'basis': '六部各设尚书一员',
    },
    {
        'threshold': 1600,
        'name': '三孤',
        'grade': '从一品',
        'basis': '少师、少傅、少保；无固定员额',
    },
    {
        'threshold': 1800,
        'name': '三公',
        'grade': '正一品',
        'basis': '太师、太傅、太保；无固定员额，授予较少',
    },
)


def get_recitation_level(mastered_count):
    """根据当前已掌握词数返回等级、晋级进度和完整等级表。

    词数来自 StudyProgress.status == 'mastered' 的统计，不写回单词状态。
    """
    try:
        count = max(0, int(mastered_count or 0))
    except (TypeError, ValueError):
        count = 0

    current_index = 0
    for index, level in enumerate(RECITATION_LEVELS):
        if count < level['threshold']:
            break
        current_index = index

    current = dict(RECITATION_LEVELS[current_index])
    next_level = (
        dict(RECITATION_LEVELS[current_index + 1])
        if current_index + 1 < len(RECITATION_LEVELS)
        else None
    )

    if next_level:
        span = next_level['threshold'] - current['threshold']
        progress_percent = round(
            (count - current['threshold']) * 100 / span
        )
        words_to_next = next_level['threshold'] - count
    else:
        progress_percent = 100
        words_to_next = 0

    levels = []
    for index, level in enumerate(RECITATION_LEVELS):
        next_threshold = (
            RECITATION_LEVELS[index + 1]['threshold']
            if index + 1 < len(RECITATION_LEVELS)
            else None
        )
        start = f"{level['threshold']:,}"
        if next_threshold is None:
            range_label = f'{start} 及以上'
        else:
            end = f'{next_threshold - 1:,}'
            range_label = f'{start}–{end}'
        levels.append({
            **level,
            'range_label': range_label,
            'is_current': index == current_index,
        })

    return {
        'mastered_count': count,
        'current': current,
        'next': next_level,
        'progress_percent': max(0, min(100, progress_percent)),
        'words_to_next': words_to_next,
        'levels': levels,
    }
