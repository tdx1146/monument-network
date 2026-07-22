#!/usr/bin/env python3
"""批量对未评分的丰碑进行三轴评分"""

import json
import os
import sys
sys.path.insert(0, '/vol2/1000/AI专用/丰碑网络/code')

from core.xuanjian_pipe import XuanjianPipe


def score_monument(filepath: str, xuanjian: XuanjianPipe) -> dict:
    """对单个丰碑评分"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 如果已有评分，跳过
    if 'monument_score' in data:
        return None

    # 提取正文
    content = data.get('content', {})
    text = content.get('正文', '')

    if not text or len(text) < 10:
        return None

    # 三轴评分
    three_axis = xuanjian.compute_three_axis(text[:500])  # 限制长度
    confidence = xuanjian.compute_confidence(
        three_axis['time_binding'],
        three_axis['transferability'],
        three_axis['abstraction_level']
    )

    # 计算monument_score（与confidence相同公式）
    monument_score = (
        (1 - three_axis['time_binding']) * 0.3 +
        three_axis['transferability'] * 0.4 +
        three_axis['abstraction_level'] * 0.3
    )

    # 写入评分字段
    data['monument_score'] = round(monument_score, 3)
    data['time_binding'] = round(three_axis['time_binding'], 3)
    data['transferability'] = round(three_axis['transferability'], 3)
    data['abstraction_level'] = round(three_axis['abstraction_level'], 3)
    data['confidence'] = round(confidence, 3)

    # 保存
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        'file': os.path.basename(filepath),
        'title': data.get('title', 'unknown')[:50],
        'monument_score': data['monument_score'],
        'time_binding': data['time_binding'],
        'transferability': data['transferability'],
        'abstraction_level': data['abstraction_level'],
    }


def main():
    xuanjian = XuanjianPipe()
    candidates_dir = '/vol2/1000/AI专用/丰碑网络/candidates'

    scored = []
    for filename in sorted(os.listdir(candidates_dir)):
        if filename.endswith('.json'):
            filepath = os.path.join(candidates_dir, filename)
            result = score_monument(filepath, xuanjian)
            if result:
                scored.append(result)
                print(f"✓ {result['title'][:40]:40s} → {result['monument_score']:.3f} "
                      f"(tb={result['time_binding']:.2f} tr={result['transferability']:.2f} "
                      f"ab={result['abstraction_level']:.2f})")

    # 输出汇总
    print(f'\n=== 评分完成 ===')
    print(f'已评分: {len(scored)} 座丰碑')
    scored.sort(key=lambda x: x['monument_score'], reverse=True)
    print('\n前10名:')
    for i, s in enumerate(scored[:10], 1):
        print(f'{i}. {s["title"][:40]} - {s["monument_score"]:.3f}')

    # 晋升候选(>=0.8)
    promoted = [s for s in scored if s['monument_score'] >= 0.8]
    print(f'\n晋升候选(≥0.8): {len(promoted)} 座')


if __name__ == '__main__':
    main()
